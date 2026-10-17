# -*- coding: utf-8 -*-
"""Episode visual brief: the object, its materials, and the find-spot."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Physical things a still can show. Bare "stone" is omitted on purpose so a
# megalith episode stays a site, not a fake object.
_HERO_OBJECTS: tuple[str, ...] = (
    "hammer", "handle", "mechanism", "gear", "tablet", "skull", "battery",
    "statue", "mask", "sword", "coin", "manuscript", "scroll", "compass",
    "crystal", "obelisk", "monolith", "fossil", "concretion", "shipwreck",
    "wreck", "dial", "inscription", "glyph", "carving", "artefact", "artifact",
    "relic", "blade", "spear", "pottery", "vessel", "jar", "necklace", "crown",
    "sarcophagus", "mummy", "papyrus", "device", "clockwork", "figurine",
    "idol", "stela", "stele", "codex", "disc", "craft", "computer", "tool",
)

_MATERIALS: tuple[str, ...] = (
    "wooden", "wood", "iron", "bronze", "copper", "gold", "metal", "limestone",
    "fossil", "bone", "clay", "ceramic", "quartz", "marble", "obsidian",
)

_PART_CUES: tuple[str, ...] = (
    "handle", "wood grain", "wooden", "grain", "fibre", "fiber", "patina",
    "corrosion", "encrust", "preservation", "preserved",
)

_LAB_CUES: tuple[str, ...] = (
    "metallurg", "microscope", "laboratory", "spectrometer", "isotope",
    "analysis", "analys", "cross-section", "cross section",
)

_MONUMENT_CUES: tuple[str, ...] = (
    "pyramid", "temple", "ruins", "megalith", "ziggurat", "stonehenge",
    "baalbek", "obelisk", "sphinx",
)

_ABSTRACT: tuple[str, ...] = (
    "historian", "debate", "theory", "researcher", "believe", "mainstream",
    "claim", "mystery", "secret", "narrative", "suppress", "enigma",
    "establish", "origin", "knowledge", "science", "scholar",
)

# A famous city name plus a different region is a find-spot, not that city.
_CITY_HOME: dict[str, frozenset[str]] = {
    "london": frozenset({"england", "britain", "uk", "united"}),
    "paris": frozenset({"france"}),
    "rome": frozenset({"italy", "roman"}),
    "athens": frozenset({"greece", "greek"}),
    "cairo": frozenset({"egypt", "egyptian"}),
    "delhi": frozenset({"india", "indian"}),
    "venice": frozenset({"italy", "italian"}),
    "florence": frozenset({"italy", "italian"}),
    "alexandria": frozenset({"egypt", "egyptian"}),
    "troy": frozenset({"turkey", "anatolia"}),
    "thebes": frozenset({"egypt", "egyptian", "greece", "greek"}),
    "memphis": frozenset({"egypt", "egyptian"}),
    "babylon": frozenset({"iraq", "mesopotamia"}),
}

_PLACE = re.compile(
    r"\b([A-Z][a-zA-Z'’\-]+),\s+([A-Z][a-zA-Z'’\-]+)\b"
)
_PREP_PLACE = re.compile(
    r"\b(?:at|in|near|off|from|outside)\s+"
    r"([A-Z][a-zA-Z'’\-]+(?:\s+[A-Z][a-zA-Z'’\-]+){0,2})"
    r"(?:,\s*([A-Z][a-zA-Z'’\-]+))?"
)


@dataclass
class EpisodeBrief:
    topic: str
    hero_short: str
    hero_is_object: bool
    objects: list[str] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)
    place: str = ""
    place_warning: str = ""


@dataclass
class BeatVisual:
    kind: str
    subject: str
    place_clause: str
    avoid: list[str]
    suppress_wide: bool
    suppress_fire: bool


def _hits(text: str, words: tuple[str, ...]) -> list[str]:
    low = (text or "").lower()
    found: list[str] = []
    for word in words:
        if word in found:
            continue
        if " " in word:
            if word in low:
                found.append(word)
            continue
        if re.search(rf"\b{re.escape(word)}s?\b", low):
            found.append(word)
    return found


def _clean_topic(topic: str) -> str:
    text = " ".join((topic or "").split())
    text = re.sub(r"^(the|a|an)\s+", "", text, flags=re.IGNORECASE)
    return text.strip(" .")


def find_place(*parts: str) -> tuple[str, str]:
    """Return ``(label, warning)``. Warning is set when the name is not the famous city."""
    blob = " ".join(p for p in parts if p)
    match = _PLACE.search(blob)
    city = region = ""
    if match:
        city, region = match.group(1), match.group(2)
    else:
        prep = _PREP_PLACE.search(blob)
        if prep:
            city = prep.group(1)
            region = prep.group(2) or ""
    if not city:
        return "", ""
    label = f"{city}, {region}".strip().strip(",") if region else city
    home = _CITY_HOME.get(city.lower())
    if home and region and region.lower() not in home:
        warning = (
            f"PLACE IS CONTEXT ONLY: {label}, the find-spot named in the narration. "
            f"Do not depict the famous city of {city}, and do not depict an urban "
            "alley, street, or skyline."
        )
        return label, warning
    if label:
        return label, (
            f"PLACE IS CONTEXT ONLY: {label}. Show it only when the spoken beat "
            "is about that place. Do not replace the subject with a city street."
        )
    return "", ""


def build_episode_brief(topic: str, script: str = "") -> EpisodeBrief:
    clean = _clean_topic(topic) or _clean_topic(script[:160])
    blob = f"{topic or ''} {script or ''}"
    objects = _hits(topic or "", _HERO_OBJECTS) or _hits(blob, _HERO_OBJECTS)
    materials = _hits(blob, _MATERIALS)
    if "wooden" in materials:
        materials = [item for item in materials if item != "wood"]
    place, warning = find_place(topic or "", script or "")
    hero_is_object = bool(objects) and not (
        _hits(topic or "", _MONUMENT_CUES) and not _hits(topic or "", _HERO_OBJECTS)
    )
    # A topic that is only a monument ("Great Pyramid") has no hero object.
    if _hits(topic or "", _MONUMENT_CUES) and not _hits(topic or "", (
        "hammer", "mechanism", "gear", "tablet", "skull", "battery", "device",
        "artefact", "artifact", "computer", "tool", "dial",
    )):
        hero_is_object = False
    if hero_is_object:
        mat = f" ({', '.join(materials[:4])})" if materials else ""
        noun = objects[0]
        hero = f"the {clean}, the physical {noun}{mat}"
    else:
        hero = f"the {clean}" if clean else "the subject of this episode"
    return EpisodeBrief(
        topic=clean,
        hero_short=hero,
        hero_is_object=hero_is_object,
        objects=objects,
        materials=materials,
        place=place,
        place_warning=warning,
    )


def _mentions_monument(text: str) -> bool:
    return bool(_hits(text, _MONUMENT_CUES))


def visual_for_beat(brief: EpisodeBrief, spoken: str) -> BeatVisual:
    """Concrete subject for one spoken line. The object leads; the place follows."""
    text = (spoken or "").strip()
    low = text.lower()
    part = _hits(text, _PART_CUES)
    lab = _hits(text, _LAB_CUES)
    named = _hits(text, _HERO_OBJECTS)
    monument = _mentions_monument(text)
    avoid = [
        "a city street or urban alley",
        "a fantasy landscape",
        "an unrelated monument",
    ]
    if brief.hero_is_object and not monument:
        avoid.append("stone ruins standing in for the artefact")
        avoid.append("torchlit ruins")

    place_clause = ""
    if brief.place_warning and not monument:
        place_clause = brief.place_warning
    elif brief.place:
        place_clause = (
            f"PLACE IS CONTEXT ONLY: {brief.place}. "
            "Do not replace the subject with a city street."
        )

    if brief.hero_is_object and part and not monument:
        subject = (
            f"an extreme close-up of the material this sentence names on "
            f"{brief.hero_short}. If it mentions a handle, wood, or preservation, "
            "show that wood grain and mineral crust, not stone architecture"
        )
        return BeatVisual("part", subject, place_clause, avoid, True, True)
    if brief.hero_is_object and lab and not monument:
        subject = (
            f"a documentary macro of {brief.hero_short} under examination on a "
            "plain laboratory bench: metal surface, tool marks, and the same artefact"
        )
        return BeatVisual("lab", subject, place_clause, avoid, True, True)
    if named or (brief.hero_is_object and re.search(r"\b(artefact|artifact|object|find)\b", low)):
        noun = ", ".join(named[:3]) if named else "artefact"
        subject = (
            f"{brief.hero_short}, shown large and sharp. This sentence is about "
            f"the {noun} itself, not the town it was found in"
        )
        return BeatVisual("object", subject, place_clause, avoid, True, True)
    if brief.hero_is_object and _hits(text, _ABSTRACT):
        subject = (
            f"{brief.hero_short} on a research table, large in the frame. "
            "The sentence is abstract, so show the same physical object"
        )
        return BeatVisual("abstract", subject, place_clause, avoid, True, True)
    if brief.hero_is_object:
        subject = (
            f"{brief.hero_short}, the same physical object, large in the frame"
        )
        return BeatVisual("object", subject, place_clause, avoid, True, False)
    literal = text[:180] or brief.hero_short
    subject = f"only what this sentence shows: {literal}"
    return BeatVisual("site", subject, "", [], False, False)
