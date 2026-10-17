# -*- coding: utf-8 -*-
"""Per-channel entity knowledge base and the era-word guard."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_ERA_WORDS = frozenset({
    "bce", "ce", "bc", "ad", "century", "centuries", "ancient", "antiquity",
    "medieval", "prehistoric", "classical", "hellenistic", "roman", "greek",
    "egyptian", "sumerian", "early", "late", "mid", "first", "second",
    "third", "fourth", "fifth", "1st", "2nd", "3rd", "4th", "5th", "circa",
})
_GENERIC_NOUNS = frozenset({
    "device", "devices", "artifact", "artifacts", "artefact", "artefacts",
    "object", "objects", "relic", "relics", "item", "items",
    "mechanism", "mechanisms", "thing", "things", "plug", "plugs", "spark",
})
_STOP = frozenset({
    "the", "a", "an", "this", "that", "these", "those", "of", "and",
})
_CLOSE_CUES = (
    "profound",
    "questions about",
    "our world",
    "about humanity",
    "what it means",
    "makes us wonder",
    "remains unexplained",
)
_COMMENTARY_CUES = (
    "historian",
    "debate",
    "mainstream",
    "experimental replica",
    "technological perception",
    "pre-columbian knowledge",
    "scholars disagree",
)


@dataclass(frozen=True)
class Entity:
    id: str
    name: str
    aliases: tuple[str, ...]
    kind: str
    canonical_visual: str
    era: str
    setting: str
    confusions: tuple[str, ...]
    channels: tuple[str, ...]
    canonical_shape: str = ""
    commons_search: tuple[str, ...] = ()
    commons_titles: tuple[str, ...] = ()
    commons_categories: tuple[str, ...] = ()
    # Text-only Nano Banana is allowed only when a live check showed the
    # shape survives without a reference (Baghdad Battery). Otherwise the
    # still is a reference edit, or the Commons photograph.
    text_only_safe: bool = False
    edit_negatives: tuple[str, ...] = ()
    event_search: tuple[str, ...] = ()
    event_categories: tuple[str, ...] = ()
    event_period: str = ""
    event_negatives: tuple[str, ...] = ()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return stripped.lower()


def is_era_generic(phrase: str) -> bool:
    """True when the phrase is only a date or era plus a generic noun.

    ``2nd century BCE device`` is not an artefact. ``Coso artifact`` is,
    because ``Coso`` is a real name.
    """
    tokens = [tok for tok in re.findall(r"[a-z0-9]+", _fold(phrase)) if tok]
    if not tokens:
        return False
    boring = _ERA_WORDS | _GENERIC_NOUNS | _STOP
    return all(tok in boring or tok.isdigit() for tok in tokens)


def is_abstract_close(text: str) -> bool:
    """A closing line that states no concrete object."""
    low = " ".join(_fold(text).split())
    if not low:
        return False
    return any(cue in low for cue in _CLOSE_CUES)


def is_commentary(text: str) -> bool:
    """A line about interpretation, not a new physical scene.

    Historians debating, mainstream narratives, and experimental replicas
    stay on the real artefact or the real site. They are not a licence for
    a library, a hangar, or a spaceship.
    """
    low = " ".join(_fold(text).split())
    if not low:
        return False
    return any(cue in low for cue in _COMMENTARY_CUES)


def load_entities(channel: str) -> list[Entity]:
    path = repo_root() / "channels_config" / channel / "entities.json"
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw if isinstance(raw, list) else raw.get("entities") or []
    found: list[Entity] = []
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            continue
        aliases = tuple(
            str(item).strip()
            for item in (row.get("aliases") or [])
            if str(item).strip()
        )
        name = str(row.get("name") or row.get("id") or "").strip()

        def _texts(key: str) -> tuple[str, ...]:
            return tuple(
                str(item).strip()
                for item in (row.get(key) or [])
                if str(item).strip()
            )

        found.append(Entity(
            id=str(row["id"]).strip(),
            name=name,
            aliases=aliases or (name.lower(),),
            kind=str(row.get("kind") or "artifact").strip(),
            canonical_visual=str(row.get("canonical_visual") or "").strip(),
            era=str(row.get("era") or "").strip(),
            setting=str(row.get("setting") or "").strip(),
            confusions=tuple(
                str(item).strip()
                for item in (row.get("confusions") or [])
                if str(item).strip()
            ),
            channels=tuple(str(item).strip() for item in (row.get("channels") or []) if item),
            canonical_shape=str(row.get("canonical_shape") or "").strip(),
            commons_search=_texts("commons_search"),
            commons_titles=_texts("commons_titles"),
            commons_categories=_texts("commons_categories"),
            text_only_safe=bool(row.get("text_only_safe")),
            edit_negatives=_texts("edit_negatives"),
            event_search=_texts("event_search"),
            event_categories=_texts("event_categories"),
            event_period=str(row.get("event_period") or "").strip(),
            event_negatives=_texts("event_negatives"),
        ))
    return found


def load_channel_config(channel: str) -> dict:
    path = repo_root() / "channels_config" / channel / "shot_planner.json"
    if not path.is_file():
        return {
            "channel": channel,
            "shot_types": list(SHOT_TYPES_FALLBACK),
            "hard_negatives": ["text", "watermark", "logo", "illustration", "CGI"],
            "identity_negatives": [],
        }
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


SHOT_TYPES_FALLBACK = ("macro", "close", "medium", "wide", "aerial", "over_shoulder")


def _alias_at(folded: str, alias: str) -> int:
    needle = _fold(alias).strip()
    if not needle:
        return -1
    match = re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", folded)
    return match.start() if match else -1


def entities_in_text(text: str, entities: list[Entity]) -> list[Entity]:
    """Entities named in *text*, earliest first. Longer aliases win overlaps."""
    folded = _fold(text)
    hits: list[tuple[int, int, Entity]] = []
    for entity in entities:
        for alias in entity.aliases:
            at = _alias_at(folded, alias)
            if at >= 0:
                hits.append((at, len(_fold(alias)), entity))
        named = _alias_at(folded, entity.name)
        if named >= 0:
            hits.append((named, len(_fold(entity.name)), entity))
    hits.sort(key=lambda row: (row[0], -row[1]))
    found: list[Entity] = []
    seen: set[str] = set()
    spans: list[tuple[int, int]] = []
    for at, length, entity in hits:
        end = at + length
        if any(at < stop and end > start for start, stop in spans):
            continue
        spans.append((at, end))
        if entity.id in seen:
            continue
        seen.add(entity.id)
        found.append(entity)
    return found
