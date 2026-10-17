# -*- coding: utf-8 -*-
"""Wrap the reel image call. A no-op while the hybrid flag is off."""
from __future__ import annotations

import logging
from pathlib import Path

from modules.reel_artefact.policy import artefact_mode
from modules.reel_artefact.route import acquire_photo, generic_flux_prompt, is_retry_stem
from modules.shot_planner.entities import Entity, load_entities
from modules.shot_planner.schema import Shot

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
        dest_dir = Path(output_directory) if output_directory else Path(".")
        stem = str(output_stem or "artefact")
        dest = dest_dir / f"{stem}.jpg"
        if role == "flux" or mode == "flux":
            text = generic_flux_prompt(str(prompt or "")) if role == "flux" else prompt
            return generate_fn(
                text,
                output_stem=output_stem,
                output_directory=output_directory,
                **kwargs,
            )
        photo = acquire_photo(
            entity,
            dest,
            channel=channel,
            mode=mode,
            variation=is_retry_stem(stem),
        )
        if photo is not None and Path(photo).is_file():
            logger.info(
                "Artefact still | entity=%s mode=%s file=%s",
                entity.id,
                mode,
                Path(photo).name,
            )
            return photo
        logger.info("Artefact still missing | entity=%s mode=%s", entity.id, mode)
        return None

    extra = {
        "artefact_role": role,
        "artefact_entity_id": shot.entity_id,
        "canonical_shape": shot.canonical_shape,
    }
    return _wrapped, extra


def _entity(channel: str, entity_id: str) -> Entity | None:
    if not entity_id:
        return None
    for item in load_entities(channel or "ancient_knowledge"):
        if item.id == entity_id:
            return item
    return None
