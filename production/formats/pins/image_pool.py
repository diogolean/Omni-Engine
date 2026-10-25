# -*- coding: utf-8 -*-
"""Reuse an already-owned still for a pin. The copy costs nothing.

The reel scene library and the channel output library are both searched.
A hit needs one shared subject token. Cooldown and scene type are not
applied: a pin has neither. The original file stays where it is.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z][a-z'\-]{2,}")
_STOP = frozenset(
    "the and for with from this that into over under near upon your their them "
    "they what when where which while about after before between through across "
    "around still just more some every very also only than then once have been "
    "were was are not but you our its his her she photoreal documentary film "
    "grain natural colour color real location image photo shot 35mm wide".split()
)
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _tokens(*parts: str) -> set[str]:
    found: set[str] = set()
    for part in parts:
        for word in _WORD.findall((part or "").lower()):
            if word not in _STOP:
                found.add(word)
    return found


def _subject_tokens(entry: dict) -> set[str]:
    slug = str(entry.get("subject_slug") or "").replace("_", " ").replace("-", " ")
    return _tokens(
        str(entry.get("entity") or ""),
        str(entry.get("named_entity") or ""),
        str(entry.get("topic") or ""),
        str(entry.get("pinterest_title") or ""),
        str(entry.get("title") or ""),
        slug,
    )


def _scene_rows(channel: str) -> list[tuple[str, Path]]:
    from production.formats.economic_reel.artefact.scene_library import library_dir

    folder = library_dir(channel)
    index = folder / "index.json"
    if not index.is_file():
        return []
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    rows: list[tuple[str, Path]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("file") or "").strip()
        if not name:
            continue
        path = folder / name
        tags = item.get("tags") or []
        tag_text = " ".join(str(tag) for tag in tags) if isinstance(tags, list) else ""
        blob = " ".join(
            str(item.get(key) or "")
            for key in ("prompt", "geography", "period", "scene_type")
        )
        rows.append((f"{tag_text} {blob} {Path(name).stem}", path))
    return rows


def _record_image(record: dict, library: Path) -> Path | None:
    for field in ("local_image_path", "image_path"):
        raw = str(record.get(field) or "").strip()
        if raw and Path(raw).is_file():
            return Path(raw)
    rel = str(record.get("image_relative") or "").replace("\\", "/").strip()
    if rel and ".." not in rel.split("/"):
        candidate = library / rel
        if candidate.is_file():
            return candidate
    return None


def _output_rows(outputs_dir: Path | None) -> list[tuple[str, Path]]:
    if outputs_dir is None:
        return []
    library = Path(outputs_dir) / "library"
    if not library.is_dir():
        return []
    rows: list[tuple[str, Path]] = []
    for path in sorted(library.glob("post_*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        image = _record_image(record, library)
        if image is None:
            continue
        blob = " ".join(
            str(record.get(key) or "")
            for key in (
                "topic",
                "pinterest_title",
                "title",
                "entity",
                "subject_slug",
                "prompt",
            )
        )
        rows.append((f"{blob} {image.stem}", image))
    images = library / "images"
    if images.is_dir():
        for image in sorted(images.iterdir()):
            if image.is_file() and image.suffix.lower() in _IMAGE_SUFFIXES:
                rows.append((image.stem.replace("_", " ").replace("-", " "), image))
    return rows


def _stamp_pool(entry: dict) -> None:
    entry["image_credit"] = ""
    entry["image_license"] = ""
    entry["image_credit_required"] = False
    entry["image_attribution"] = ""
    entry["image_source"] = "pool"
    entry["generated_by"] = "pool"


def reuse_pool_image(
    channel: str,
    entry: dict,
    *,
    outputs_dir: Path | None = None,
) -> Path | None:
    """Copy the best token match into scratch space, or return None.

    The caller may delete the copy. The library file is left in place.
    """
    subject = _subject_tokens(entry)
    if not subject or not (channel or "").strip():
        return None
    from production.formats.pins.photos import is_text_graphic
    from shared.paths import temp_path

    ranked: list[tuple[int, str, Path]] = []
    seen: set[Path] = set()
    for blob, path in _scene_rows(channel) + _output_rows(outputs_dir):
        if path.suffix.lower() not in _IMAGE_SUFFIXES or not path.is_file():
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        score = len(subject & _tokens(blob, path.stem.replace("_", " ").replace("-", " ")))
        if score >= 1:
            ranked.append((score, str(resolved), path))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    for score, _key, path in ranked:
        if is_text_graphic(path):
            continue
        stem = str(entry.get("post_id") or entry.get("subject_slug") or "pin").strip() or "pin"
        dest = temp_path(channel, "pin_pool", f"{stem}{path.suffix.lower()}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        _stamp_pool(entry)
        log.info("pin pool reuse | tokens=%d | %s", score, path.name)
        return dest
    return None
