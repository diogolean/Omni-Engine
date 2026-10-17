# -*- coding: utf-8 -*-
"""Ken Burns reuse of a still that already passed the judge."""
from __future__ import annotations

import threading
from pathlib import Path

from modules.reel_artefact.logbook import note_source
from modules.reel_artefact.motion import profile_for, write_motion_sidecar
from modules.reel_artefact.source import source_meta, write_source_sidecar

_LOCK = threading.Lock()
_APPROVED: list[Path] = []


def reset_approved() -> None:
    with _LOCK:
        _APPROVED.clear()


def remember_approved(path: Path | str | None) -> None:
    if not path:
        return
    image = Path(path)
    if not image.is_file():
        return
    with _LOCK:
        if image not in _APPROVED:
            _APPROVED.append(image)


def approved_still() -> Path | None:
    with _LOCK:
        for image in reversed(_APPROVED):
            if image.is_file():
                return image
    return None


def reuse_into(
    dest: Path | str,
    beat_index: int,
    *,
    entity_id: str = "",
    source: Path | str | None = None,
) -> Path | None:
    """Copy an approved still and give this beat a different Ken Burns move."""
    origin = Path(source) if source else approved_still()
    if origin is None or not origin.is_file():
        return None
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    if origin.resolve() != target.resolve():
        target.write_bytes(origin.read_bytes())
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
        },
    )
    note_source("reuse:approved", entity_id, int(beat_index) + 1)
    return target
