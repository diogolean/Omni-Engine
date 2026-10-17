# -*- coding: utf-8 -*-
"""Episode visual brief: the object, its materials, and the find-spot."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

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
    object_label: str = ""
    other_objects: list[str] = field(default_factory=list)


# "Coso artifact", "Klerksdorp spheres", "London Hammer". The noun is the
# thing a still can show. A bare topic like "Lost OOPArts" does not match.
_PROPER_OBJECT = re.compile(
    r"\b([A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+){0,3})\s+"
    r"((?i:spark\s+plugs?|artifacts?|artefacts?|spheres?|hammers?|mechanisms?|"
    r"batter(?:y|ies)|skulls?|tablets?|devices?|geodes?|concretions?|"
    r"figurines?|idols?))\b"
)
_GEOLOGY = (
    "geological", "natural formation", "concretion", "geode", "mineral",
    "erosion", "sediment",
)
_THEORY = (
    "believe", "skeptic", "hoax", "forgery", "theory", "debate", "historian",
    "researcher", "mainstream", "claim", "scholar",
)
# A vague subject let Flux invent these on the Lost OOPArts smoke: a cannon
# for "geological formation", a cyberpunk figure for a technology line, and a
# foggy monument for a beat that started mid-sentence. They are not in the
# channel style block. The object leads the prompt; these stay in DO NOT DEPICT.
_DRIFT = (
    "a cannon, artillery piece, or firearm",
    "a cyberpunk character, neon costume, or science-fiction figure",
    "a statue or idol standing in for the artefact",
    "foggy monument or temple architecture standing in for a small object",
    "a city street or urban alley",
    "a fantasy landscape",
)


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


# A capitalised determiner is not the name of an object. "This artefact"
# must not beat "the London Hammer".
_NAME_STOP = frozenset({
    "the", "a", "an", "this", "that", "these", "those", "some", "it", "its",
    "people", "when", "what", "inside", "after", "before", "there", "here",
    "then", "and", "but", "in", "on", "of", "for", "with", "from", "near",
    "off", "his", "her", "their", "our", "your", "my", "such", "many",
    "several", "another", "other", "they", "one", "into", "over",
})


def _bare_name(name: str) -> str:
    parts = (name or "").split()
    while parts and parts[0].lower() in _NAME_STOP:
        parts.pop(0)
    return " ".join(parts)


def named_objects(text: str) -> list[str]:
    """Proper-name objects in the order they appear. ``Coso artifact`` stays intact."""
    found: list[str] = []
    seen: set[str] = set()
    for match in _PROPER_OBJECT.finditer(text or ""):
        name = _bare_name(match.group(1))
        if not name:
            continue
        label = f"{name} {match.group(2)}"
        key = " ".join(label.lower().split())
        if key in seen:
            continue
        seen.add(key)
        found.append(label)
    return found


def _starts_as_fragment(text: str) -> bool:
    """True when the beat begins with the tail of the previous sentence."""
    raw = " ".join((text or "").split())
    if not raw:
        return False
    first = raw.split()[0].strip("\"'")
    if first[:1].islower() and first.lower() not in {"a", "i"}:
        return True
    return bool(re.match(r"^[a-z][^.]{0,40}\.\s+\S", raw))


def _bare(label: str) -> str:
    text = " ".join((label or "").split())
    return re.sub(r"^(?:the|a|an)\s+", "", text, flags=re.IGNORECASE).strip()


def _object_before(script: str, spoken: str) -> str:
    """Last named object that appears before this line in the script.

    A line that is not in the script returns nothing, so a later object
    (the Klerksdorp spheres) cannot steal a beat about the earlier one.
    """
    blob = script or ""
    needle = " ".join((spoken or "").split())
    if len(needle) < 12:
        return ""
    at = blob.lower().find(needle.lower()[:40])
    if at < 0:
        return ""
    found = named_objects(blob[:at])
    return found[-1] if found else ""


def _with_article(label: str) -> str:
    text = _bare(label)
    if not text:
        return ""
    return f"the {text}"


def build_episode_brief(topic: str, script: str = "") -> EpisodeBrief:
    clean = _clean_topic(topic) or _clean_topic(script[:160])
    blob = f"{topic or ''} {script or ''}"
    catalog = named_objects(f"{topic or ''}\n{script or ''}")
    materials = _hits(blob, _MATERIALS)
    if "wooden" in materials:
        materials = [item for item in materials if item != "wood"]
    place, warning = find_place(topic or "", script or "")
    monument_topic = bool(_hits(topic or "", _MONUMENT_CUES)) and not catalog
    hero_is_object = bool(catalog) and not monument_topic
    if hero_is_object:
        mat = f" ({', '.join(materials[:4])})" if materials else ""
        hero = f"{_with_article(catalog[0])}{mat}"
    else:
        hero = f"the {clean}" if clean else "the subject of this episode"
    return EpisodeBrief(
        topic=clean,
        hero_short=hero,
        hero_is_object=hero_is_object,
        objects=catalog,
        materials=materials,
        place=place,
        place_warning=warning,
    )


def _mentions_monument(text: str) -> bool:
    return bool(_hits(text, _MONUMENT_CUES))


def resolve_beat_object(
    brief: EpisodeBrief,
    spoken: str,
    *,
    script: str = "",
    previous_spoken: str = "",
    next_spoken: str = "",
    locked_object: str = "",
) -> str:
    """The physical thing this one beat should show.

    A beat that names the Coso artifact does not inherit the Klerksdorp
    spheres, and a theory line that names neither keeps the object mentioned
    just before it. A mid-sentence fragment borrows the neighbour.
    """
    here = named_objects(spoken)
    if here:
        return here[-1] if _starts_as_fragment(spoken) else here[0]
    locked = _bare(locked_object)
    if locked:
        return locked
    if _starts_as_fragment(spoken):
        previous = named_objects(previous_spoken)
        if previous:
            return previous[-1]
        following = named_objects(next_spoken)
        if following:
            return following[0]
    prior = _object_before(script, spoken)
    if prior:
        return prior
    # A theory line that never names the object keeps the neighbour's object
    # when the line cannot be placed in the script.
    previous = named_objects(previous_spoken)
    if previous:
        return previous[-1]
    following = named_objects(next_spoken)
    if following:
        return following[0]
    if len(brief.objects) == 1:
        return brief.objects[0]
    if brief.hero_is_object and brief.objects:
        return brief.objects[0]
    return ""


def _beat_visual(
    kind: str,
    subject: str,
    place_clause: str,
    avoid: list[str],
    suppress_wide: bool,
    suppress_fire: bool,
    label: str,
    others: list[str],
) -> BeatVisual:
    return BeatVisual(
        kind, subject, place_clause, avoid, suppress_wide, suppress_fire,
        object_label=_with_article(label) if label else "",
        other_objects=[_with_article(item) for item in others if item != label],
    )


def visual_for_beat(
    brief: EpisodeBrief,
    spoken: str,
    *,
    script: str = "",
    previous_spoken: str = "",
    next_spoken: str = "",
    locked_object: str = "",
) -> BeatVisual:
    """Concrete subject for one spoken line. The object leads; the place follows."""
    text = (spoken or "").strip()
    low = text.lower()
    label = resolve_beat_object(
        brief,
        text,
        script=script,
        previous_spoken=previous_spoken,
        next_spoken=next_spoken,
        locked_object=locked_object,
    )
    shown = _with_article(label) if label else brief.hero_short
    part = _hits(text, _PART_CUES)
    lab = _hits(text, _LAB_CUES)
    monument = _mentions_monument(text)
    geology = any(cue in low for cue in _GEOLOGY)
    theory = geology or any(cue in low for cue in _THEORY) or bool(_hits(text, _ABSTRACT))
    avoid: list[str] = []
    if label:
        avoid.extend(_DRIFT)
        if "statue" in shown.lower():
            avoid = [item for item in avoid if "statue" not in item]
        if not monument:
            avoid.append("stone ruins standing in for the artefact")
            avoid.append("torchlit ruins")
    others = [
        item for item in brief.objects
        if _bare(item).lower() != _bare(label).lower()
    ]

    place_clause = ""
    # The find-spot belongs to the beat that names it. California must not
    # follow the Klerksdorp spheres into their frame.
    place_name = brief.place.split(",")[0].strip() if brief.place else ""
    spoken_names_place = bool(
        place_name and re.search(rf"\b{re.escape(place_name)}\b", text, re.IGNORECASE)
    )
    if spoken_names_place and brief.place_warning and not monument:
        place_clause = brief.place_warning
    elif spoken_names_place and brief.place:
        place_clause = (
            f"PLACE IS CONTEXT ONLY: {brief.place}. "
            "Do not replace the subject with a city street."
        )

    if label and part and not monument:
        subject = (
            f"an extreme close-up of the material this sentence names on "
            f"{shown}. If it mentions a handle, wood, or preservation, "
            "show that wood grain and mineral crust, not stone architecture"
        )
        return _beat_visual("part", subject, place_clause, avoid, True, True, label, others)
    if label and lab and not monument:
        subject = (
            f"a documentary macro of {shown} under examination on a "
            "plain laboratory bench: metal surface, tool marks, and the same artefact"
        )
        return _beat_visual("lab", subject, place_clause, avoid, True, True, label, others)
    if label and geology:
        subject = (
            f"a documentary close-up of {shown} as a natural rock concretion "
            "or geode cross-section. The same object fills the frame. "
            "This is the sceptical reading of that same object"
        )
        return _beat_visual("theory", subject, place_clause, avoid, True, True, label, others)
    if label and theory:
        subject = (
            f"{shown} large on a research table. The sentence is a theory "
            "about this object, so the object stays the picture"
        )
        return _beat_visual(
            "abstract", subject, place_clause, avoid, True, True, label, others,
        )
    if label:
        subject = (
            f"{shown}, shown large and sharp, filling the frame. "
            "The picture is that object"
        )
        return _beat_visual("object", subject, place_clause, avoid, True, True, label, others)
    literal = text[:180] or brief.hero_short
    subject = f"only what this sentence shows: {literal}"
    return BeatVisual("site", subject, "", avoid, False, False)
