# -*- coding: utf-8 -*-
"""Read-only Google Drive library source.

Same read interface the publishers use for B2: list a prefix, get a size,
download one file. Listings and downloaded bytes are cached on the outputs
volume. Nothing in Drive is deleted. The service account scope is
``drive.readonly``.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_FOLDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CHANNEL_RE = re.compile(r"[^A-Za-z0-9]+")
_DEFAULT_TTL_S = 6 * 60 * 60
_BACKOFF_ATTEMPTS = 3
_QUOTA_REASONS = (
    "ratelimitexceeded",
    "userratelimitexceeded",
    "quotaexceeded",
    "dailylimitexceeded",
)

_LOCK = threading.Lock()
_listings: dict[str, dict] = {}
_missing_logged: set[str] = set()
_page_folder_ids: dict[str, str] = {}


class DriveQuotaError(RuntimeError):
    """Drive API quota is still exhausted after backoff."""


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def reset_caches(*, disk: bool = False) -> None:
    """Drop in-memory listing cache. Disk cache stays unless *disk* is set."""
    with _LOCK:
        _listings.clear()
        _missing_logged.clear()
        _page_folder_ids.clear()
    if disk:
        root = cache_root()
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)


def cache_root() -> Path:
    """Listings and file bytes live on the outputs volume."""
    override = (os.getenv("DRIVE_CACHE_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    from utils.pipeline_paths import outputs_root  # noqa: PLC0415

    return outputs_root() / "drive" / "cache"


def list_cache_ttl_seconds() -> int:
    raw = (os.getenv("DRIVE_LIST_CACHE_TTL_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_TTL_S
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_TTL_S


def service_account_path() -> Path | None:
    """Path from ``GOOGLE_DRIVE_SA_JSON``. The file contents are never logged."""
    raw = (os.getenv("GOOGLE_DRIVE_SA_JSON") or "").strip().strip('"').strip("'")
    if not raw or "PRIVATE KEY" in raw or "\n" in raw or len(raw) > 300:
        return None
    path = Path(raw).expanduser()
    if path.is_file():
        return path
    return None


def drive_credentials_present() -> bool:
    return service_account_path() is not None


def env_name_for_channel(channel: str) -> str:
    slug = _CHANNEL_RE.sub("_", (channel or "").strip()).strip("_").upper()
    return f"DRIVE_LIBRARY_FOLDER_ID_{slug or 'UNKNOWN'}"


def folder_id_for_channel(channel: str, *, override: str = "", root: Path | None = None) -> str:
    """Folder id for one channel.

    Order: explicit override, ``DRIVE_LIBRARY_FOLDER_ID_<CHANNEL>``,
    ``DRIVE_LIBRARY_FOLDER_ID`` on the channel page config, then
    ``drive_library_folder_id`` in ``config/brandings.yaml``, then
    ``drive_folder_id`` in ``config/pinterest_accounts.yaml``.
    """
    explicit = (override or "").strip()
    if explicit:
        return explicit
    slug = (channel or "").strip().lower()
    if not slug:
        return ""
    env_value = (os.getenv(env_name_for_channel(slug)) or "").strip()
    if env_value:
        return env_value
    from_page = _page_config_folder_id(slug)
    if from_page:
        return from_page
    return _yaml_folder_id(slug, root)


def build_drive_service():
    """Build a Drive v3 client, or None when the account file cannot be used.

    Failures are logged by exception type only. The JSON is never written to
    the log.
    """
    path = service_account_path()
    if path is None:
        log.info("Drive service account JSON is not a readable file.")
        return None
    try:
        return _load_credentials(path)
    except Exception as exc:  # noqa: BLE001 — do not log the message; it can echo the key
        log.info("Drive service account was not loaded (%s).", type(exc).__name__)
        return None


def _load_credentials(path: Path):
    from google.oauth2 import service_account  # noqa: PLC0415
    from googleapiclient.discovery import build  # noqa: PLC0415

    creds = service_account.Credentials.from_service_account_file(
        str(path),
        scopes=[READONLY_SCOPE],
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_object_keys(
    prefix: str,
    *,
    client=None,
    folder_id: str | None = None,
) -> list[str]:
    """Return object keys under *prefix*. Does not download file bodies."""
    channel, _rest = _split_key(prefix)
    if not channel:
        return []
    resolved = folder_id_for_channel(channel, override=folder_id or "")
    if not _valid_folder_id(resolved):
        _log_missing_folder(channel)
        return []
    rows = _cached_rows(resolved, client)
    needle = prefix.replace("\\", "/").strip()
    if needle and not needle.endswith("/"):
        needle += "/"
    keys: list[str] = []
    for row in rows:
        full = f"{channel}/{row['path']}"
        if not needle or full.startswith(needle) or full == needle.rstrip("/"):
            keys.append(full)
    return keys


def object_size(
    key: str,
    *,
    client=None,
    folder_id: str | None = None,
) -> int | None:
    """Size in bytes from the cached listing. Does not download the file."""
    row = _row_for_key(key, client=client, folder_id=folder_id)
    if row is None:
        return None
    return int(row.get("size") or 0)


def download_object(
    key: str,
    dest: Path,
    *,
    client=None,
    folder_id: str | None = None,
) -> Path:
    """Download one file to *dest*. A cached copy is reused. Drive is not modified."""
    row = _row_for_key(key, client=client, folder_id=folder_id)
    if row is None:
        raise FileNotFoundError(key)
    dest = Path(dest)
    known = int(row.get("size") or 0)
    cached = _blob_path(key)
    if _usable(cached, known):
        if dest.resolve() != cached.resolve():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cached, dest)
        return dest
    service = _service(client)
    if service is None:
        raise FileNotFoundError(key)
    file_id = str(row.get("id") or "")
    if not _valid_folder_id(file_id):
        raise FileNotFoundError(key)
    _stream_to(service, file_id, cached, what=Path(key).name)
    if known and cached.is_file() and cached.stat().st_size != known:
        cached.unlink(missing_ok=True)
        raise OSError(f"Drive file size mismatch for {Path(key).name}")
    if dest.resolve() != cached.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, dest)
    return dest


def read_object_text(
    key: str,
    *,
    client=None,
    folder_id: str | None = None,
) -> str | None:
    """Read one small object as text. Missing keys return None."""
    try:
        path = download_object(key, _blob_path(key), client=client, folder_id=folder_id)
    except FileNotFoundError:
        return None
    except DriveQuotaError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.info("Drive object not read (%s): %s", type(exc).__name__, Path(key).name)
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        log.info("Drive object not read (%s): %s", type(exc).__name__, Path(key).name)
        return None


def call_with_backoff(action, *, what: str):
    """Run *action*. On a quota response, back off and then stop."""
    delay = 1.0
    last: BaseException | None = None
    for attempt in range(1, _BACKOFF_ATTEMPTS + 1):
        try:
            return action()
        except DriveQuotaError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not _is_quota_error(exc):
                raise
            last = exc
            if attempt >= _BACKOFF_ATTEMPTS:
                break
            log.warning(
                "Drive quota on %s (attempt %d/%d) — backing off %.0fs.",
                what,
                attempt,
                _BACKOFF_ATTEMPTS,
                delay,
            )
            _sleep(delay)
            delay *= 2
    log.warning("Drive quota exhausted on %s.", what)
    raise DriveQuotaError(f"Drive quota exhausted on {what}.") from last


def _service(client):
    if client is not None:
        return client
    return build_drive_service()


def _cached_rows(folder_id: str, client) -> list[dict]:
    with _LOCK:
        hit = _listings.get(folder_id)
        if hit is not None and _fresh(float(hit.get("saved_at") or 0)):
            return list(hit.get("files") or [])
    disk = _read_disk(folder_id)
    if disk is not None:
        with _LOCK:
            _listings[folder_id] = disk
        return list(disk.get("files") or [])
    service = _service(client)
    if service is None:
        return []
    rows = _normalize_library(_walk(service, folder_id))
    payload = {"saved_at": time.time(), "files": rows}
    with _LOCK:
        _listings[folder_id] = payload
    _write_disk(folder_id, payload)
    return rows


def _row_for_key(key: str, *, client, folder_id: str | None) -> dict | None:
    channel, relative = _split_key(key)
    if not channel or not relative:
        return None
    resolved = folder_id_for_channel(channel, override=folder_id or "")
    if not _valid_folder_id(resolved):
        _log_missing_folder(channel)
        return None
    for row in _cached_rows(resolved, client):
        if row.get("path") == relative:
            return row
    return None


def _walk(service, folder_id: str) -> list[dict]:
    found: list[dict] = []
    stack: list[tuple[str, str]] = [(folder_id, "")]
    while stack:
        current, prefix = stack.pop()
        for item in _list_children(service, current):
            name = str(item.get("name") or "").replace("/", "_").strip()
            if not name or name in {".", ".."}:
                continue
            rel = name if not prefix else f"{prefix}/{name}"
            mime = str(item.get("mimeType") or "")
            if mime == _FOLDER_MIME:
                child = str(item.get("id") or "")
                if _valid_folder_id(child):
                    stack.append((child, rel))
                continue
            if mime.startswith("application/vnd.google-apps."):
                continue
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            found.append(
                {
                    "path": rel,
                    "id": str(item.get("id") or ""),
                    "size": size,
                    "mime": mime,
                }
            )
    found.sort(key=lambda row: row["path"])
    return found


def _list_children(service, folder_id: str) -> list[dict]:
    files: list[dict] = []
    token: str | None = None
    while True:
        kwargs: dict = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id, name, size, mimeType)",
            "pageSize": 1000,
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }
        if token:
            kwargs["pageToken"] = token

        def _call(kwargs: dict = kwargs):
            return service.files().list(**kwargs).execute()

        page = call_with_backoff(_call, what="list folder")
        files.extend(page.get("files") or [])
        token = page.get("nextPageToken")
        if not token:
            break
    return files


def _stream_to(service, file_id: str, dest: Path, *, what: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")

    def _pull() -> None:
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        if hasattr(request, "execute_media"):
            data = request.execute_media()
            if not isinstance(data, (bytes, bytearray)):
                data = bytes(data)
            partial.write_bytes(data)
            return
        from googleapiclient.http import MediaIoBaseDownload  # noqa: PLC0415

        with partial.open("wb") as handle:
            downloader = MediaIoBaseDownload(handle, request, chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                _status, done = downloader.next_chunk()

    try:
        call_with_backoff(_pull, what=f"download {what}")
        partial.replace(dest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _normalize_library(rows: list[dict]) -> list[dict]:
    paths = [str(row.get("path") or "") for row in rows]
    if not _looks_like_library_root(paths):
        return rows
    shifted: list[dict] = []
    for row in rows:
        copy = dict(row)
        copy["path"] = "library/" + str(row.get("path") or "")
        shifted.append(copy)
    return shifted


def _looks_like_library_root(paths: list[str]) -> bool:
    """True when the shared folder is ``outputs/<channel>/library`` itself."""
    if any(path == "library" or path.startswith("library/") for path in paths):
        return False
    if any(path == "clips" or path.startswith("clips/") for path in paths):
        return False
    suffixes = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    for path in paths:
        name = Path(path).name
        if name.startswith("post_") and name.endswith(".json"):
            return True
        if Path(path).suffix.lower() in suffixes:
            return True
    return False


def _split_key(key: str) -> tuple[str, str]:
    text = (key or "").replace("\\", "/").strip().strip("/")
    if not text or ".." in text.split("/"):
        return "", ""
    channel, _, rest = text.partition("/")
    return channel.strip().lower(), rest


def _valid_folder_id(value: str) -> bool:
    return bool(value) and bool(_FOLDER_ID_RE.fullmatch(value))


def _log_missing_folder(channel: str) -> None:
    with _LOCK:
        if channel in _missing_logged:
            return
        _missing_logged.add(channel)
    log.info("Drive library folder id is unset for %s.", channel)


def _fresh(saved_at: float) -> bool:
    ttl = list_cache_ttl_seconds()
    if ttl <= 0:
        return False
    return (time.time() - saved_at) < ttl


def _disk_path(folder_id: str) -> Path:
    return cache_root() / "listings" / f"{folder_id}.json"


def _read_disk(folder_id: str) -> dict | None:
    path = _disk_path(folder_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not _fresh(float(payload.get("saved_at") or 0)):
        return None
    files = payload.get("files")
    if not isinstance(files, list):
        return None
    return payload


def _write_disk(folder_id: str, payload: dict) -> None:
    path = _disk_path(folder_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        log.info("Drive listing cache was not written (%s).", type(exc).__name__)


def download_cache_path(key: str) -> Path:
    """Where a downloaded Drive file is kept on the outputs volume."""
    return _blob_path(key)


def _blob_path(key: str) -> Path:
    channel, relative = _split_key(key)
    safe = Path(relative)
    if safe.is_absolute() or ".." in safe.parts:
        raise FileNotFoundError(key)
    return cache_root() / "files" / (channel or "unknown") / safe


def _usable(path: Path, known: int) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    if known and path.stat().st_size != known:
        return False
    return True


def _is_quota_error(exc: BaseException) -> bool:
    status = _status_code(exc)
    blob = _reason_blob(exc).lower().replace("_", "")
    if status == 429:
        return True
    if any(reason in blob for reason in _QUOTA_REASONS):
        return status in {0, 403, 429}
    return False


def _status_code(exc: BaseException) -> int:
    status = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "resp", None)
        status = getattr(resp, "status", None) if resp is not None else None
    try:
        return int(status or 0)
    except (TypeError, ValueError):
        return 0


def _reason_blob(exc: BaseException) -> str:
    parts = [str(getattr(exc, "reason", "") or "")]
    content = getattr(exc, "content", b"")
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    if content:
        parts.append(str(content))
    return " ".join(parts)


def _page_config_folder_id(channel: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "", channel)
    if not slug:
        return ""
    with _LOCK:
        if slug in _page_folder_ids:
            return _page_folder_ids[slug]
    try:
        module = importlib.import_module(f"channels_config.{slug}.page_config")
    except Exception:  # noqa: BLE001
        value = ""
    else:
        value = str(getattr(module, "DRIVE_LIBRARY_FOLDER_ID", "") or "").strip()
    with _LOCK:
        _page_folder_ids[slug] = value
    return value


def _yaml_folder_id(channel: str, root: Path | None) -> str:
    base = root if root is not None else Path(__file__).resolve().parents[2]
    branding = _read_yaml(base / "config" / "brandings.yaml")
    body = (branding.get("brandings") or {}).get(channel) if isinstance(branding, dict) else None
    if isinstance(body, dict):
        value = str(body.get("drive_library_folder_id") or "").strip()
        if value:
            return value
    accounts = _read_yaml(base / "config" / "pinterest_accounts.yaml")
    rows = accounts.get("accounts") if isinstance(accounts, dict) else None
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("channel_id") or "").strip().lower() != channel:
                continue
            value = str(row.get("drive_folder_id") or "").strip()
            if value:
                return value
    return ""


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    return loaded if isinstance(loaded, dict) else {}


__all__ = [
    "READONLY_SCOPE",
    "DriveQuotaError",
    "build_drive_service",
    "cache_root",
    "download_cache_path",
    "download_object",
    "drive_credentials_present",
    "env_name_for_channel",
    "folder_id_for_channel",
    "list_object_keys",
    "object_size",
    "read_object_text",
    "reset_caches",
    "service_account_path",
]
