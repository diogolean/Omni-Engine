# -*- coding: utf-8 -*-
"""Make a shot list obey the entity rules before it is rendered."""
from __future__ import annotations

import re

from modules.reel_artefact.flux_policy import (
    fields_for_scene,
    is_abstract_claim,
    is_physical_object_line,
    next_scene,
    object_line_strength,
    scene_fields,
)
from modules.reel_artefact.role import image_role_for, is_event
from modules.shot_planner.entities import (
    Entity,
    entities_in_text,
    entity_for_line,
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


def _fill(
    shot: Shot,
    entity: Entity | None,
    *,
    recap: bool,
    others: list[Entity],
    index: int = 1,
    force_place: bool = False,
    force_object: bool = False,
) -> Shot:
    event = is_event(shot.narration, camera=shot.camera, setting=shot.setting)
    object_line = force_object or is_physical_object_line(shot.narration)
    abstract = (
        force_place
        or is_abstract_claim(shot.narration)
        or is_abstract_close(shot.narration)
        or (
            is_commentary(shot.narration)
            and not object_line
        )
    )
    geology = entity is not None and entity.id == "coso_artifact" and any(
        cue in shot.narration.lower() for cue in _GEOLOGY
    )
    # A feature of the object is a photograph. The wreck discovery, with no
    # feature named, stays the event. Abstract lines stay a place.
    if (not geology) and not object_line and (event or abstract):
        fields = scene_fields(
            shot.narration, entity=entity, index=index, event=event,
        )
        extra = list(getattr(entity, "edit_negatives", ()) or ()) if entity else []
        negatives = _uniq(
            list(shot.negatives)
            + (list(entity.confusions) if entity else [])
            + extra
            + list(_DOC_NEGATIVES)
            + [other.name for other in others if entity is None or other.id != entity.id]
        )
        return Shot(
            id=shot.id,
            narration=shot.narration,
            entity_id=(entity.id if entity else shot.entity_id) or "unknown",
            subject=fields["subject"],
            canonical_visual=fields["canonical_visual"],
            shot_type=fields["shot_type"] if fields["shot_type"] in SHOT_TYPES else "wide",
            camera=fields["camera"],
            setting=fields["setting"],
            era=shot.era or (entity.era if entity else "") or "unspecified",
            mood=shot.mood or "quiet, investigative",
            lighting=fields["lighting"],
            negatives=negatives,
            kb_miss=entity is None,
            canonical_shape="",
            image_role="flux",
            scene_type=fields.get("scene") or "",
        )
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
            canonical_shape="",
            image_role="flux",
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
        + list(getattr(entity, "edit_negatives", ()) or ())
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
        canonical_shape=entity.canonical_shape or entity.canonical_visual,
        image_role=(
            "photo" if force_object else image_role_for(
                entity,
                shot_type=shot_type,
                narration=shot.narration,
                camera=camera,
                setting=setting,
            )
        ),
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


def normalize_shots(
    shots: list[Shot],
    entities: list[Entity],
    *,
    topic: str = "",
) -> list[Shot]:
    """Drop era-generic inventions and pin abstract closings to the main artefact.

    The knowledge-base canonical visual wins for a known entity. A geology
    line about the Coso artifact keeps that entity and shows the concretion.
    When ``topic`` is set, every shot stays on that entity unless the line
    names a different one.
    """
    by_id = {entity.id: entity for entity in entities}
    bound: list[tuple[Shot, Entity | None, bool, bool, bool]] = []
    previous: Entity | None = None
    introduced: list[Entity] = []
    # A topic string that names no entity ("Lost OOPArts") keeps the old
    # inheritance. The lock is the entity the reel is about.
    topic_hits = entities_in_text(topic, entities) if (topic or "").strip() else []
    locked = bool(topic_hits)
    for shot in shots:
        named = entities_in_text(shot.narration, entities)
        named_ent = named[0] if named else None
        era_bad = is_era_generic(shot.subject) or is_era_generic(
            shot.entity_id.replace("_", " "),
        )
        commentary = is_commentary(shot.narration)
        abstract = is_abstract_close(shot.narration) and named_ent is None
        if locked:
            entity = entity_for_line(
                shot.narration,
                entities,
                topic=topic,
                proposed_id=shot.entity_id,
            )
        elif named_ent is not None:
            entity = named_ent
        elif era_bad or abstract:
            entity = previous
        elif commentary and previous is not None:
            entity = previous
        elif shot.entity_id in by_id:
            entity = by_id[shot.entity_id]
        else:
            entity = previous
        # A line that names no entity and no object is a place, not a photo
        # of whichever artefact the model wrote into entity_id.
        force_place = bool(
            locked
            and not named
            and not is_physical_object_line(shot.narration)
        )
        inherit = abstract or era_bad or (commentary and named_ent is None)
        if entity is not None and not inherit and entity not in introduced:
            introduced.append(entity)
        if entity is not None and not inherit:
            previous = entity
        bound.append((shot, entity, abstract or commentary, abstract, force_place))
    main = introduced[0] if introduced else previous
    output: list[Shot] = []
    for index, (shot, entity, recap, pin_main, force_place) in enumerate(bound, start=1):
        if pin_main and main is not None and not locked:
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
        output.append(_fill(
            shot,
            entity,
            recap=recap,
            others=introduced,
            index=index,
            force_place=force_place,
        ))
    by_id = {entity.id: entity for entity in entities}
    output = _fit_topic(output, by_id)
    output = _balance_objects(output, by_id, locked=locked)
    output = _vary_scenes(output, by_id)
    return _fit_topic(output, by_id)


def _scene_name(shot: Shot) -> str:
    if shot.scene_type:
        return shot.scene_type
    if shot.image_role != "flux":
        return ""
    blob = f"{shot.subject} {shot.setting} {shot.canonical_visual}".lower()
    for name, words in (
        ("sea", ("calm sea", "open sea", "along the coast")),
        ("coast", ("the coast",)),
        ("aerial", ("from the air", "aerial")),
        ("detail", ("rock", "foliage", "close on the water")),
        ("golden", ("golden hour",)),
        ("fog", ("fog",)),
        ("ruins", ("ruins", "architecture")),
        ("landscape", ("landscape", "the real place")),
        ("establishing", ("establishing",)),
    ):
        if any(word in blob for word in words):
            return name
    return ""


def _with_scene(shot: Shot, scene: str, entity: Entity | None = None) -> Shot:
    fields = fields_for_scene(scene, entity)
    shot_type = fields["shot_type"] if fields["shot_type"] in SHOT_TYPES else shot.shot_type
    return Shot(
        id=shot.id,
        narration=shot.narration,
        entity_id=shot.entity_id,
        subject=fields["subject"],
        canonical_visual=fields["canonical_visual"],
        shot_type=shot_type or "wide",
        camera=fields["camera"],
        setting=fields["setting"],
        era=shot.era,
        mood=shot.mood,
        lighting=fields["lighting"],
        negatives=list(shot.negatives),
        kb_miss=shot.kb_miss,
        canonical_shape=shot.canonical_shape,
        image_role=shot.image_role or "flux",
        scene_type=fields["scene"],
    )


def _vary_scenes(shots: list[Shot], by_id: dict[str, Entity] | None = None) -> list[Shot]:
    """Consecutive Flux beats use a different place treatment."""
    known = by_id or {}
    varied: list[Shot] = []
    previous = ""
    run = 0
    for shot in shots:
        entity = known.get(shot.entity_id)
        coastal = bool(getattr(entity, "coastal", False)) if entity is not None else True
        scene = _scene_name(shot)
        event = is_event(shot.narration, camera=shot.camera, setting=shot.setting)
        if shot.image_role == "flux" and scene and scene == previous:
            run += 1
        else:
            run = 1 if scene else 0
        # Abstract lines alternate. A wreck and the divers that follow it
        # both stay on the sea.
        if (not event) and shot.image_role == "flux" and scene and run > 1:
            scene = next_scene(previous, coastal=coastal)
            shot = _with_scene(shot, scene, entity)
            run = 1
        previous = scene or previous
        if shot.image_role == "flux" and scene and not shot.scene_type:
            shot = Shot(
                id=shot.id,
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
                canonical_shape=shot.canonical_shape,
                image_role=shot.image_role,
                scene_type=scene,
            )
        varied.append(shot)
    return varied


def _fit_topic(shots: list[Shot], by_id: dict[str, Entity]) -> list[Shot]:
    """An inland culture does not get a sea scene. Landscapes name the real place."""
    fitted: list[Shot] = []
    for shot in shots:
        entity = by_id.get(shot.entity_id)
        if shot.image_role != "flux" or entity is None:
            fitted.append(shot)
            continue
        coastal = bool(getattr(entity, "coastal", False))
        scene = _scene_name(shot)
        if scene == "sea" and not coastal:
            shot = _with_scene(shot, "landscape", entity)
        elif scene in {
            "landscape", "establishing", "atmosphere", "sea",
            "aerial", "detail", "golden", "fog", "ruins", "coast",
        } and entity.geography:
            shot = _with_scene(shot, scene or "landscape", entity)
        fitted.append(shot)
    return fitted


_HARD_ABSTRACT = (
    "legend",
    "theor",
    "debate",
    "historian",
    "researcher",
    "record",
    "wonder",
    "question",
    "what if",
    "according to",
    "scholar",
    "lost knowledge",
    "lost blueprint",
    "enigma",
    "impossible",
    "commentary",
)


def _to_place(shot: Shot, entity: Entity | None, index: int) -> Shot:
    fields = scene_fields(shot.narration, entity=entity, index=index, event=False)
    shot_type = fields["shot_type"] if fields["shot_type"] in SHOT_TYPES else "wide"
    return Shot(
        id=shot.id,
        narration=shot.narration,
        entity_id=shot.entity_id,
        subject=fields["subject"],
        canonical_visual=fields["canonical_visual"],
        shot_type=shot_type,
        camera=fields["camera"],
        setting=fields["setting"],
        era=shot.era,
        mood=shot.mood,
        lighting=fields["lighting"],
        negatives=list(shot.negatives),
        kb_miss=shot.kb_miss,
        canonical_shape="",
        image_role="flux",
        scene_type=fields.get("scene") or "",
    )


def _object_supply(entity: Entity | None) -> int:
    """Real photos twice each, plus up to four Nano Banana edits twice each."""
    from modules.reel_artefact.budget import nano_cap, real_max_per_photo, reuse_max_per_still

    photos = int(getattr(entity, "usable_photos", 0) or 0) if entity is not None else 0
    return photos * real_max_per_photo() + nano_cap() * reuse_max_per_still()


def _promotable(shot: Shot) -> bool:
    """A line that still refers to the object, and is not pure atmosphere."""
    if shot.image_role == "photo" or is_event(shot.narration):
        return False
    low = " ".join(shot.narration.lower().split())
    if any(cue in low for cue in _HARD_ABSTRACT):
        return False
    return bool(re.search(r"\b(its|their|these)\b", low))


def _balance_objects(
    shots: list[Shot],
    by_id: dict[str, Entity],
    *,
    locked: bool,
) -> list[Shot]:
    """Feature lines stay photographs. A locked reel aims at about 30–40%.

    Supply is the cap: each real photo twice, and four Nano Banana edits
    each reused once. Atmosphere lines are not promoted to fill the share.
    """
    balanced = list(shots)
    groups: dict[str, list[int]] = {}
    for index, shot in enumerate(balanced):
        if shot.image_role != "photo" or not shot.entity_id:
            continue
        groups.setdefault(shot.entity_id, []).append(index)
    for entity_id, indexes in groups.items():
        entity = by_id.get(entity_id)
        slots = _object_supply(entity)
        # Weakest lines lose the slot first. A named feature keeps it.
        ranked = sorted(
            indexes,
            key=lambda index: (object_line_strength(balanced[index].narration), -index),
        )
        for index in ranked[: max(0, len(ranked) - slots)]:
            balanced[index] = _to_place(balanced[index], entity, index + 1)
    if not locked or not balanced:
        return balanced
    # One topic entity. Fill toward 30% from lines that still point at it,
    # and drop only the promoted lines if that would pass 40%.
    entity = next((by_id.get(shot.entity_id) for shot in balanced if shot.entity_id in by_id), None)
    if entity is None:
        return balanced
    n = len(balanced)
    floor = max(1, round(n * 0.30))
    ceiling = max(floor, round(n * 0.40))
    slots = _object_supply(entity)

    def _photos() -> list[int]:
        return [index for index, shot in enumerate(balanced) if shot.image_role == "photo"]

    promoted: list[int] = []
    seen: set[int] = set()
    while len(_photos()) < min(floor, slots):
        candidate = next(
            (
                index for index, shot in enumerate(balanced)
                if index not in seen
                and _promotable(shot)
                and shot.entity_id == entity.id
            ),
            None,
        )
        if candidate is None:
            break
        seen.add(candidate)
        balanced[candidate] = _fill(
            balanced[candidate],
            entity,
            recap=False,
            others=[],
            index=candidate + 1,
            force_object=True,
        )
        promoted.append(candidate)
    photos = _photos()
    if len(photos) > ceiling:
        extra = [index for index in promoted if index in photos]
        for index in extra:
            if len([i for i, shot in enumerate(balanced) if shot.image_role == "photo"]) <= ceiling:
                break
            balanced[index] = _to_place(balanced[index], entity, index + 1)
    return balanced
