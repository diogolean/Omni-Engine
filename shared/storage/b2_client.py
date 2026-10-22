# -*- coding: utf-8 -*-
"""
Backblaze B2 upload client — S3-compatible via boto3.

Ported from the working implementation in
  @ MEDIAUPSCALE_FACTORY/scheduler_module.py
with the same connection contract (SigV4, path-style addressing).

Credentials are read from the project .env:
  B2_KEY_ID            — application key ID
  B2_APPLICATION_KEY   — application key secret
  B2_BUCKET_NAME       — target bucket  (default: MediaupscaleStorage)
  B2_ENDPOINT_URL      — regional S3 endpoint

Usage
-----
    from shared.storage.b2_client import B2VideoUploader

    uploader = B2VideoUploader()
    b2_url = uploader.upload(Path("clips/my_reel.mp4"))
    # → "https://MediaupscaleStorage.s3.us-east-005.backblazeb2.com/my_reel.mp4"
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from shared.storage.b2_budget import (
    B2DailyCapError,
    B2StorageCapError,
    allow_video_download,
    cached_listing,
    cached_object_size,
    cached_text,
    call_with_cap_backoff,
    charge,
    choose_publish_key,
    client_token,
    invalidate_listings,
    invalidate_text,
    is_transaction_cap,
    is_video_key,
    note_download_bytes,
    store_listing,
    store_text,
)

logger = logging.getLogger(__name__)

__all__ = [
    "B2DailyCapError",
    "B2StorageCapError",
    "B2VideoUploader",
    "choose_publish_key",
    "download_object",
    "list_object_keys",
    "media_bucket_name",
    "read_object_outcome",
    "read_object_text",
    "resolve_b2_bucket_name",
    "upload_object",
]


_B2_ENDPOINT_URL = "https://s3.us-east-005.backblazeb2.com"
_B2_BUCKET_FALLBACK = "MediaupscaleStorage"


def _reload_dotenv_if_needed() -> None:
    """Bind project .env into os.environ without clobbering live CLI overrides."""
    if (os.getenv("B2_BUCKET_NAME") or "").strip() and (
        os.getenv("B2_KEY_ID") or ""
    ).strip():
        return
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.is_file():
            load_dotenv(env_path, override=False, encoding="utf-8-sig")
    except Exception:  # noqa: BLE001
        pass


def resolve_b2_bucket_name() -> str:
    """Read ``B2_BUCKET_NAME`` at call time — never cache it at import."""
    _reload_dotenv_if_needed()
    bucket_name = (os.getenv("B2_BUCKET_NAME") or "").strip()
    if not bucket_name:
        raise ValueError(
            "CRITICAL: B2_BUCKET_NAME is unresolved or empty at execution time. "
            "Check .env loading sequence."
        )
    return bucket_name


def media_bucket_name() -> str:
    """Bucket for Pinterest image listing.

    Uploads still require an explicit ``B2_BUCKET_NAME`` via
    :func:`resolve_b2_bucket_name`. Listing defaults to MediaupscaleStorage
    when that variable is empty.
    """
    _reload_dotenv_if_needed()
    bucket_name = (os.getenv("B2_BUCKET_NAME") or "").strip()
    return bucket_name or _B2_BUCKET_FALLBACK


def list_object_keys(prefix: str, *, client=None, bucket: str | None = None) -> list[str]:
    """Return object keys under *prefix*. Does not download object bodies.

    A prefix is listed at most once per cache TTL (six hours by default).
    """
    bucket_name = (bucket or "").strip() or media_bucket_name()
    token = client_token(client)
    cached = cached_listing(token, bucket_name, prefix or "")
    if cached is not None:
        return cached
    s3 = client if client is not None else _get_b2_resource().meta.client
    keys: list[str] = []
    sizes: dict[str, int] = {}
    page_token: str | None = None
    while True:
        kwargs: dict = {"Bucket": bucket_name, "Prefix": prefix or ""}
        if page_token:
            kwargs["ContinuationToken"] = page_token
        charge(class_c=1)
        page = call_with_cap_backoff(
            lambda kwargs=kwargs: s3.list_objects_v2(**kwargs),
            what=f"list {prefix or '/'}",
        )
        for item in page.get("Contents") or []:
            key = str(item.get("Key") or "")
            if key and not key.endswith("/"):
                keys.append(key)
                try:
                    sizes[key] = int(item.get("Size") or 0)
                except (TypeError, ValueError):
                    sizes[key] = 0
        if not page.get("IsTruncated"):
            break
        page_token = page.get("NextContinuationToken")
        if not page_token:
            break
    store_listing(token, bucket_name, prefix or "", keys, sizes)
    return keys


def download_object(
    key: str,
    dest: Path,
    *,
    client=None,
    bucket: str | None = None,
    allow_video: bool | None = None,
) -> Path:
    """Download one object to *dest*. Never deletes the B2 object.

    Video keys are refused unless ``allow_video`` or ``B2_ALLOW_VIDEO_DOWNLOAD``
    is set. Publish the image instead of pulling a multi-gigabyte clip.
    """
    video_ok = allow_video_download() if allow_video is None else allow_video
    if is_video_key(key) and not video_ok:
        raise B2DailyCapError(
            f"Refusing video download of {key}. Publish the image, or set "
            "B2_ALLOW_VIDEO_DOWNLOAD=true for this one file."
        )
    bucket_name = (bucket or "").strip() or media_bucket_name()
    known = cached_object_size(client_token(client), bucket_name, key)
    if known and known > 1:
        charge(class_b=1, download_bytes=known)
        reserved = known
    else:
        charge(class_b=1, download_bytes=1)
        reserved = 1
    s3 = client if client is not None else _get_b2_resource().meta.client
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        call_with_cap_backoff(
            lambda: s3.download_file(bucket_name, key, str(dest)),
            what=f"download {key}",
        )
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    size = dest.stat().st_size if dest.is_file() else 0
    # Replace the reservation with the bytes that actually arrived.
    if size > reserved:
        note_download_bytes(size - reserved)
    return dest


def upload_object(
    key: str,
    source: "bytes | Path",
    *,
    client=None,
    bucket: str | None = None,
    content_type: str = "application/octet-stream",
) -> None:
    """Upload one object. Does not delete anything already in the bucket."""
    s3 = client if client is not None else _get_b2_resource().meta.client
    bucket_name = (bucket or "").strip() or media_bucket_name()
    body = source.read_bytes() if isinstance(source, Path) else bytes(source)

    def _put() -> None:
        s3.put_object(Bucket=bucket_name, Key=key, Body=body, ContentType=content_type)

    call_with_cap_backoff(_put, what=f"upload {key}")
    token = client_token(client)
    invalidate_listings(token, bucket_name, key)
    if content_type.startswith("text/") or content_type in {
        "application/json",
        "application/x-ndjson",
    }:
        store_text(token, bucket_name, key, body.decode("utf-8", errors="replace"))
    else:
        invalidate_text(token, bucket_name, key)


def read_object_text(key: str, *, client=None, bucket: str | None = None) -> str | None:
    """Read one small object (a sidecar or ledger) as text. Missing keys return None.

    A hit within the listing TTL does not call B2 again.
    """
    bucket_name = (bucket or "").strip() or media_bucket_name()
    token = client_token(client)
    cached = cached_text(token, bucket_name, key)
    if cached is not None:
        return cached
    s3 = client if client is not None else _get_b2_resource().meta.client
    try:
        charge(class_b=1, download_bytes=1)
        obj = call_with_cap_backoff(
            lambda: s3.get_object(Bucket=bucket_name, Key=key),
            what=f"read {key}",
        )
    except B2DailyCapError:
        raise
    except Exception as exc:  # noqa: BLE001 — missing sidecar, not a credential dump
        logger.info("B2 object not read (%s): %s", type(exc).__name__, key)
        return None
    body = obj["Body"].read()
    if isinstance(body, bytes):
        text = body.decode("utf-8")
    else:
        text = str(body)
    if len(text.encode("utf-8")) > 1:
        note_download_bytes(len(text.encode("utf-8")) - 1)
    store_text(token, bucket_name, key, text)
    return text


def read_object_outcome(
    key: str, *, client=None, bucket: str | None = None
) -> tuple[str | None, str]:
    """Read one small object and say whether the caller can trust the result.

    ``ok`` means ``text`` is the object body, which may be empty. ``missing``
    means the key is not in the bucket. ``unreachable`` means credentials,
    the network, or the daily cap stopped the read. A daily cap does not
    raise here. ``read_object_text`` still raises ``B2DailyCapError``.
    """
    bucket_name = (bucket or "").strip() or media_bucket_name()
    token = client_token(client)
    cached = cached_text(token, bucket_name, key)
    if cached is not None:
        return cached, "ok"
    try:
        s3 = client if client is not None else _get_b2_resource().meta.client
        charge(class_b=1, download_bytes=1)
        obj = call_with_cap_backoff(
            lambda: s3.get_object(Bucket=bucket_name, Key=key),
            what=f"read {key}",
        )
    except B2DailyCapError:
        return None, "unreachable"
    except Exception as exc:  # noqa: BLE001 — missing key or transport, not a secret
        logger.info("B2 object not read (%s): %s", type(exc).__name__, key)
        return None, _object_read_status(exc)
    body = obj["Body"].read()
    if isinstance(body, bytes):
        text = body.decode("utf-8")
    else:
        text = str(body)
    try:
        if len(text.encode("utf-8")) > 1:
            note_download_bytes(len(text.encode("utf-8")) - 1)
    except B2DailyCapError:
        logger.info("B2 download cap reached after reading %s", key)
    store_text(token, bucket_name, key, text)
    return text, "ok"


def _object_read_status(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    code = ""
    if isinstance(response, dict):
        err = response.get("Error") or {}
        if isinstance(err, dict):
            code = str(err.get("Code") or "")
    if code in {"NoSuchKey", "404", "NotFound", "NoSuchBucket"}:
        return "missing"
    return "unreachable"


def _is_storage_cap_error(exc: BaseException) -> bool:
    """True for B2/S3 AccessDenied / cap / quota style failures."""
    text = str(exc or "").lower()
    if "accessdenied" in text or "access denied" in text:
        return True
    if "storage" in text and ("cap" in text or "quota" in text or "limit" in text):
        return True
    try:
        code = getattr(exc, "response", {}) or {}
        err = code.get("Error") or {}
        ec = str(err.get("Code") or "")
        if ec in ("AccessDenied", "403", "QuotaExceeded"):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False

# Upload resilience: 30-second connect/read timeout, up to 2 retries (3 total
# attempts) with a short backoff between attempts. Callers (main.py) already
# wrap ``upload()`` in a try/except and log-and-continue on total failure —
# this budget just maximizes the odds a transient network hiccup self-heals
# before that final fallback kicks in.
_B2_TIMEOUT_S: float = 30.0
_B2_MAX_ATTEMPTS: int = 3  # 1 initial attempt + 2 retries
_B2_RETRY_BACKOFF_S: float = 2.0


def _get_b2_resource():
    """
    Create a boto3 S3 resource connected to Backblaze B2.

    - signature_version='s3v4'      : mandatory for B2
    - addressing_style='path'       : forces path-style URLs (B2 rejects
                                      virtual-hosted style on multipart uploads)
    - endpoint_url hardcoded        : immune to .env whitespace/BOM issues
    """
    try:
        import boto3  # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required for B2 uploads.  Run: pip install boto3"
        ) from exc

    _reload_dotenv_if_needed()
    key_id = (os.getenv("B2_KEY_ID") or "").strip()
    app_key = (os.getenv("B2_APPLICATION_KEY") or "").strip()
    if not key_id or not app_key:
        raise RuntimeError(
            "B2_KEY_ID and B2_APPLICATION_KEY must be set in .env "
            "to use the B2 uploader."
        )

    endpoint = (os.getenv("B2_ENDPOINT_URL") or _B2_ENDPOINT_URL).strip()

    return boto3.resource(
        service_name="s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
        region_name="us-east-005",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=_B2_TIMEOUT_S,
            read_timeout=_B2_TIMEOUT_S,
        ),
    )


class B2VideoUploader:
    """
    Thin wrapper around boto3 for uploading video clips to Backblaze B2.

    Parameters
    ----------
    bucket_name : str
        B2 bucket name.  Defaults to the B2_BUCKET_NAME env var or
        "MediaupscaleStorage".
    """

    def __init__(self, bucket_name: str | None = None) -> None:
        # Do not resolve credentials or bucket at construction — ``.env`` may
        # not be bound yet if this module was imported during Phase 0.
        self._bucket_override = (bucket_name or "").strip() or None
        self._b2 = None

    def _resource(self):
        if self._b2 is None:
            self._b2 = _get_b2_resource()
        return self._b2

    def _bucket(self) -> str:
        if self._bucket_override:
            return self._bucket_override
        return resolve_b2_bucket_name()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def upload(self, local_path: Path | str, *, content_type: str = "video/mp4") -> str:
        """
        Upload *local_path* to the bucket and return its public HTTPS URL.

        The object key is always the bare filename (no folder nesting) so
        the public URL is predictable:
            https://<bucket>.s3.us-east-005.backblazeb2.com/<filename>

        Returns the public URL regardless of whether the file was freshly
        uploaded or was already present in the bucket.
        """
        path = Path(local_path)
        if not path.is_file():
            raise FileNotFoundError(f"B2 upload source not found: {path}")

        key = path.name
        bucket_name = self._bucket()

        if self._object_exists(key):
            logger.info("[B2] Already uploaded: %s — skipping re-upload.", key)
        else:
            size_mb = path.stat().st_size / (1024 * 1024)
            print(f"[B2] Uploading {key} ({size_mb:.1f} MB) -> {bucket_name} ...")
            logger.info("[B2] Uploading %s (%.1f MB) -> %s", key, size_mb, bucket_name)

            last_exc: Exception | None = None
            for attempt in range(1, _B2_MAX_ATTEMPTS + 1):
                try:
                    call_with_cap_backoff(
                        lambda: self._resource().Object(bucket_name, key).upload_file(
                            str(path),
                            ExtraArgs={"ContentType": content_type},
                        ),
                        what=f"upload {key}",
                    )
                    last_exc = None
                    break
                except B2DailyCapError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    # Storage cap / AccessDenied — do not retry or dump stack traces
                    if _is_storage_cap_error(exc):
                        msg = (
                            f"[B2] Storage cap / AccessDenied for '{key}' — "
                            "skipping B2; using local/ImgBB paths."
                        )
                        logger.warning(msg)
                        print(msg)
                        raise B2StorageCapError(msg) from None
                    if attempt < _B2_MAX_ATTEMPTS:
                        logger.warning(
                            "[B2] Upload attempt %d/%d failed for %s (%s) — retrying in %.1fs…",
                            attempt, _B2_MAX_ATTEMPTS, key, type(exc).__name__,
                            _B2_RETRY_BACKOFF_S,
                        )
                        time.sleep(_B2_RETRY_BACKOFF_S)
                    else:
                        logger.warning(
                            "[B2] Upload failed for %s after %d attempt(s): %s — "
                            "caller will fall back to local path.",
                            key, _B2_MAX_ATTEMPTS, type(exc).__name__,
                        )
            if last_exc is not None:
                if _is_storage_cap_error(last_exc):
                    raise B2StorageCapError(
                        f"[B2] Storage cap / AccessDenied for '{key}'"
                    ) from None
                raise last_exc

            print(f"[B2] Upload complete: {key}")
            logger.info("[B2] Upload complete: %s", key)

        return self.public_url(key)

    @staticmethod
    def public_url(filename: str) -> str:
        """Return the public HTTPS URL for a bare filename (no path prefix)."""
        try:
            bucket = resolve_b2_bucket_name()
        except ValueError:
            bucket = _B2_BUCKET_FALLBACK
        return f"https://{bucket}.s3.us-east-005.backblazeb2.com/{Path(filename).name}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _object_exists(self, key: str) -> bool:
        """
        Check whether *key* already exists in the bucket.

        B2 returns 403 (not 404) for HeadObject on private buckets when the
        object is missing, so we treat both 403 and 404 as "not found".
        """
        try:
            from botocore.exceptions import ClientError  # noqa: F401, PLC0415
        except ImportError:
            return False

        try:
            charge(class_b=1)
            self._resource().Object(self._bucket(), key).load()
            return True
        except B2DailyCapError:
            raise
        except Exception as exc:  # noqa: BLE001
            if is_transaction_cap(exc):
                raise B2DailyCapError(
                    f"B2 daily cap exceeded while checking {key}. Stopping this run."
                ) from exc
            try:
                code = exc.response["Error"]["Code"]   # type: ignore[attr-defined]
                if code in ("404", "NoSuchKey"):
                    return False
                if code in ("403", "AccessDenied"):
                    # Quiet — private buckets often 403 on missing keys
                    logger.debug(
                        "[B2] HeadObject %s for '%s' — treating as not uploaded.",
                        code, key,
                    )
                    return False
            except AttributeError:
                pass
            return False
