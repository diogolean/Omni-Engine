# -*- coding: utf-8 -*-
"""Wrap the reel image call. A no-op while the hybrid flag is off."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from modules.reel_artefact.logbook import note_source
from modules.reel_artefact.policy import artefact_mode
from modules.reel_artefact.reuse import reuse_into
from modules.reel_artefact.role import beat_kind
from modules.reel_artefact.route import (
    acquire_photo,
    cached_variation,
    generic_flux_prompt,
    is_retry_stem,
)
from modules.shot_planner.entities import Entity, load_entities
from modules.shot_planner.schema import Shot

_ACT = re.compile(r"_act(\d+)")

logger = logging.getLogger(__name__)


def wrap_reel_generate(generate_fn, *, channel: str, shot: Shot | None):
    """Return ``(generate_fn, extra_kwargs)``.

    Flag off returns the original function and an empty dict, so Flux and
    the free-image flag behave as they do today.
    """
    mode = artefact_mode()
    if mode is None or shot is None or shot.kb_miss:
        return generate_fn, {}
    entity = _entity(channel, shot.entity_id)
    role = shot.image_role or ""
    if role not in {"photo", "flux"}:
        return generate_fn, {}
    if role == "photo" and entity is None:
        return generate_fn, {}

    def _wrapped(prompt, output_stem=None, output_directory=None, **kwargs):
        kwargs.pop("artefact_role", None)
        kwargs.pop("artefact_entity_id", None)
        kwargs.pop("canonical_shape", None)
        kwargs.pop("reference_image", None)
        kwargs.pop("visual_role", None)
        dest_dir = Path(output_directory) if output_directory else Path(".")
        stem = str(output_stem or "artefact")
        dest = dest_dir / f"{stem}.jpg"
        beat = _beat_index(stem)
        retry = is_retry_stem(stem)
        kind = beat_kind(
            entity,
            narration=str(getattr(shot, "narration", "") or ""),
            shot_type=str(getattr(shot, "shot_type", "") or ""),
            camera=str(getattr(shot, "camera", "") or ""),
            setting=str(getattr(shot, "setting", "") or ""),
        )
        if kind == "scene":
            # A place, people, or commentary beat gets its own documentary
            # frame. Reusing the artefact still is what made one edit fill
            # an entire reel.
            note_source("flux", entity.id if entity is not None else "", beat + 1)
            return generate_fn(
                generic_flux_prompt(str(prompt or "")),
                output_stem=output_stem,
                output_directory=output_directory,
                visual_role="documentary",
                **kwargs,
            )
        if role == "flux" or mode == "flux":
            return _flux_or_photoreal(
                generate_fn,
                prompt,
                dest,
                output_stem=output_stem,
                output_directory=output_directory,
                kwargs=kwargs,
                entity=entity,
                channel=channel,
                mode=mode,
                role=role,
                beat=beat,
                retry=retry,
            )
        photo = acquire_photo(
            entity,
            dest,
            channel=channel,
            mode=mode,
            variation=retry,
            beat_index=beat,
        )
        if photo is not None and Path(photo).is_file():
            logger.info(
                "Artefact still | entity=%s mode=%s file=%s",
                entity.id,
                mode,
                Path(photo).name,
            )
            return photo
        reused = _reuse_or_cached(dest, entity, channel, beat)
        if reused is not None:
            return reused
        logger.info("Artefact still missing | entity=%s mode=%s", entity.id, mode)
        note_source("flux", entity.id, beat + 1)
        return generate_fn(
            generic_flux_prompt(str(prompt or "")),
            output_stem=output_stem,
            output_directory=output_directory,
            **kwargs,
        )

    extra = {
        "artefact_role": role,
        "artefact_entity_id": shot.entity_id,
        "canonical_shape": shot.canonical_shape,
    }
    return _wrapped, extra


def _beat_index(stem: str) -> int:
    match = _ACT.search(stem or "")
    if not match:
        return 0
    return max(0, int(match.group(1)) - 1)


def _reuse_or_cached(dest: Path, entity: Entity | None, channel: str, beat: int) -> Path | None:
    if entity is not None:
        cached = cached_variation(entity, channel=channel)
        if cached is not None:
            copied = reuse_into(dest, beat, entity_id=entity.id, source=cached)
            if copied is not None:
                return copied
    return reuse_into(dest, beat, entity_id=entity.id if entity is not None else "")


def _flux_or_photoreal(
    generate_fn,
    prompt,
    dest: Path,
    *,
    output_stem,
    output_directory,
    kwargs: dict,
    entity: Entity | None,
    channel: str,
    mode: str,
    role: str,
    beat: int,
    retry: bool,
):
    """Commentary stays photoreal: a real photo, a reuse, then text-only if safe."""
    if mode != "flux" and entity is not None:
        photo = acquire_photo(
            entity,
            dest,
            channel=channel,
            mode="chain" if mode != "commons" else "commons",
            variation=retry,
            beat_index=beat,
            allow_nano=False,
        )
        if photo is not None and Path(photo).is_file():
            return photo
        reused = _reuse_or_cached(dest, entity, channel, beat)
        if reused is not None:
            return reused
        if bool(getattr(entity, "text_only_safe", False)):
            text = acquire_photo(
                entity,
                dest,
                channel=channel,
                mode="chain",
                beat_index=beat,
                text_only=True,
            )
            if text is not None and Path(text).is_file():
                return text
    elif entity is None:
        reused = reuse_into(dest, beat)
        if reused is not None:
            return reused
    note_source("flux", entity.id if entity is not None else "", beat + 1)
    text = generic_flux_prompt(str(prompt or "")) if role == "flux" or mode == "flux" else prompt
    return generate_fn(
        text,
        output_stem=output_stem,
        output_directory=output_directory,
        **kwargs,
    )


def _entity(channel: str, entity_id: str) -> Entity | None:
    if not entity_id:
        return None
    for item in load_entities(channel or "ancient_knowledge"):
        if item.id == entity_id:
            return item
    return None
