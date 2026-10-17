# -*- coding: utf-8 -*-
"""Archival photographs for an event beat, before any generated scene."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from PIL import Image

from agents.media.free_images.http import RequestsClient
from agents.media.free_images.licenses import license_allowed
from agents.media.free_images.models import Candidate
from modules.reel_artefact.budget import claim_still, file_hash_key, release_still
from modules.reel_artefact.crop import anchor_for, prepare_reel_frame
from modules.reel_artefact.fetch import (
    _BAD_TITLE,
    _commons_category,
    _commons_search,
    _download,
    artefact_cache_dir,
)
from modules.reel_artefact.logbook import note_source
from modules.reel_artefact.motion import profile_for, write_motion_sidecar
from modules.reel_artefact.source import write_source_sidecar
from modules.shot_planner.entities import Entity

logger = logging.getLogger(__name__)

_STOP = {"file", "category", "with", "from", "that", "this", "wreck"}


def _queries(entity: Entity | None) -> list[str]:
    if entity is None:
        return []
    return [item for item in getattr(entity, "event_search", ()) or () if str(item).strip()]


def _categories(entity: Entity | None) -> list[str]:
    if entity is None:
        return []
    return [
        item for item in getattr(entity, "event_categories", ()) or () if str(item).strip()
    ]


def _tokens(entity: Entity | None) -> set[str]:
    period = str(getattr(entity, "event_period", "") or "")
    blob = " ".join(_queries(entity)) + " " + period
    return {
        token
        for token in re.findall(r"[a-z0-9]{4,}", blob.lower())
        if token not in _STOP
    }


def _usable(candidate: Candidate, tokens: set[str], *, from_category: bool) -> bool:
    if not candidate.download_url:
        return False
    if license_allowed(candidate.license_raw) is None:
        return False
    title = (candidate.title or "").lower()
    if any(bad in title for bad in _BAD_TITLE):
        return False
    long_side = max(int(candidate.width or 0), int(candidate.height or 0))
    if long_side < 720:
        return False
    if from_category:
        return True
    return any(token in title for token in tokens)


def _collect(entity: Entity, client) -> list[tuple[Candidate, bool]]:
    found: list[tuple[Candidate, bool]] = []
    seen: set[str] = set()
    tokens = _tokens(entity)

    def add(items: list[Candidate], *, from_category: bool) -> None:
        for item in items:
            if not _usable(item, tokens, from_category=from_category):
                continue
            key = (item.download_url or item.title).split("?")[0].lower()
            if not key or key in seen:
                continue
            seen.add(key)
            found.append((item, from_category))

    for category in _categories(entity):
        add(_commons_category(category, client), from_category=True)
    for query in _queries(entity):
        add(_commons_search(query, client), from_category=False)
    return found


def acquire_event_photo(
    entity: Entity | None,
    dest: Path,
    *,
    channel: str,
    beat_index: int = 0,
    n_beats: int = 0,
    client=None,
) -> Path | None:
    """Publish one archival still. None when the event has no usable photo."""
    if entity is None or (not _queries(entity) and not _categories(entity)):
        return None
    http = client or RequestsClient(timeout=20.0)
    folder = artefact_cache_dir(channel or "ancient_knowledge", entity.id) / "events"
    folder.mkdir(parents=True, exist_ok=True)
    for index, (candidate, _category) in enumerate(_collect(entity, http)):
        raw = folder / f"event_{index:02d}.jpg"
        if not raw.is_file():
            data = _download(candidate, http)
            if not data:
                continue
            raw.write_bytes(data)
        if not raw.is_file():
            continue
        key = file_hash_key(str(raw))
        target = str(Path(dest).resolve())
        ok, reason = claim_still(target, key, n_beats)
        if not ok:
            logger.info(
                "event still capped | entity=%s | beat=%s | %s",
                entity.id,
                int(beat_index) + 1,
                reason,
            )
            continue
        try:
            with Image.open(raw) as opened:
                frame, _how = prepare_reel_frame(
                    opened.convert("RGB"),
                    anchor=anchor_for(int(beat_index)),
                )
        except (OSError, ValueError):
            release_still(target, key)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        frame.save(dest, format="JPEG", quality=92)
        write_motion_sidecar(dest, profile=profile_for(int(beat_index)))
        provider = candidate.provider or "commons"
        write_source_sidecar(
            dest,
            {
                "provider": provider,
                "origin": provider,
                "license": candidate.license_raw,
                "credit_line": "",
                "credit_required": False,
                "reference_image": str(raw),
                "canonical_shape": "",
                "title": candidate.title,
                "source_url": candidate.source_url,
                "author": candidate.author,
                "model_id": "",
                "image_hash": key,
                "reuse_key": key,
                "still_kind": "scene",
            },
        )
        note_source(f"event:{provider}", entity.id, int(beat_index) + 1)
        return dest
    return None
