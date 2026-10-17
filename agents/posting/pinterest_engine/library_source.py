# -*- coding: utf-8 -*-
"""Library records from the PC disk or the same tree in B2.

Local ``outputs/<channel>/library/post_*.json`` wins when it exists.
Otherwise the JSON files under ``<channel>/library/`` are copied down.
The image for the pin being published is downloaded on its own and the
caller deletes that temp file. Nothing in B2 is deleted.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def library_prefix(channel_id: str, override: str = "") -> str:
    text = (override or f"{channel_id}/library/").strip().lstrip("/")
    if text and not text.endswith("/"):
        text += "/"
    return text


def ensure_local_library(
    outputs_dir: Path,
    *,
    prefix: str,
    client: Any | None = None,
    bucket: str | None = None,
) -> int:
    """Copy missing ``post_*.json`` records from B2. Returns how many were written.

    When the local library already has records, B2 is not listed.
    """
    library = outputs_dir / "library"
    library.mkdir(parents=True, exist_ok=True)
    if any(library.glob("post_*.json")):
        return 0
    try:
        from agents.media.b2_client import list_object_keys, read_object_text  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        log.info("B2 library unavailable (%s).", type(exc).__name__)
        return 0
    try:
        keys = list_object_keys(prefix, client=client, bucket=bucket)
    except Exception as exc:  # noqa: BLE001
        from agents.media.b2_budget import B2DailyCapError  # noqa: PLC0415

        if isinstance(exc, B2DailyCapError):
            raise
        log.info("B2 library list failed (%s).", type(exc).__name__)
        return 0
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
        log.info("Copied %d library record(s) from %s", written, prefix)
    return written


def image_object_key(record: dict, prefix: str) -> str:
    for field in ("b2_image_key", "image_key"):
        value = str(record.get(field) or "").strip().replace("\\", "/").lstrip("/")
        if value:
            return value
    rel = str(record.get("image_relative") or record.get("image_path") or "")
    rel = rel.strip().replace("\\", "/")
    if not rel:
        return ""
    if rel.startswith(prefix):
        return rel
    marker = "/library/"
    if marker in rel:
        rel = rel.split(marker, 1)[1]
    return prefix + rel.lstrip("/")


def materialize_image(
    record: dict,
    *,
    prefix: str,
    channel: str,
    client: Any | None = None,
    bucket: str | None = None,
) -> tuple[Path | None, bool]:
    """Return ``(path, delete_after)`` for the one image this pin needs."""
    local = str(record.get("local_image_path") or "")
    if local and Path(local).is_file():
        return Path(local), False
    key = image_object_key(record, prefix)
    if not key or Path(key).suffix.lower() not in _IMAGE_SUFFIXES:
        return None, False
    from agents.media.b2_client import download_object  # noqa: PLC0415
    from utils.pipeline_paths import temp_path  # noqa: PLC0415

    dest = temp_path(channel, "pinterest", Path(key).name)
    try:
        download_object(key, dest, client=client, bucket=bucket)
    except Exception as exc:  # noqa: BLE001
        from agents.media.b2_budget import B2DailyCapError  # noqa: PLC0415

        dest.unlink(missing_ok=True)
        if isinstance(exc, B2DailyCapError):
            raise
        log.info("Library image download failed for %s (%s).", key, type(exc).__name__)
        return None, False
    return dest, True


def upload_library_tree(
    outputs_dir: Path,
    *,
    prefix: str,
    client: Any | None = None,
    bucket: str | None = None,
) -> list[str]:
    """Upload library JSON and images. Existing keys are left in place. Nothing is deleted."""
    from agents.media.b2_client import list_object_keys, upload_object  # noqa: PLC0415

    library = outputs_dir / "library"
    if not library.is_dir():
        return []
    try:
        existing = set(list_object_keys(prefix, client=client, bucket=bucket))
    except Exception as exc:
        from agents.media.b2_budget import B2DailyCapError  # noqa: PLC0415

        if isinstance(exc, B2DailyCapError):
            raise
        existing = set()
    uploaded: list[str] = []
    for path in sorted(library.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _IMAGE_SUFFIXES | {".json"}:
            continue
        rel = path.relative_to(library).as_posix()
        key = prefix + rel
        if key in existing:
            continue
        content_type = "application/json" if path.suffix.lower() == ".json" else "image/jpeg"
        upload_object(key, path, client=client, bucket=bucket, content_type=content_type)
        uploaded.append(key)
    return uploaded


def write_record_json(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
