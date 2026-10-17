# -*- coding: utf-8 -*-
"""Which beats may show a named artefact, and which stay generic."""
from __future__ import annotations

from modules.shot_planner.entities import Entity

_GENERIC = (
    "archaeologist",
    "excavation",
    "at dusk",
    "landscape",
    "museum hall",
    "atmosphere",
    "open sky",
)
_HERO = ("museum case", "fills the frame", "museum hero", "macro", "close")


def image_role_for(
    entity: Entity | None,
    *,
    shot_type: str = "",
    narration: str = "",
    camera: str = "",
    setting: str = "",
) -> str:
    """``photo`` for a named artefact or site. ``flux`` for a generic scene.

    A close view of the object stays on the real photograph. An archaeologist,
    a dusk landscape, or an empty museum hall does not ask Flux to invent
    the artefact.
    """
    if entity is None:
        return "flux"
    blob = " ".join((narration, camera, setting, shot_type)).lower()
    hero = shot_type in {"macro", "close"} or any(phrase in blob for phrase in _HERO)
    generic = any(cue in blob for cue in _GENERIC)
    if generic and not hero:
        return "flux"
    if entity.kind in {"artifact", "artefact", "place", "site"}:
        return "photo"
    return "flux"
