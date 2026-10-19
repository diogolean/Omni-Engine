# -*- coding: utf-8 -*-
"""The hook has to name this episode, and the land has to match the place."""
from __future__ import annotations

import re

_FOLD = str.maketrans({
    "ö": "o", "ü": "u", "ä": "a", "é": "e", "è": "e",
    "ş": "s", "ğ": "g", "ı": "i", "â": "a", "î": "i",
})
_HOOK_STOP = frozenset({
    "carved", "stone", "pillars", "ancient", "about", "from", "with",
    "that", "this", "their", "there", "into", "near", "were", "have",
})
_SANDS = re.compile(r"\bsands\b", re.I)


def _fold(text: str) -> str:
    return (text or "").lower().translate(_FOLD)


def topic_tokens(topic: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]{4,}", _fold(topic))
        if token not in _HOOK_STOP
    ]


def first_sentence(script: str) -> str:
    text = " ".join((script or "").split())
    if not text:
        return ""
    return re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]


def hook_names_topic(script: str, topic: str) -> bool:
    """The opening sentence names this episode, not a different monument."""
    tokens = topic_tokens(topic)
    if not tokens:
        return True
    opening = _fold(first_sentence(script))
    return any(token in opening for token in tokens)


def terrain_conflicts(script: str, geography: str) -> str:
    """A non-desert place cannot be told as sand. Empty when the line fits."""
    from modules.reel_qa.relevance import dune_conflict

    if dune_conflict(script or "", geography or ""):
        return "sand_dunes"
    place = (geography or "").lower()
    if not place.strip() or re.search(r"dune|sahara|erg|sand sea|\bdesert\b", place):
        return ""
    if _SANDS.search(script or ""):
        return "sands"
    return ""


def script_is_usable(script: str, topic: str = "") -> bool:
    """A real narration, not fragments of the topic string."""
    words = [item for item in (script or "").split() if item]
    if len(words) < 40:
        return False
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[.!?])\s+", " ".join(words))
        if item.strip()
    ]
    if len(sentences) < 3:
        return False
    real = sum(1 for sentence in sentences if len(sentence.split()) >= 6)
    if real < 3:
        return False
    topic_words = {item.strip(".,").lower() for item in (topic or "").split()}
    if topic_words and all(len(sentence.split()) <= 2 for sentence in sentences):
        return False
    return True


def grounding_problem(script: str, topic: str, *, channel: str = "") -> str:
    """Why this narration cannot be filmed, or "" when it can."""
    if not hook_names_topic(script, topic):
        return "hook does not name the topic"
    geography = ""
    if (channel or "").strip().lower() in {"", "ancient_knowledge"}:
        try:
            from modules.shot_planner.entities import entity_for_line, load_entities

            rows = load_entities(channel or "ancient_knowledge")
            entity = entity_for_line(topic, rows, topic=topic)
            geography = entity.geography if entity is not None else ""
        except Exception:  # noqa: BLE001
            geography = ""
    return terrain_conflicts(script, geography)
