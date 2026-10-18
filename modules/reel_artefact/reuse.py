# -*- coding: utf-8 -*-
"""Ken Burns reuse of a still that already passed the judge."""
from __future__ import annotations

import threading
from pathlib import Path

from PIL import Image

from modules.reel_artefact.budget import note_original, note_reuse, reuse_room
from modules.reel_artefact.crop import anchor_for, prepare_reel_frame
from modules.reel_artefact.logbook import note_source
from modules.reel_artefact.motion import profile_for, write_motion_sidecar
from modules.reel_artefact.source import source_meta, write_source_sidecar

_LOCK = threading.Lock()
_APPROVED: list[tuple[str, Path]] = []


def reset_approved() -> None:
    with _LOCK:
        _APPROVED.clear()


def reuse_key_for(path: Path | str | None) -> str:
    if not path:
        return ""
    meta = source_meta(path)
    saved = str(meta.get("reuse_key") or "")
    if saved:
        return saved
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(path)


def remember_approved(path: Path | str | None, *, kind: str = "object") -> None:
    if not path:
        return
    image = Path(path)
    if not image.is_file():
        return
    label = kind if kind in {"object", "scene"} else "object"
    key = reuse_key_for(image)
    with _LOCK:
        already = any(
            existing.resolve() == image.resolve()
            for _, existing in _APPROVED
            if existing.exists()
        )
        if not already:
            _APPROVED.append((label, image))
    note_original(str(image.resolve()), key)


def approved_still(kind: str | None = None) -> Path | None:
    """Newest approved file. ``kind`` keeps a scene beat off an artefact still."""
    with _LOCK:
        rows = list(_APPROVED)
    for label, image in reversed(rows):
        if kind and label != kind:
            continue
        if image.is_file():
            return image
    return None


def reuse_into(
    dest: Path | str,
    beat_index: int,
    *,
    entity_id: str = "",
    source: Path | str | None = None,
    kind: str | None = None,
    n_beats: int = 0,
) -> Path | None:
    """Copy an approved still, with a new crop and a different Ken Burns move.

    Returns None when this still or this reel is already at the reuse cap.
    """
    origin = Path(source) if source else approved_still(kind)
    if origin is None or not origin.is_file():
        return None
    key = reuse_key_for(origin)
    allowed, reason = reuse_room(key, n_beats)
    if not allowed:
        note_source(f"reuse:skip-{reason}", entity_id, int(beat_index) + 1)
        return None
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    if origin.resolve() != target.resolve():
        _write_crop(origin, target, int(beat_index))
    write_motion_sidecar(target, profile=profile_for(int(beat_index)))
    previous = source_meta(origin)
    write_source_sidecar(
        target,
        {
            "provider": "reuse",
            "origin": str(previous.get("provider") or "reuse"),
            "license": str(previous.get("license") or ""),
            "credit_line": str(previous.get("credit_line") or ""),
            "credit_required": bool(previous.get("credit_line")),
            "reference_image": str(previous.get("reference_image") or origin),
            "canonical_shape": str(previous.get("canonical_shape") or ""),
            "title": str(previous.get("title") or ""),
            "source_url": str(previous.get("source_url") or ""),
            "author": str(previous.get("author") or ""),
            "model_id": "",
            "reuse_key": key,
            "still_kind": str(previous.get("still_kind") or kind or "object"),
        },
    )
    note_reuse(str(target.resolve()), key)
    note_source("reuse:approved", entity_id, int(beat_index) + 1)
    return target


def _write_crop(origin: Path, target: Path, beat_index: int) -> None:
    try:
        with Image.open(origin) as opened:
            frame, _how = prepare_reel_frame(
                opened.convert("RGB"),
                anchor=anchor_for(beat_index),
            )
        frame.save(target, format="JPEG", quality=92)
    except (OSError, ValueError):
        target.write_bytes(origin.read_bytes())
