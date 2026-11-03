# -*- coding: utf-8 -*-
"""Which beats may show a named artefact, and which stay generic."""
from __future__ import annotations

from production.formats.economic_reel.shot_planner.entities import Entity

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
# Place, event, and people beats. These are scenes, not the object in a case.
_SCENE = (
    "diver",
    "divers",
    "shipwreck",
    "underwater",
    "under water",
    "ocean",
    " sea",
    "coast",
    "island",
    "boat",
    "workshop",
    "craftsman",
    "archaeologist",
    "excavation",
    "landscape",
    "museum hall",
    "people",
    "crowd",
    "sailor",
    "sponge",
    "open sky",
    "at dusk",
    "atmosphere",
    "desert",
    "harbour",
    "harbor",
    "beach",
    "crew",
    "discovered",
    "recovered",
    "found by",
    "off the",
    "legends",
    "theories",
    "commentary",
)
_OBJECT = (
    "museum case",
    "fills the frame",
    "museum hero",
    "on display",
    "the mechanism",
    "the pendant",
    "the artefact",
    "the artifact",
    "gear",
    "gears",
    "corroded",
    "fragments of the",
    "in its case",
    "the object",
    "pillar",
    "pillars",
    "relief",
    "reliefs",
    "carved",
    "carving",
)


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
    try:
        from production.formats.economic_reel.artefact.flux_policy import object_line_strength

        # Pillars, reliefs, and carved stone are the photograph, even when
        # the camera line also says "landscape".
        if object_line_strength(narration) >= 2:
            return "photo"
    except Exception:  # noqa: BLE001
        pass
    blob = " ".join((narration, camera, setting, shot_type)).lower()
    hero = shot_type in {"macro", "close"} or any(phrase in blob for phrase in _HERO)
    generic = any(cue in blob for cue in _GENERIC)
    if generic and not hero:
        return "flux"
    if entity.kind in {"artifact", "artefact", "place", "site"}:
        return "photo"
    return "flux"


_EVENT = (
    "shipwreck",
    "wreck",
    "diver",
    "divers",
    "sponge",
    "excavation",
    "excavated",
    "discovery",
    "discovered",
    "recovered",
    "recover",
)


def is_event(
    narration: str = "",
    *,
    camera: str = "",
    setting: str = "",
) -> bool:
    """A historical event, not a portrait of the artefact."""
    blob = " ".join((narration, camera, setting)).lower()
    return any(cue in blob for cue in _EVENT)


def beat_kind(
    entity: Entity | None,
    *,
    shot_type: str = "",
    narration: str = "",
    camera: str = "",
    setting: str = "",
    is_cta: bool = False,
) -> str:
    """``object`` only when the still is the artefact itself. Otherwise ``scene``.

    Divers, a shipwreck, the sea, a workshop, a landscape, and commentary
    are scenes. They take a documentary Flux frame, not a photo of the object.
    """
    if is_cta:
        return "scene"
    try:
        from production.formats.economic_reel.artefact.flux_policy import (
            is_abstract_claim,
            is_physical_object_line,
        )

        if is_abstract_claim(narration) and not is_physical_object_line(narration):
            return "scene"
    except Exception:  # noqa: BLE001
        pass
    blob = " ".join((narration, camera, setting, shot_type)).lower()
    if any(cue in blob for cue in _SCENE):
        return "scene"
    try:
        from production.formats.economic_reel.shot_planner.entities import is_commentary

        if is_commentary(narration):
            return "scene"
    except Exception:  # noqa: BLE001
        pass
    if entity is None:
        return "scene"
    if shot_type in {"macro", "close", "detail"} or any(cue in blob for cue in _OBJECT):
        return "object"
    name = (entity.name or "").lower()
    if name and name in blob and entity.kind in {"artifact", "artefact"}:
        return "object"
    if entity.kind in {"place", "site"}:
        return "scene"
    if entity.kind in {"artifact", "artefact"} and not any(cue in blob for cue in _SCENE):
        # A named object with no place or people cue stays on the photograph.
        if shot_type in {"macro", "close", "detail", "hero"}:
            return "object"
    return "scene"
