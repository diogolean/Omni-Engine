# -*- coding: utf-8 -*-
"""Choose where a publisher reads a library file.

``LIBRARY_SOURCE`` may be ``drive``, ``local``, or ``b2``. When it is unset
and a Drive service-account file is configured, the order is Drive, then the
local tree (including the PC ``G:`` path), then B2. An existing local file is
used as-is so a mapped Drive folder does not spend an API call.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".json": "application/json",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}


def library_source_order() -> tuple[str, ...]:
    """Sources to try when the file is not already on disk."""
    explicit = (os.getenv("LIBRARY_SOURCE") or "").strip().lower()
    if explicit in {"drive", "b2", "local"}:
        return (explicit,)
    if explicit:
        log.info("LIBRARY_SOURCE is not drive, local, or b2; using the default order.")
    from agents.media.drive_source import drive_credentials_present  # noqa: PLC0415

    if drive_credentials_present():
        return ("drive", "local", "b2")
    return ("local", "b2")


def object_key_for_path(path: str | Path, channel: str = "") -> str:
    """Map a local or ``G:\\My Drive\\...\\outputs\\...`` path to an object key."""
    text = str(path or "").replace("\\", "/").strip()
    if not text or ".." in text.split("/"):
        return ""
    lower = text.lower()
    marker = "/outputs/"
    index = lower.rfind(marker)
    if index >= 0:
        relative = text[index + len(marker) :].lstrip("/")
        return relative
    channel_slug = (channel or "").strip().lower()
    relative = text.lstrip("/")
    if channel_slug and relative.lower().startswith(channel_slug + "/"):
        return relative
    if relative.startswith(("library/", "clips/", "assets/")) and channel_slug:
        return f"{channel_slug}/{relative}"
    return ""


def existing_local_file(key: str, channel: str = "") -> Path | None:
    """Return a file that is already on disk for *key*, including the PC G: tree."""
    if not key:
        return None
    channel_slug, relative = _split(key)
    if channel and not channel_slug:
        channel_slug = channel.strip().lower()
    from utils.pipeline_paths import outputs_path, outputs_root  # noqa: PLC0415

    candidates = [
        outputs_root() / key,
        outputs_path() / key,
    ]
    if channel_slug and relative:
        candidates.append(outputs_root() / channel_slug / relative)
        candidates.append(outputs_path(channel_slug) / relative)
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            marker = candidate.resolve()
        except OSError:
            marker = candidate
        if marker in seen:
            continue
        seen.add(marker)
        if candidate.is_file():
            return candidate
    return None


def sync_library_records(
    outputs_dir: Path,
    *,
    prefix: str,
    client=None,
    bucket: str | None = None,
    folder_id: str | None = None,
) -> int:
    """Copy missing ``post_*.json`` records. Images stay remote until publish."""
    library = Path(outputs_dir) / "library"
    library.mkdir(parents=True, exist_ok=True)
    if any(library.glob("post_*.json")):
        return 0
    written = 0
    for source in library_source_order():
        try:
            if source == "local":
                got = _copy_local_records(library, prefix)
            elif source == "drive":
                got = _copy_drive_records(library, prefix, folder_id=folder_id)
            else:
                got = _copy_b2_records(library, prefix, client=client, bucket=bucket)
        except Exception as exc:  # noqa: BLE001
            if _stop_for_source(exc, source):
                raise
            log.info("Library source %s failed (%s).", source, type(exc).__name__)
            if any(library.glob("post_*.json")):
                return written
            continue
        written += got
        if got or any(library.glob("post_*.json")):
            return written
    return written


def materialize_key(
    key: str,
    dest: Path,
    *,
    channel: str,
    client=None,
    bucket: str | None = None,
    folder_id: str | None = None,
    allow_video: bool | None = None,
    hinted: str | Path | None = None,
) -> tuple[Path | None, str]:
    """Return ``(path, source)`` for one object. ``source`` is ``local`` when already on disk."""
    if hinted:
        hinted_path = Path(hinted)
        if hinted_path.is_file():
            return hinted_path, "local"
    local = existing_local_file(key, channel)
    if local is not None:
        return local, "local"
    if not key:
        return None, ""
    for source in library_source_order():
        if source == "local":
            continue
        if source == "drive":
            found = _drive_download(key, dest, folder_id=folder_id)
            if found is not None:
                return found, "drive"
            continue
        found = _b2_download(
            key,
            dest,
            client=client,
            bucket=bucket,
            allow_video=allow_video,
        )
        if found is not None:
            return found, "b2"
    return None, ""


def materialize_publish_file(
    path: str | Path,
    *,
    channel: str,
    dest: Path | None = None,
    client=None,
    bucket: str | None = None,
    folder_id: str | None = None,
    allow_video: bool | None = None,
) -> Path | None:
    """Resolve one publishable file. Downloads only when it is not already local."""
    raw = Path(path)
    if raw.is_file():
        return raw
    key = object_key_for_path(raw, channel)
    if dest is None:
        if key:
            from agents.media.drive_source import download_cache_path  # noqa: PLC0415

            dest = download_cache_path(key)
        else:
            from utils.pipeline_paths import temp_path  # noqa: PLC0415

            dest = temp_path(channel or "publish", "library", raw.name or "asset.bin")
    found, _source = materialize_key(
        key,
        dest,
        channel=channel,
        client=client,
        bucket=bucket,
        folder_id=folder_id,
        allow_video=allow_video,
        hinted=raw,
    )
    return found


def ensure_b2_public_url(
    source: Path,
    *,
    key: str,
    client=None,
    bucket: str | None = None,
    content_type: str = "",
) -> str:
    """Return a public B2 URL for one file.

    Uploads that file (Class A) through the budgeted B2 client when the key
    is missing. Never deletes an object that is already in the bucket.
    """
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(path)
    object_key = key.replace("\\", "/").lstrip("/")
    if not object_key or ".." in object_key.split("/"):
        raise FileNotFoundError(key)
    from agents.media.b2_client import (  # noqa: PLC0415
        list_object_keys,
        media_bucket_name,
        upload_object,
    )

    bucket_name = (bucket or "").strip() or media_bucket_name()
    present = object_key in set(list_object_keys(object_key, client=client, bucket=bucket_name))
    if not present:
        upload_object(
            object_key,
            path,
            client=client,
            bucket=bucket_name,
            content_type=content_type or _content_type(object_key),
        )
        log.info("B2 Class A upload of one file: %s", object_key)
    return b2_public_url(bucket_name, object_key)


def b2_public_url(bucket: str, key: str) -> str:
    endpoint = (os.getenv("B2_ENDPOINT_URL") or "https://s3.us-east-005.backblazeb2.com").strip()
    host = urlsplit(endpoint).netloc or "s3.us-east-005.backblazeb2.com"
    return f"https://{bucket}.{host}/{key.lstrip('/')}"


def _copy_local_records(library: Path, prefix: str) -> int:
    channel, _rest = _split(prefix if prefix.endswith("/") else prefix + "/")
    if not channel:
        return 0
    from utils.pipeline_paths import outputs_path  # noqa: PLC0415

    source = outputs_path(channel, "library")
    try:
        if source.resolve() == library.resolve():
            return 0
    except OSError:
        return 0
    if not source.is_dir():
        return 0
    written = 0
    for path in sorted(source.glob("post_*.json")):
        dest = library / path.name
        if dest.is_file() and dest.stat().st_size > 0:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        written += 1
    if written:
        log.info("Copied %d library record(s) from the local tree.", written)
    return written


def _copy_drive_records(library: Path, prefix: str, *, folder_id: str | None) -> int:
    from agents.media.drive_source import download_object, list_object_keys  # noqa: PLC0415

    keys = list_object_keys(prefix, folder_id=folder_id)
    written = 0
    for key in keys:
        name = Path(key).name
        if not (name.startswith("post_") and name.endswith(".json")):
            continue
        dest = library / name
        if dest.is_file() and dest.stat().st_size > 0:
            continue
        download_object(key, dest, folder_id=folder_id)
        written += 1
    if written:
        log.info("Copied %d library record(s) from Drive.", written)
    return written


def _copy_b2_records(library: Path, prefix: str, *, client, bucket: str | None) -> int:
    from agents.media.b2_client import list_object_keys, read_object_text  # noqa: PLC0415

    keys = list_object_keys(prefix, client=client, bucket=bucket)
    written = 0
    for key in keys:
        name = Path(key).name
        if not (name.startswith("post_") and name.endswith(".json")):
            continue
        text = read_object_text(key, client=client, bucket=bucket)
        if not text:
            continue
        dest = library / name
        dest.write_text(text, encoding="utf-8")
        written += 1
    if written:
        log.info("Copied %d library record(s) from B2.", written)
    return written


def _drive_download(key: str, dest: Path, *, folder_id: str | None) -> Path | None:
    from agents.media.drive_source import DriveQuotaError, download_object  # noqa: PLC0415

    try:
        return download_object(key, dest, folder_id=folder_id)
    except FileNotFoundError:
        return None
    except DriveQuotaError:
        if library_source_order() == ("drive",):
            raise
        log.info("Drive quota exhausted while downloading %s.", Path(key).name)
        return None


def _b2_download(
    key: str,
    dest: Path,
    *,
    client,
    bucket: str | None,
    allow_video: bool | None,
) -> Path | None:
    if _skip_b2_video(key, allow_video):
        return None
    from agents.media.b2_budget import B2DailyCapError  # noqa: PLC0415
    from agents.media.b2_client import download_object  # noqa: PLC0415

    try:
        return download_object(
            key,
            dest,
            client=client,
            bucket=bucket,
            allow_video=allow_video,
        )
    except FileNotFoundError:
        return None
    except B2DailyCapError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.info("B2 download failed for %s (%s).", Path(key).name, type(exc).__name__)
        dest.unlink(missing_ok=True)
        return None


def _skip_b2_video(key: str, allow_video: bool | None) -> bool:
    if Path(key).suffix.lower() not in _VIDEO_SUFFIXES:
        return False
    if allow_video is True:
        return False
    if allow_video is False:
        return True
    from agents.media.b2_budget import allow_video_download  # noqa: PLC0415

    return not allow_video_download()


def _stop_for_source(exc: BaseException, source: str) -> bool:
    from agents.media.b2_budget import B2DailyCapError  # noqa: PLC0415
    from agents.media.drive_source import DriveQuotaError  # noqa: PLC0415

    if isinstance(exc, B2DailyCapError):
        return True
    return isinstance(exc, DriveQuotaError) and source == "drive" and library_source_order() == (
        "drive",
    )


def _split(key: str) -> tuple[str, str]:
    text = (key or "").replace("\\", "/").strip().strip("/")
    if not text or ".." in text.split("/"):
        return "", ""
    channel, _, rest = text.partition("/")
    return channel.strip().lower(), rest


def _content_type(key: str) -> str:
    return _CONTENT_TYPES.get(Path(key).suffix.lower(), "application/octet-stream")


__all__ = [
    "b2_public_url",
    "ensure_b2_public_url",
    "existing_local_file",
    "library_source_order",
    "materialize_key",
    "materialize_publish_file",
    "object_key_for_path",
    "sync_library_records",
]
