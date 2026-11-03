# -*- coding: utf-8 -*-
"""Flux may only draw the scene types a channel lists as safe.

The lists live in the channel RAG (``flux_scenes``) and in
``shot_planner.json``, so another channel can reuse the same rule.
A named artefact, its parts, and any specific object are never a Flux subject.
"""
from __future__ import annotations

from shared.channel_flags import coalesce_page

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
    "the sea, the coast, or underwater ambience",
    "sky, weather, fog, and golden-hour light",
    "an aerial view of the real region",
    "close natural detail: rock, water, or foliage",
    "ruins or architecture of the real region",
)
_DROPPED_SCENES = {"museum_hall", "silhouette", "manuscript"}
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
    "aerial": (
        "the real place from the air",
        "An aerial view of the real place. Ridges, valleys, and vegetation. "
        "No named artefact.",
        "high aerial, the ground fills the frame",
        "the real place seen from the air",
        "aerial",
    ),
    "detail": (
        "rock, water, or foliage",
        "A close natural detail of the real place: rock, water, or foliage. "
        "No object, no hands.",
        "close on stone, water, or leaves",
        "the ground of the real place",
        "close",
    ),
    "golden": (
        "golden hour on the real place",
        "Golden-hour light over the real place. Warm low sun, long shadows, "
        "empty of objects.",
        "wide view in golden hour",
        "the real place at golden hour",
        "wide",
    ),
    "fog": (
        "fog over the real place",
        "Fog or soft weather over the real place. Diffused light, no objects.",
        "wide view in fog",
        "the real place in fog",
        "wide",
    ),
    "coast": (
        "the coast",
        "The coast of the real place. Water, rock, and sky. No boat, no artefact.",
        "wide view of the coast",
        "the coast of the real place",
        "wide",
    ),
    "ruins": (
        "ruins of the real region",
        "Stone ruins or old architecture of the real region, seen in the "
        "landscape. No named artefact, no display case.",
        "wide view of regional ruins",
        "ruins in the real region",
        "wide",
    ),
}
_INLAND = ("landscape", "aerial", "detail", "golden", "fog", "ruins")
_COASTAL = ("landscape", "sea", "aerial", "coast", "detail", "golden", "fog", "ruins")
_OBJECT_CYCLE = _INLAND
_ABSTRACT_CYCLE = _INLAND


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


# Spoken features of the artefact. These lines are photographs, not landscapes.
_FEATURE_WORDS = (
    "gear", "gears", "cog", "cogs",
    "wing", "wings", "tailfin", "tailfins",
    "stabilizer", "stabilizers", "rudder", "rudders",
    "pendant", "pendants", "ornament", "ornaments",
    "figurine", "figurines",
    "fragment", "fragments",
    "celestial", "eclipse", "eclipses",
    "aerodynamic",
    "corroded",
    "relief", "reliefs",
    "pillar", "pillars",
    "carved", "carving", "carvings",
)
_FEATURE_PHRASES = (
    "the mechanism",
    "this mechanism",
    "tail fin",
    "tail fins",
    "delta wing",
    "golden object",
    "golden objects",
    "museum drawer",
    "in its case",
    "on display",
)
# Weaker object references. Used to fill a reel up toward the object share.
_MENTION_PHRASES = (
    "relics",
    "these shapes",
    "their forms",
    "its design",
    "precise geometry",
    "the artefact",
    "the artifact",
)


def object_line_strength(text: str) -> int:
    """2 when the line names a feature, 1 when it names the object, else 0.

    A feature wins even if the same sentence also debates or theorizes.
    ``the mechanism`` does not match the proper name ``Antikythera Mechanism``.
    """
    low = " ".join((text or "").lower().split())
    if not low:
        return 0
    if _mentions(low, _FEATURE_WORDS) or any(phrase in low for phrase in _FEATURE_PHRASES):
        return 2
    if _mentions(low, ("relic",)) or any(phrase in low for phrase in _MENTION_PHRASES):
        return 1
    if any(cue in low for cue in _PHYSICAL) and not any(cue in low for cue in _ABSTRACT_HARD):
        return 2
    return 0


def is_physical_object_line(text: str) -> bool:
    """The line names the object or a feature of it."""
    return object_line_strength(text) >= 1


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


def ak_narration_rule() -> str:
    """Spoken prose for Ancient Knowledge. No shot-list vocabulary."""
    return (
        "Write spoken documentary prose. Name the place, the object, and "
        "what the evidence shows. Do not write camera directions or a shot "
        "list: no fog rolling, no golden-hour light, no aerial views, no "
        "foliage growing, no water flowing as a shot, no rock formations as "
        "a shot, no close natural detail, and no 'ruins of the real region'. "
        "Do not speak a follow or subscribe line. That line is added later."
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
    cycle = _COASTAL if coastal else _INLAND
    if kind == "object" or is_abstract_claim(narration):
        return cycle[slot % len(cycle)]
    if _mentions(low, ("sky", "fog", "weather", "mist")):
        return "fog"
    if _mentions(low, ("golden", "dusk", "sunset")):
        return "golden"
    if _mentions(low, ("aerial", "from above", "from the air")):
        return "aerial"
    if _mentions(low, ("ruin", "ruins", "temple", "architecture")):
        return "ruins"
    if _mentions(low, ("landscape", "region", "place", "highland")):
        return "landscape"
    return cycle[slot % len(cycle)]


def _coastal(entity: Any) -> bool:
    """Unknown entities keep the old sea scenes. A known inland find does not."""
    if entity is None:
        return True
    return bool(getattr(entity, "coastal", False))


def _arid_place(geography: str) -> bool:
    low = (geography or "").lower()
    tokens = (
        "arid", "dry limestone", "no lake", "desert plateau", "no river",
    )
    return any(token in low for token in tokens)


def _sand_desert(geography: str) -> bool:
    """True when the region template is itself dunes or a sand desert."""
    low = (geography or "").lower()
    if any(token in low for token in ("dune", "sahara", "erg", "sand sea")):
        return True
    return "desert" in low and "not a desert" not in low


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
    dry = _arid_place(place)
    dry_line = " Dry ground only. No lake, no river, no sea." if dry else ""
    if scene == "landscape":
        return (
            place,
            f"A wide landscape of {place}. The real ground, ridges, and vegetation. "
            f"No named artefact.{dry_line}",
            place,
        )
    if scene == "establishing":
        return (
            place,
            f"A wide establishing shot of {place}. The location is the subject.{dry_line}",
            place,
        )
    if scene == "atmosphere":
        return (
            place,
            f"Sky, light, or fog over {place}. Empty of objects.{dry_line}",
            place,
        )
    if scene == "sea":
        return (
            place,
            f"Calm sea along {place}. Light in the water, uncluttered.",
            place,
        )
    if scene == "aerial":
        return (
            place,
            f"An aerial view of {place}. Ridges, valleys, and vegetation. "
            f"No named artefact.{dry_line}",
            place,
        )
    if scene == "detail":
        detail = (
            "dry limestone, dust, and stone. No water, no object, no hands."
            if dry
            else "rock, water, or foliage. No object, no hands."
        )
        return (
            place,
            f"A close natural detail of {place}: {detail}{dry_line}",
            place,
        )
    if scene == "golden":
        return (
            place,
            f"Golden-hour light over {place}. Warm low sun, long shadows, "
            f"empty of objects.{dry_line}",
            place,
        )
    if scene == "fog":
        return (
            place,
            f"Fog or soft weather over {place}. Diffused light, no objects.{dry_line}",
            place,
        )
    if scene == "coast":
        return (
            place,
            f"The coast of {place}. Water, rock, and sky. No boat, no artefact.",
            place,
        )
    if scene == "ruins":
        return (
            place,
            f"Stone ruins or old architecture of {place}, in the landscape. "
            f"No named artefact.{dry_line}",
            place,
        )
    return subject, visual, setting


def _cycle(coastal: bool) -> tuple[str, ...]:
    return _COASTAL if coastal else _INLAND


def fields_for_scene(scene: str, entity: Any = None) -> dict[str, str]:
    """Subject, camera, and setting for one allowed Flux scene type."""
    coastal = _coastal(entity)
    key = scene if scene in _SCENES else "landscape"
    off_cycle = key not in _cycle(coastal) and key not in {"establishing", "atmosphere"}
    if key in _DROPPED_SCENES or off_cycle:
        key = _cycle(coastal)[0]
    if key == "sea" and not coastal:
        key = "landscape"
    if key == "coast" and not coastal:
        key = "landscape"
    subject, visual, camera, setting, shot_type = _SCENES[key]
    geography = str(getattr(entity, "geography", "") or "") if entity is not None else ""
    subject, visual, setting = _place_copy(key, subject, visual, setting, geography)
    if key == "golden":
        light = "golden hour, warm low sun"
    elif key == "fog":
        light = "fog, soft diffused light"
    else:
        light = "natural daylight, documentary"
    return {
        "scene": key,
        "subject": subject,
        "canonical_visual": visual,
        "shot_type": shot_type,
        "camera": camera,
        "setting": setting,
        "lighting": light,
    }


def next_scene(scene: str, *, coastal: bool = True) -> str:
    """The next place treatment. Wide, aerial, detail, light, then ruins."""
    order = list(_cycle(coastal))
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
    elif any(
        word in low
        for word in ("historian", "debate", "archive", "researcher", "theory", "legend")
    ):
        scene = _cycle(coastal)[(max(1, int(index)) - 1) % len(_cycle(coastal))]
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
    chosen = scene if scene in _SCENES and scene not in _DROPPED_SCENES else choose_scene(
        narration, attempt=attempt, event=event, kind=kind, coastal=coastal,
    )
    if chosen in _DROPPED_SCENES:
        chosen = choose_scene(
            narration, attempt=attempt, event=event, kind=kind, coastal=coastal,
        )
    if chosen in {"sea", "coast"} and not coastal:
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
    if (geography or "").strip() and not _sand_desert(geography):
        bans.append("sand dunes, mesas, Sahara")
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
        from production.qa.judges.visual_critic.channel_rag import get_channel_rules

        rules = get_channel_rules(channel or "")
    except Exception:  # noqa: BLE001
        return [], []
    return _pair(rules.get("flux_scenes") if isinstance(rules, dict) else None)


def _from_planner_config(channel: str) -> tuple[list[str], list[str]]:
    try:
        from production.formats.economic_reel.shot_planner.entities import load_channel_config

        config = load_channel_config(coalesce_page(channel, "DOCUMENTARY_PIPELINE"))
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
