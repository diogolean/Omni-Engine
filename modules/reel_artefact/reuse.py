# -*- coding: utf-8 -*-
"""Ken Burns reuse of a still that already passed the judge."""
from __future__ import annotations

import re
import threading
from pathlib import Path

from PIL import Image

from modules.reel_artefact.budget import (
    note_original,
    note_reuse,
    note_safety_use,
    release_noted_reuse,
    release_safety_use,
    reuse_room,
    safety_reel_room,
    safety_room,
    still_uses,
)
from modules.reel_artefact.crop import anchor_for, prepare_reel_frame
from modules.reel_artefact.logbook import note_source
from modules.reel_artefact.motion import profile_for, write_motion_sidecar
from modules.reel_artefact.source import source_meta, write_source_sidecar

_LOCK = threading.Lock()
_APPROVED: list[tuple[str, Path]] = []
_BEAT_KEYS: dict[int, str] = {}


def reset_approved() -> None:
    with _LOCK:
        _APPROVED.clear()
        _BEAT_KEYS.clear()


def note_beat_key(beat_index: int, key: str) -> None:
    """Remember which still a beat published, so the next beat can avoid it."""
    token = str(key or "")
    if not token:
        return
    with _LOCK:
        _BEAT_KEYS[int(beat_index)] = token


def forget_beat_key(beat_index: int) -> None:
    """Drop the key when the copy is rejected and the beat stays empty."""
    with _LOCK:
        _BEAT_KEYS.pop(int(beat_index), None)


def adjacent_same_still(beat_index: int, key: str) -> bool:
    """True when the previous or next beat already shows this still."""
    token = str(key or "")
    if not token:
        return False
    index = int(beat_index)
    with _LOCK:
        return _BEAT_KEYS.get(index - 1) == token or _BEAT_KEYS.get(index + 1) == token


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
    safety: bool = False,
    force: bool = False,
) -> Path | None:
    """Copy an approved still, with a new crop and a different Ken Burns move.

    Returns None when this still or this reel is already at the reuse cap.
    ``safety`` is the last resort: it ignores the per-reel fraction and the
    normal per-still cap, and stops at 3 uses of that still.
    """
    origin = Path(source) if source else approved_still(kind)
    if origin is None or not origin.is_file():
        return None
    key = reuse_key_for(origin)
    if adjacent_same_still(int(beat_index), key):
        note_source("reuse:skip-adjacent", entity_id, int(beat_index) + 1)
        return None
    if force:
        pass
    elif safety:
        if not safety_reel_room() or not safety_room(key):
            note_source("reuse:skip-safety", entity_id, int(beat_index) + 1)
            return None
    else:
        allowed, reason = reuse_room(key, n_beats)
        if not allowed:
            note_source(f"reuse:skip-{reason}", entity_id, int(beat_index) + 1)
            return None
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    if origin.resolve() != target.resolve():
        anchor = _rescue_anchor(origin, int(beat_index)) if safety else ""
        _write_crop(origin, target, int(beat_index), anchor=anchor)
    write_motion_sidecar(target, profile=_rescue_profile(origin, int(beat_index)))
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
    if safety:
        note_safety_use()
    note_beat_key(int(beat_index), key)
    from modules.reel_artefact.diversity import claim_diverse

    if not claim_diverse(target, source_key=key, beat_index=int(beat_index)):
        release_noted_reuse(str(target.resolve()), key)
        if safety:
            release_safety_use()
        forget_beat_key(int(beat_index))
        note_source("reuse:skip-phash", entity_id, int(beat_index) + 1)
        try:
            target.unlink()
        except OSError:
            pass
        return None
    note_source("reuse:approved", entity_id, int(beat_index) + 1)
    return target


_PLACE = {
    "",
    "scene",
    "landscape",
    "sea",
    "aerial",
    "detail",
    "golden",
    "fog",
    "ruins",
    "coast",
    "establishing",
    "atmosphere",
}


def _is_place(path: Path) -> bool:
    meta = source_meta(path)
    kind = str(meta.get("still_kind") or "")
    if kind in {"object", "event"}:
        return False
    scene = str(meta.get("scene_type") or "")
    return scene in _PLACE


def approved_place_stills() -> list[Path]:
    """Approved landscapes, seas, and the other place scenes from this reel."""
    with _LOCK:
        rows = list(_APPROVED)
    found: list[Path] = []
    for label, image in rows:
        if label != "scene" or not image.is_file() or not _is_place(image):
            continue
        found.append(image)
    return found


def _plain_fallback(dest: Path, beat_index: int) -> Path:
    """A distinct still so an exhausted beat still has a frame."""
    from PIL import Image, ImageDraw

    dest.parent.mkdir(parents=True, exist_ok=True)
    colors = (
        (86, 108, 72),
        (72, 96, 118),
        (128, 104, 70),
        (64, 84, 58),
        (98, 82, 64),
        (70, 110, 96),
    )
    image = Image.new("RGB", (96, 160), colors[int(beat_index) % len(colors)])
    draw = ImageDraw.Draw(image)
    y = 12 + (int(beat_index) % 7) * 16
    draw.rectangle((8, y, 88, y + 10), fill=(210, 196, 150))
    image.save(dest, format="JPEG", quality=85)
    return dest


def guarantee_still(
    dest: Path | str,
    beat_index: int,
    *,
    entity_id: str = "",
    n_beats: int = 0,
) -> Path:
    """A still for a beat the ladder could not fill.

    An approved place from this reel comes first. The safety cap is ignored
    so a few hard beats cannot drop the reel. Adjacent copies are still
    refused. The last resort is a plain frame that is not a neighbour of
    the previous beat.
    """
    target = Path(dest)
    placed = rescue_place(
        target, int(beat_index), entity_id=entity_id, n_beats=n_beats,
    )
    if placed is not None and placed.is_file():
        return placed
    with _LOCK:
        rows = list(_APPROVED)
    for label, image in rows:
        del label
        if not image.is_file():
            continue
        forced = reuse_into(
            target,
            int(beat_index),
            entity_id=entity_id,
            source=image,
            n_beats=n_beats,
            force=True,
        )
        if forced is not None and forced.is_file():
            return forced
    return _plain_fallback(target, int(beat_index))


def rescue_place(
    dest: Path | str,
    beat_index: int,
    *,
    entity_id: str = "",
    n_beats: int = 0,
) -> Path | None:
    """Reuse one approved place still past the normal cap, at most 3 times."""
    ranked: list[tuple[int, str, Path]] = []
    if not safety_reel_room():
        return None
    for image in approved_place_stills():
        key = reuse_key_for(image)
        if not safety_room(key) or adjacent_same_still(int(beat_index), key):
            continue
        ranked.append((still_uses(key), key, image))
    if not ranked:
        return None
    ranked.sort(key=lambda row: (row[0], row[1]))
    _uses, _key, origin = ranked[0]
    return reuse_into(
        dest,
        beat_index,
        entity_id=entity_id,
        source=origin,
        kind="scene",
        n_beats=n_beats,
        safety=True,
    )


def _origin_index(path: Path) -> int | None:
    match = re.search(r"ladder_(\d+)", path.name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _rescue_profile(origin: Path, beat_index: int) -> str:
    """A Ken Burns move that is not the one already on the source still."""
    from modules.reel_artefact.motion import motion_sidecar_path

    current = ""
    path = motion_sidecar_path(origin)
    if path.is_file():
        try:
            import json

            current = str(json.loads(path.read_text(encoding="utf-8")).get("profile") or "")
        except (OSError, json.JSONDecodeError, ValueError):
            current = ""
    if not current:
        origin_index = _origin_index(origin)
        if origin_index is not None:
            current = profile_for(origin_index)
    profile = profile_for(int(beat_index))
    if profile == current:
        profile = profile_for(int(beat_index) + 1)
    return profile


def _rescue_anchor(origin: Path, beat_index: int) -> str:
    """A crop window that is not the one the source beat already used."""
    anchor = anchor_for(int(beat_index))
    origin_index = _origin_index(origin)
    if origin_index is not None and anchor == anchor_for(origin_index):
        anchor = anchor_for(int(beat_index) + 1)
    return anchor


def _write_crop(origin: Path, target: Path, beat_index: int, anchor: str = "") -> None:
    try:
        with Image.open(origin) as opened:
            frame, _how = prepare_reel_frame(
                opened.convert("RGB"),
                anchor=anchor or anchor_for(beat_index),
            )
        frame.save(target, format="JPEG", quality=92)
    except (OSError, ValueError):
        target.write_bytes(origin.read_bytes())
