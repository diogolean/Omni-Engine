# -*- coding: utf-8 -*-
"""Daily Backblaze B2 budgets shared by Pinterest, YouTube, and the ledger.

Counters live in one file-locked usage file on the outputs volume and reset
at 00:00 UTC. The engine, the scheduler, and scripts on the same host share
that file. The PC and the Oracle VM do not share a disk, so each keeps its
own counter. Defaults stay near half of B2's free daily allowance (1 GB
download, 2,500 Class B, 2,500 Class C) so the two hosts together stay
inside it. Env overrides are clamped to that allowance and cannot raise it.

A call that would pass the download, Class B, or Class C budget raises
``B2DailyCapError`` and stops the run. Bucket listings and small object
reads are cached. Downloads are one object at a time, and a video is not
pulled when an image can be published instead. A provider 403 that says
the cap is exceeded backs off, then stops the run.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
_DEFAULT_DOWNLOAD_MB = 500
_DEFAULT_CLASS_B = 1500
_DEFAULT_CLASS_C = 1500
# Backblaze B2 free daily allowance. Env overrides cannot go above these.
_FREE_DOWNLOAD_MB = 1000
_FREE_CLASS_B = 2500
_FREE_CLASS_C = 2500
_DEFAULT_LIST_TTL_S = 6 * 60 * 60
_BACKOFF_ATTEMPTS = 3

_listings: dict[str, dict] = {}
_texts: dict[str, dict] = {}


class B2StorageCapError(RuntimeError):
    """Raised when B2 rejects an upload due to storage/cap AccessDenied."""


class B2DailyCapError(B2StorageCapError):
    """Daily download or transaction budget is exhausted. Stop the run."""


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _int_env(name: str, default: int, *, ceiling: int | None = None) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        value = default
    else:
        try:
            value = max(0, int(raw))
        except ValueError:
            value = default
    if ceiling is not None and value > ceiling:
        return ceiling
    return value


def download_limit_mb() -> int:
    return _int_env(
        "B2_DAILY_DOWNLOAD_MB",
        _DEFAULT_DOWNLOAD_MB,
        ceiling=_FREE_DOWNLOAD_MB,
    )


def download_limit_bytes() -> int:
    return download_limit_mb() * 1024 * 1024


def class_b_limit() -> int:
    return _int_env("B2_DAILY_CLASS_B", _DEFAULT_CLASS_B, ceiling=_FREE_CLASS_B)


def class_c_limit() -> int:
    return _int_env("B2_DAILY_CLASS_C", _DEFAULT_CLASS_C, ceiling=_FREE_CLASS_C)


def list_cache_ttl_seconds() -> int:
    return _int_env("B2_LIST_CACHE_TTL_SECONDS", _DEFAULT_LIST_TTL_S)


def allow_video_download() -> bool:
    return (os.getenv("B2_ALLOW_VIDEO_DOWNLOAD") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_backblaze_endpoint(endpoint: str) -> bool:
    """True when *endpoint* is a Backblaze B2 URL.

    Other S3-compatible hosts, including RunPod, are not B2 and are not
    charged against this budget.
    """
    host = (endpoint or "").lower()
    return "backblazeb2" in host or "backblaze.com" in host


def usage_path() -> Path:
    override = (os.getenv("B2_USAGE_PATH") or "").strip()
    if override:
        return Path(override)
    from utils.pipeline_paths import outputs_root  # noqa: PLC0415

    return outputs_root() / "b2" / "usage.json"


def client_token(client: object | None) -> str:
    """Injected clients do not share a cache. The process client does."""
    if client is None:
        return "default"
    return str(id(client))


def reset_caches() -> None:
    """Drop in-memory listing and object caches. Disk counters stay put."""
    _listings.clear()
    _texts.clear()


def choose_publish_key(keys: list[str], sizes: dict[str, int] | None = None) -> str:
    """One object: the smallest image. A video is used only when nothing else exists."""
    known = sizes or {}
    images = [key for key in keys if Path(key).suffix.lower() in _IMAGE_SUFFIXES]
    if images:
        pool = images
    else:
        pool = [key for key in keys if Path(key).suffix.lower() not in _VIDEO_SUFFIXES]
    if not pool:
        pool = list(keys)
    if not pool:
        raise B2DailyCapError("No B2 object to publish.")
    return min(pool, key=lambda key: (known.get(key, 1 << 62), key))


def is_video_key(key: str) -> bool:
    return Path(key).suffix.lower() in _VIDEO_SUFFIXES


def is_transaction_cap(exc: BaseException) -> bool:
    """True for a 403 that says the daily cap was exceeded.

    A bare 403 on HeadObject is how B2 reports a missing private object, so
    that case is not a cap.
    """
    text = str(exc or "").lower()
    code = ""
    status = 0
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        err = response.get("Error") or {}
        code = str(err.get("Code") or err.get("Message") or "").lower()
        text = f"{text} {code} {err.get('Message') or ''}".lower()
        meta = response.get("ResponseMetadata") or {}
        try:
            status = int(meta.get("HTTPStatusCode") or 0)
        except (TypeError, ValueError):
            status = 0
    cap_words = ("cap", "bandwidth", "quota", "too many requests")
    exceeded = "exceed" in text or "exhausted" in text
    if any(word in text for word in cap_words) and (
        exceeded or "403" in text or status == 403 or code in {"403", "accessdenied"}
    ):
        return True
    if "download" in text and exceeded:
        return True
    return False


def charge(*, class_b: int = 0, class_c: int = 0, download_bytes: int = 0) -> None:
    """Reserve a transaction. Raises when today's budget cannot hold it.

    Class A uploads are free and are not charged here. Callers charge Class B
    for a head or download and Class C for a listing.
    """
    with _usage_file_lock():
        state = _load_state()
        if class_b and state["class_b"] + class_b > class_b_limit():
            _mark_refused(
                state,
                "class_b",
                "B2 daily Class B budget reached "
                f"({class_b_limit()}). Refusing further downloads until 00:00 UTC.",
            )
        if class_c and state["class_c"] + class_c > class_c_limit():
            _mark_refused(
                state,
                "class_c",
                "B2 daily Class C budget reached "
                f"({class_c_limit()}). Refusing further listings until 00:00 UTC.",
            )
        if download_bytes and state["download_bytes"] + download_bytes > download_limit_bytes():
            _mark_refused(state, "download", _download_cap_message())
        state["class_b"] += class_b
        state["class_c"] += class_c
        state["download_bytes"] += download_bytes
        _save_state(state)


def note_download_bytes(nbytes: int) -> None:
    """Record bytes after a download. Stop the run if this file crossed the cap."""
    if nbytes <= 0:
        return
    with _usage_file_lock():
        state = _load_state()
        state["download_bytes"] += nbytes
        if state["download_bytes"] > download_limit_bytes():
            _mark_refused(state, "download", _download_cap_message())
        _save_state(state)


def call_with_cap_backoff(action, *, what: str):
    """Run *action*. On a cap 403, back off and then stop the run."""
    delay = 1.0
    last: BaseException | None = None
    for attempt in range(1, _BACKOFF_ATTEMPTS + 1):
        try:
            return action()
        except B2DailyCapError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not is_transaction_cap(exc):
                raise
            last = exc
            if attempt >= _BACKOFF_ATTEMPTS:
                break
            log.warning(
                "B2 cap on %s (attempt %d/%d) — backing off %.0fs.",
                what,
                attempt,
                _BACKOFF_ATTEMPTS,
                delay,
            )
            _sleep(delay)
            delay *= 2
    message = f"B2 daily cap exceeded on {what}. Stopping this run."
    log.warning(message)
    raise B2DailyCapError(message) from last


def cached_listing(token: str, bucket: str, prefix: str) -> list[str] | None:
    hit = _cache_get(_listings, _list_path(), _list_key(token, bucket, prefix))
    if hit is None:
        return None
    keys = hit.get("keys")
    if isinstance(keys, list):
        return [str(key) for key in keys]
    return None


def cached_object_size(token: str, bucket: str, key: str) -> int | None:
    """Size from a cached listing, if this process already listed a parent prefix."""
    for hit in _listings.values():
        sizes = hit.get("sizes")
        if isinstance(sizes, dict) and key in sizes:
            try:
                return int(sizes[key])
            except (TypeError, ValueError):
                return None
    disk = _read_cache(_list_path())
    for hit in disk.values():
        if not isinstance(hit, dict):
            continue
        sizes = hit.get("sizes")
        if isinstance(sizes, dict) and key in sizes:
            try:
                return int(sizes[key])
            except (TypeError, ValueError):
                return None
    return None


def cached_listing_sizes(token: str, bucket: str, prefix: str) -> dict[str, int]:
    hit = _cache_get(_listings, _list_path(), _list_key(token, bucket, prefix))
    if hit is None:
        return {}
    sizes = hit.get("sizes")
    if not isinstance(sizes, dict):
        return {}
    parsed: dict[str, int] = {}
    for key, size in sizes.items():
        try:
            parsed[str(key)] = int(size)
        except (TypeError, ValueError):
            continue
    return parsed


def store_listing(
    token: str,
    bucket: str,
    prefix: str,
    keys: list[str],
    sizes: dict[str, int] | None = None,
) -> None:
    _cache_put(
        _listings,
        _list_path(),
        _list_key(token, bucket, prefix),
        {"at": time.time(), "prefix": prefix, "keys": list(keys), "sizes": dict(sizes or {})},
    )


def invalidate_listings(token: str, bucket: str, key: str) -> None:
    disk = _read_cache(_list_path())
    changed = False
    for cache_key in list(_listings):
        if _listing_covers(cache_key, _listings[cache_key], token, bucket, key):
            _listings.pop(cache_key, None)
            changed = True
    for cache_key in list(disk):
        if _listing_covers(cache_key, disk[cache_key], token, bucket, key):
            disk.pop(cache_key, None)
            changed = True
    if changed:
        _write_cache(_list_path(), disk)


def cached_text(token: str, bucket: str, key: str) -> str | None:
    hit = _cache_get(_texts, _text_path(), _text_key(token, bucket, key))
    if hit is None:
        return None
    text = hit.get("text")
    return text if isinstance(text, str) else None


def store_text(token: str, bucket: str, key: str, text: str) -> None:
    if len(text.encode("utf-8")) > 1_000_000:
        return
    _cache_put(
        _texts,
        _text_path(),
        _text_key(token, bucket, key),
        {"at": time.time(), "text": text},
    )


def invalidate_text(token: str, bucket: str, key: str) -> None:
    cache_key = _text_key(token, bucket, key)
    _texts.pop(cache_key, None)
    disk = _read_cache(_text_path())
    if cache_key in disk:
        disk.pop(cache_key, None)
        _write_cache(_text_path(), disk)


def _list_key(token: str, bucket: str, prefix: str) -> str:
    return f"{token}|{bucket}|{prefix}"


def _text_key(token: str, bucket: str, key: str) -> str:
    return f"{token}|{bucket}|{key}"


def _list_path() -> Path:
    return usage_path().with_name("list_cache.json")


def _text_path() -> Path:
    return usage_path().with_name("text_cache.json")


def _listing_covers(cache_key: str, hit: dict, token: str, bucket: str, key: str) -> bool:
    prefix = str(hit.get("prefix") or "")
    return cache_key.startswith(f"{token}|{bucket}|") and key.startswith(prefix)


def _cache_get(memory: dict[str, dict], path: Path, cache_key: str) -> dict | None:
    hit = memory.get(cache_key)
    if hit is None:
        disk = _read_cache(path)
        loaded = disk.get(cache_key)
        if isinstance(loaded, dict):
            hit = loaded
            memory[cache_key] = hit
    if hit is None:
        return None
    if time.time() - float(hit.get("at") or 0) > list_cache_ttl_seconds():
        memory.pop(cache_key, None)
        return None
    return hit


def _cache_put(memory: dict[str, dict], path: Path, cache_key: str, hit: dict) -> None:
    memory[cache_key] = hit
    disk = _read_cache(path)
    disk[cache_key] = hit
    _write_cache(path, disk)


def _read_cache(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def _download_cap_message() -> str:
    return (
        "B2 daily download budget reached "
        f"({download_limit_mb()} MB). "
        "Refusing further downloads until 00:00 UTC."
    )


@contextmanager
def _usage_file_lock():
    """Exclusive lock so every process on this host shares one counter.

    The lock file sits beside the usage file on the outputs volume. flock
    (Windows: msvcrt) covers other processes; threads in this process take
    separate descriptors and block on the same lock. The PC and the VM do
    not share this file.
    """
    path = usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    handle = open(lock_path, "a+b")
    locked = False
    try:
        _lock_handle(handle)
        locked = True
        yield
    finally:
        if locked:
            _unlock_handle(handle)
        handle.close()


def _lock_handle(handle) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_handle(handle) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _mark_refused(state: dict, kind: str, message: str) -> None:
    """Record the refusal and stop the run. Caller holds the usage lock."""
    logged = state.setdefault("logged", {})
    first = not logged.get(kind)
    logged[kind] = True
    _save_state(state)
    if first:
        log.warning(message)
    raise B2DailyCapError(message)


def _fresh(day: str) -> dict:
    return {
        "day": day,
        "download_bytes": 0,
        "class_b": 0,
        "class_c": 0,
        "logged": {},
    }


def _load_state() -> dict:
    today = _today()
    path = usage_path()
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict) and loaded.get("day") == today:
            loaded.setdefault("download_bytes", 0)
            loaded.setdefault("class_b", 0)
            loaded.setdefault("class_c", 0)
            loaded.setdefault("logged", {})
            return loaded
    return _fresh(today)


def _save_state(state: dict) -> None:
    path = usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
