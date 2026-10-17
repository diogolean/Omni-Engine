# -*- coding: utf-8 -*-
"""Make a shot list obey the entity rules before it is rendered."""
from __future__ import annotations

from modules.shot_planner.entities import (
    Entity,
    entities_in_text,
    is_abstract_close,
    is_commentary,
    is_era_generic,
)
from modules.shot_planner.schema import SHOT_TYPES, Shot

_GEOLOGY = ("geological", "natural formation", "concretion", "geode cross")
_BAD_SCENE = (
    "sci-fi", "science fiction", "steampunk", "futuristic", "spaceship",
    "hangar", "fighter jet", "cyberpunk", "modern library", "bookshelf",
    "bookshelves", "reading lamp", "miniature", "clay model",
)
_DOC_NEGATIVES = (
    "science fiction",
    "steampunk",
    "futuristic interior",
    "spaceship",
    "fighter jet",
    "modern aircraft",
    "hangar",
    "modern library",
    "CGI",
    "miniature model",
    "clay diorama",
    "illustration",
)


def _uniq(items: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = " ".join(str(item).split())
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        found.append(text)
    return found


def _ungrounded(shot: Shot) -> bool:
    blob = " ".join((shot.setting, shot.camera, shot.canonical_visual)).lower()
    return any(term in blob for term in _BAD_SCENE)


def _documentary_frame(entity: Entity, *, recap: bool) -> tuple[str, str, str, str]:
    """Camera, setting, lighting, shot type for a grounded documentary still."""
    if entity.kind == "place":
        return (
            "archival documentary photograph of the real site in open air",
            entity.setting or "the real site",
            "natural daylight, documentary",
            "wide",
        )
    if recap:
        return (
            "cinematic museum hero shot, subject fills the frame",
            "museum case",
            "single raking museum lamp",
            "close",
        )
    return (
        "documentary photograph of the artefact in a museum case, "
        "or an archaeologist examining it",
        entity.setting or "museum case",
        "single raking museum lamp",
        "close",
    )


def _fill(shot: Shot, entity: Entity | None, *, recap: bool, others: list[Entity]) -> Shot:
    if entity is None:
        subject = shot.subject
        if is_era_generic(subject):
            subject = "the episode subject"
        visual = shot.canonical_visual or (
            "a cinematic museum hero shot of the episode subject"
        )
        shot_type = shot.shot_type if shot.shot_type in SHOT_TYPES else "close"
        return Shot(
            id=shot.id,
            narration=shot.narration,
            entity_id=shot.entity_id or "unknown",
            subject=subject,
            canonical_visual=visual,
            shot_type=shot_type,
            camera=shot.camera or "cinematic museum hero shot, subject fills the frame",
            setting=shot.setting or "unspecified",
            era=shot.era or "unspecified",
            mood=shot.mood or "quiet, investigative",
            lighting=shot.lighting or "soft documentary light on the subject",
            negatives=_uniq(shot.negatives),
            kb_miss=True,
        )
    visual = entity.canonical_visual
    low = shot.narration.lower()
    if entity.id == "coso_artifact" and any(cue in low for cue in _GEOLOGY):
        visual = (
            "a rock concretion or geode cross-section of the same Coso artifact"
        )
    unsafe = _ungrounded(shot)
    if recap or unsafe:
        camera, setting, lighting, shot_type = _documentary_frame(entity, recap=recap)
    else:
        camera = shot.camera or _default_camera(entity, shot.narration)
        lighting = shot.lighting or _default_light(entity)
        shot_type = shot.shot_type if shot.shot_type in SHOT_TYPES else _default_type(
            entity, shot.narration,
        )
        setting = shot.setting or entity.setting or "the find as named in the narration"
    extra = list(_DOC_NEGATIVES) if (recap or unsafe) else []
    negatives = _uniq(
        list(shot.negatives)
        + list(entity.confusions)
        + extra
        + [other.name for other in others if other.id != entity.id]
    )
    return Shot(
        id=shot.id,
        narration=shot.narration,
        entity_id=entity.id,
        subject=entity.name,
        canonical_visual=visual,
        shot_type=shot_type,
        camera=camera,
        setting=setting,
        era=shot.era or entity.era or "unspecified",
        mood=shot.mood or "quiet, investigative",
        lighting=lighting,
        negatives=negatives,
        kb_miss=False,
    )


def _default_type(entity: Entity, narration: str) -> str:
    low = narration.lower()
    aerial = ("aerial", "from above", "from the air")
    if entity.kind == "place" and any(word in low for word in aerial):
        return "aerial"
    if entity.kind == "place":
        return "wide"
    return "macro"


def _default_camera(entity: Entity, narration: str) -> str:
    kind = _default_type(entity, narration)
    if kind == "aerial":
        return "high aerial, the subject fills the ground plane"
    if kind == "wide":
        return "wide documentary view, open sky, subject large in frame"
    return "close three-quarter, subject fills the frame"


def _default_light(entity: Entity) -> str:
    if "museum" in entity.setting.lower():
        return "single raking museum lamp"
    return "soft documentary light on the subject"


def normalize_shots(shots: list[Shot], entities: list[Entity]) -> list[Shot]:
    """Drop era-generic inventions and pin abstract closings to the main artefact.

    The knowledge-base canonical visual wins for a known entity. A geology
    line about the Coso artifact keeps that entity and shows the concretion.
    """
    by_id = {entity.id: entity for entity in entities}
    bound: list[tuple[Shot, Entity | None, bool, bool]] = []
    previous: Entity | None = None
    introduced: list[Entity] = []
    for shot in shots:
        named = entities_in_text(shot.narration, entities)
        named_ent = named[0] if named else None
        era_bad = is_era_generic(shot.subject) or is_era_generic(
            shot.entity_id.replace("_", " "),
        )
        commentary = is_commentary(shot.narration)
        abstract = is_abstract_close(shot.narration) and named_ent is None
        if named_ent is not None:
            entity = named_ent
        elif era_bad or abstract:
            entity = previous
        elif commentary and previous is not None:
            entity = previous
        elif shot.entity_id in by_id:
            entity = by_id[shot.entity_id]
        else:
            entity = previous
        inherit = abstract or era_bad or (commentary and named_ent is None)
        if entity is not None and not inherit and entity not in introduced:
            introduced.append(entity)
        if entity is not None and not inherit:
            previous = entity
        bound.append((shot, entity, abstract or commentary, abstract))
    main = introduced[0] if introduced else previous
    output: list[Shot] = []
    for index, (shot, entity, recap, pin_main) in enumerate(bound, start=1):
        if pin_main and main is not None:
            entity = main
        if not shot.id:
            shot = Shot(
                id=f"s{index:02d}",
                narration=shot.narration,
                entity_id=shot.entity_id,
                subject=shot.subject,
                canonical_visual=shot.canonical_visual,
                shot_type=shot.shot_type,
                camera=shot.camera,
                setting=shot.setting,
                era=shot.era,
                mood=shot.mood,
                lighting=shot.lighting,
                negatives=list(shot.negatives),
                kb_miss=shot.kb_miss,
            )
        output.append(_fill(shot, entity, recap=recap, others=introduced))
    return output
