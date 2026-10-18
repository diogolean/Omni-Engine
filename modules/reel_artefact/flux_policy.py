# -*- coding: utf-8 -*-
"""Flux may only draw the scene types a channel lists as safe.

The lists live in the channel RAG (``flux_scenes``) and in
``shot_planner.json``, so another channel can reuse the same rule.
A named artefact, its parts, and any specific object are never a Flux subject.
"""
from __future__ import annotations

import re
from typing import Any

_LOOK = (
    "Photoreal documentary photograph from a real camera, 35mm film grain, "
    "natural colour, real location. Simple calm composition. "
    "Not a render, not CGI, not an illustration."
)
_FORBID = (
    "Do not depict a named artefact, its parts, or any specific object. "
    "No gears, no pendants, no complete mechanism, no clean brass gears, "
    "no invented gold figurine, no gold bird, no silver disc. "
    "No readable text. No detailed faces."
)

# Used when a channel has not copied the rule into its style guide yet.
_ALLOWED = (
    "landscapes and the real place",
    "the sea or underwater ambience",
    "atmosphere such as sky, light, or fog",
    "wide establishing shots",
    "generic museum halls or archives seen from afar",
    "silhouettes of people and hands without detailed faces",
    "old books or manuscripts as props without readable text",
)
_FORBIDDEN = (
    "a named artefact",
    "parts of an artefact such as gears or pendants",
    "any specific object",
    "a complete mechanism",
    "clean brass gears",
    "an invented gold figurine, gold bird, or silver disc",
    "readable text",
    "detailed faces",
)

_ABSTRACT = (
    "theor",
    "legend",
    "ancient record",
    "record",
    "real place",
    "lost knowledge",
    "lost blueprint",
    "blueprint",
    "clockmaking",
    "cicero",
    "some researchers",
    "researchers believe",
    "scholars disagree",
    "historian",
    "mainstream",
    "according to",
    "debate",
    "debating",
    "profound question",
    "some believe",
    "commentary",
)
_PHYSICAL = (
    "fragment",
    "gear",
    "pendant",
    "museum drawer",
    "in its case",
    "corroded",
    "on display",
)
_ABSTRACT_HARD = (
    "legend",
    "theor",
    "blueprint",
    "clockmaking",
    "cicero",
    "record",
)

# id, subject, visual, camera, setting, shot type
_SCENES: dict[str, tuple[str, str, str, str, str]] = {
    "landscape": (
        "the real place",
        "A wide landscape of the real place under open sky.",
        "wide documentary view of the landscape",
        "the landscape of the place",
        "wide",
    ),
    "sea": (
        "calm sea",
        "Calm sea or underwater ambience, light in the water, uncluttered.",
        "wide view of the sea, no faces",
        "open sea",
        "wide",
    ),
    "atmosphere": (
        "sky and light",
        "Sky, light, or fog over the place. Empty of objects.",
        "wide atmospheric view",
        "open sky",
        "wide",
    ),
    "establishing": (
        "a wide establishing shot",
        "A wide establishing shot of the place. The location is the subject.",
        "wide establishing shot",
        "the place seen from a distance",
        "wide",
    ),
    "museum_hall": (
        "a museum archive seen from afar",
        "A generic museum hall or archive seen from across the room. "
        "Cases are small and unreadable.",
        "wide view from across the hall",
        "a museum archive seen from across the hall",
        "wide",
    ),
    "silhouette": (
        "hands without a detailed face",
        "Silhouettes of people, or hands only, with no detailed faces.",
        "hands and silhouettes, faces not visible",
        "a work table, faces out of frame",
        "medium",
    ),
    "manuscript": (
        "an old manuscript on a desk",
        "An old book or manuscript used as a prop on a wooden desk. "
        "The pages have no readable text.",
        "medium view of a closed or blurred book",
        "a wooden desk",
        "medium",
    ),
}
_OBJECT_CYCLE = ("museum_hall", "landscape", "establishing", "manuscript")
_ABSTRACT_CYCLE = ("manuscript", "silhouette", "museum_hall", "landscape")


def _mentions(text: str, words: tuple[str, ...]) -> bool:
    """Match a cue as a word, so ``researchers`` is not the sea."""
    for word in words:
        if len(word) >= 6:
            if word in text:
                return True
            continue
        if re.search(rf"\b{re.escape(word)}\b", text):
            return True
    return False


def is_abstract_claim(text: str) -> bool:
    """A theory, legend, record, or debate that names no filmable object."""
    low = " ".join((text or "").lower().split())
    if not low:
        return False
    return any(cue in low for cue in _ABSTRACT)


def is_physical_object_line(text: str) -> bool:
    """The line is about the object itself, not a story about it."""
    low = " ".join((text or "").lower().split())
    if not any(cue in low for cue in _PHYSICAL):
        return False
    return not any(cue in low for cue in _ABSTRACT_HARD)


def script_filmable_rule() -> str:
    """One instruction the scriptwriter and the planner both follow."""
    allowed = "; ".join(_ALLOWED)
    return (
        "Each spoken line must pair with one concrete picture Flux can paint, "
        "or with a real photograph of the object. Abstract claims (theories, "
        "legends, records, lost knowledge) use one of these and nothing else: "
        f"{allowed}. Never ask for a named artefact, its gears or pendants, "
        "or any specific object in a generated still."
    )


def load_flux_policy(channel: str) -> dict[str, tuple[str, ...]]:
    """Allowed and forbidden Flux scene types for ``channel``.

    The RAG style guide wins. ``shot_planner.json`` is the channel-config
    copy. The built-in list is the last resort, and it is the same rule.
    """
    allowed, forbidden = _from_rag(channel)
    if not allowed:
        allowed, forbidden = _from_planner_config(channel)
    return {
        "allowed": tuple(allowed or _ALLOWED),
        "forbidden": tuple(forbidden or _FORBIDDEN),
    }


def choose_scene(
    narration: str,
    *,
    attempt: int = 1,
    event: bool = False,
    kind: str = "scene",
    coastal: bool = True,
) -> str:
    """One allowed scene id. Object beats re-plan onto a context scene.

    ``coastal`` is false for an inland find. Quimbaya does not get a sea.
    """
    if event or kind == "event":
        return "sea" if coastal else "landscape"
    low = " ".join((narration or "").lower().split())
    if _mentions(low, ("sea", "underwater", "shipwreck", "diver", "divers", "wreck")):
        return "sea" if coastal else "landscape"
    slot = max(1, int(attempt)) - 1
    if kind == "object":
        return _OBJECT_CYCLE[slot % len(_OBJECT_CYCLE)]
    if is_abstract_claim(narration):
        return _ABSTRACT_CYCLE[slot % len(_ABSTRACT_CYCLE)]
    if _mentions(low, ("sky", "fog", "light", "dusk", "atmosphere")):
        return "atmosphere"
    if _mentions(low, ("landscape", "region", "map")):
        return "landscape"
    return ("establishing", "landscape", "museum_hall", "silhouette")[slot % 4]


def _coastal(entity: Any) -> bool:
    """Unknown entities keep the old sea scenes. A known inland find does not."""
    if entity is None:
        return True
    return bool(getattr(entity, "coastal", False))


def _place_copy(
    scene: str,
    subject: str,
    visual: str,
    setting: str,
    geography: str,
) -> tuple[str, str, str]:
    """Name the real geography. A bare 'real place' is what drew the wrong desert."""
    place = " ".join((geography or "").split())
    if not place:
        return subject, visual, setting
    if scene == "landscape":
        return (
            place,
            f"A wide landscape of {place}. The real ground, ridges, and vegetation. No named artefact.",
            place,
        )
    if scene == "establishing":
        return (
            place,
            f"A wide establishing shot of {place}. The location is the subject.",
            place,
        )
    if scene == "atmosphere":
        return (
            place,
            f"Sky, light, or fog over {place}. Empty of objects.",
            place,
        )
    if scene == "sea":
        return (
            place,
            f"Calm sea along {place}. Light in the water, uncluttered.",
            place,
        )
    return subject, visual, setting


def fields_for_scene(scene: str, entity: Any = None) -> dict[str, str]:
    """Subject, camera, and setting for one allowed Flux scene type."""
    key = scene if scene in _SCENES else "landscape"
    if key == "sea" and not _coastal(entity):
        key = "landscape"
    subject, visual, camera, setting, shot_type = _SCENES[key]
    geography = str(getattr(entity, "geography", "") or "") if entity is not None else ""
    subject, visual, setting = _place_copy(key, subject, visual, setting, geography)
    return {
        "scene": key,
        "subject": subject,
        "canonical_visual": visual,
        "shot_type": shot_type,
        "camera": camera,
        "setting": setting,
        "lighting": "natural daylight, documentary",
    }


def next_scene(scene: str, *, coastal: bool = True) -> str:
    """A different allowed scene, used when two beats already used this one."""
    order = ["manuscript", "silhouette", "museum_hall", "landscape", "establishing", "atmosphere"]
    if coastal:
        order = [
            "manuscript", "silhouette", "museum_hall", "landscape",
            "sea", "establishing", "atmosphere",
        ]
    if scene not in order:
        return order[0]
    return order[(order.index(scene) + 1) % len(order)]


def scene_fields(
    narration: str,
    *,
    entity: Any = None,
    index: int = 1,
    event: bool = False,
) -> dict[str, str]:
    """Planner fields for a line that must not ask Flux to invent an object."""
    low = " ".join((narration or "").lower().split())
    coastal = _coastal(entity)
    if event:
        scene = "sea" if coastal else "landscape"
    elif any(word in low for word in ("real place", "highland", "landscape", "region")):
        scene = "landscape"
    elif any(word in low for word in ("historian", "debate", "archive", "researcher")):
        scene = "museum_hall"
    elif (
        entity is not None
        and str(getattr(entity, "kind", "") or "") in {"place", "site"}
    ):
        place = str(getattr(entity, "setting", "") or "the real place")
        return {
            "scene": "landscape",
            "subject": "the real place",
            "canonical_visual": (
                f"A wide landscape of {place}. No named artefact in the frame."
            ),
            "shot_type": "wide",
            "camera": "wide establishing shot of the real place",
            "setting": place,
            "lighting": "natural daylight, documentary",
        }
    else:
        scene = choose_scene(
            narration, attempt=index, event=event, kind="scene", coastal=coastal,
        )
    if scene == "sea" and not coastal:
        scene = "landscape"
    return fields_for_scene(scene, entity)


_PART_WORDS = (
    "gear",
    "gears",
    "gearwheel",
    "gear wheel",
    "pendant",
    "pendants",
    "ornament",
    "ornaments",
    "figurine",
    "figurines",
    "mechanism",
    "fragment",
    "fragments",
    "gold bird",
    "silver disc",
    "brass dial",
)


def acceptable_flux_concept(
    text: str,
    *,
    name: str = "",
    aliases: tuple[str, ...] | list[str] = (),
) -> bool:
    """False when a re-plan names the entity, a part, or a specific object.

    Ban lines (``do not depict``, ``no gears``) are ignored. The scene
    description that remains has to stay inside the Flux policy.
    """
    kept = _without_bans(text)
    if not kept:
        return False
    needles = [str(name or "").strip().lower(), *[str(item).strip().lower() for item in aliases]]
    needles.extend(_PART_WORDS)
    for needle in needles:
        if len(needle) < 3:
            continue
        if needle in kept:
            return False
    return True


def _without_bans(text: str) -> str:
    low = " ".join((text or "").lower().split())
    return re.sub(
        r"\b(?:do not depict|don't depict|never depict|no|without|not)\b[^.]{0,140}",
        " ",
        low,
    ).strip()


def safe_flux_prompt(
    narration: str,
    *,
    name: str = "",
    negatives: tuple[str, ...] | list[str] = (),
    period: str = "",
    attempt: int = 1,
    event: bool = False,
    kind: str = "scene",
    rejection: str = "",
    scene: str = "",
    geography: str = "",
    coastal: bool = True,
    variant: int = 0,
) -> str:
    """One Flux prompt. The subject is always an allowed scene type.

    ``scene`` is the planned scene. ``variant`` walks to the next allowed
    scene on a retry. ``geography`` is the real place from the entity file.
    """
    chosen = scene if scene in _SCENES else choose_scene(
        narration, attempt=attempt, event=event, kind=kind, coastal=coastal,
    )
    if chosen == "sea" and not coastal:
        chosen = "landscape"
    for _step in range(max(0, int(variant or 0))):
        chosen = next_scene(chosen, coastal=coastal)
    _subject, visual, _camera, _setting, _shot = _SCENES[chosen]
    _subject, visual, _setting = _place_copy(chosen, _subject, visual, _setting, geography)
    spoken = " ".join((narration or "").split())
    named = f" Do not depict {name}." if name else ""
    when = f" Period: {period}." if period and (event or chosen == "sea") else ""
    bans = [
        str(item).strip().rstrip(".")
        for item in negatives
        if str(item).strip()
    ]
    if event or chosen == "sea":
        if not any("scuba" in item.lower() for item in bans):
            bans.append("no modern scuba")
        if not any("boat" in item.lower() for item in bans):
            bans.append("no modern boats")
    ban_line = (", ".join(bans) + ".") if bans else ""
    reason = " ".join((rejection or "").split())
    rejected = f" The previous still was rejected: {reason}." if reason else ""
    context = ""
    if spoken:
        context = " The spoken line is context only. Do not illustrate its objects."
    return " ".join(
        (
            f"EXPECTED SUBJECT: {_subject}. {_LOOK} {visual}{named}{when} "
            f"{ban_line} {_FORBID}{context}{rejected}"
        ).split()
    )


def _from_rag(channel: str) -> tuple[list[str], list[str]]:
    try:
        from quality.VisualQA_Agent.channel_rag import get_channel_rules

        rules = get_channel_rules(channel or "")
    except Exception:  # noqa: BLE001
        return [], []
    return _pair(rules.get("flux_scenes") if isinstance(rules, dict) else None)


def _from_planner_config(channel: str) -> tuple[list[str], list[str]]:
    try:
        from modules.shot_planner.entities import load_channel_config

        config = load_channel_config(channel or "ancient_knowledge")
    except Exception:  # noqa: BLE001
        return [], []
    return _pair(config.get("flux_scenes") if isinstance(config, dict) else None)


def _pair(raw: Any) -> tuple[list[str], list[str]]:
    if not isinstance(raw, dict):
        return [], []
    allowed = [str(item).strip() for item in (raw.get("allowed") or []) if str(item).strip()]
    forbidden = [
        str(item).strip() for item in (raw.get("forbidden") or []) if str(item).strip()
    ]
    return allowed, forbidden
