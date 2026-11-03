# -*- coding: utf-8 -*-
"""The hook has to name this episode, and the land has to match the place."""
from __future__ import annotations

from shared.channel_flags import coalesce_page, flag

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
_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think>", re.I | re.S)
_THINK_TAIL = re.compile(r"<think\b[^>]*>.*\Z", re.I | re.S)
_FENCE = re.compile(r"```+[a-z0-9_-]*", re.I)
_ACT_ONE = re.compile(r"\[\s*ACT\s*1\s*\]", re.I)
_PREAMBLE = re.compile(
    r"^(?:here(?:'s| is)|sure|certainly|of course|below is|the following is)"
    r"[^\n]{0,80}(?:script|narration|voiceover|draft)[^\n]*$",
    re.I,
)
_LABELS = frozenset({
    "narration", "script", "voiceover", "voiceover script", "documentary script",
})
# A real narration is near the 90-second ceiling. A longer draft is still the
# model's reasoning, and prefixing the topic onto it would hide that.
_HOOK_REPAIR_WORDS = 400
_SPOKEN_CTA = re.compile(
    r"^(?:follow ancient knowledge\b|follow for more\b|follow us\b|"
    r"subscribe\b|like and subscribe\b)",
    re.I,
)
_CAMERA_DIRECTION = re.compile(
    r"(?:"
    r"golden[\s-]?hour"
    r"|aerial\s+views?"
    r"|fog\s+(?:often\s+)?rolls?"
    r"|foliage\s+grows?"
    r"|\bwater\s+flows?\b"
    r"|close\s+natural\s+detail"
    r"|ruins of the real region"
    r"|\brock\s+formations?\b"
    r"|\b(?:wide|establishing)\s+shots?\b"
    r"|\b(?:the camera|camera pans|drone shot)\b"
    r")",
    re.I,
)
_PSEUDO_SCIENCE = re.compile(
    r"(?:"
    r"mainstream\s+archaeolog\w*\s+(?:often\s+)?"
    r"(?:refuses|refused|denies|denied|hides|rejects)"
    r"|archaeolog\w*\s+(?:refuses|denies|hides)\b"
    r"|(?:have|has|had)\s+powered\b"
    r"|powered\s+(?:similar\s+)?devices"
    r")",
    re.I,
)


def _fold(text: str) -> str:
    return (text or "").lower().translate(_FOLD)


def topic_tokens(topic: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]{4,}", _fold(topic))
        if token not in _HOOK_STOP
    ]


def strip_model_wrapper(text: str) -> str:
    """Drop reasoning tags, fences, and a leading 'Here is the script'."""
    raw = _THINK_BLOCK.sub("\n", text or "")
    raw = _THINK_TAIL.sub("\n", raw)
    raw = _FENCE.sub(" ", raw)
    kept: list[str] = []
    started = False
    for line in raw.splitlines():
        stripped = line.strip()
        if not started:
            if not stripped or _PREAMBLE.match(stripped):
                continue
            if stripped.lower().rstrip(".:") in _LABELS:
                continue
        started = True
        kept.append(line)
    return "\n".join(kept).strip()


def extract_narration(text: str) -> str:
    """The spoken script, starting at [ACT 1] when the model wrote one."""
    raw = strip_model_wrapper(text)
    match = _ACT_ONE.search(raw)
    if match:
        raw = raw[match.start():]
    return raw.strip()


def repair_hook(script: str, topic: str) -> str:
    """Prefix the topic so the opening sentence names this episode."""
    body = (script or "").strip()
    name = " ".join((topic or "").split()).strip().rstrip(".")
    if not body or not name or hook_names_topic(body, name):
        return body
    return f"{name}. {body}"


def repair_hook_if_only_miss(
    script: str,
    topic: str,
    *,
    channel: str = "",
) -> str:
    """Repair a short script whose only grounding miss is the opening line."""
    body = (script or "").strip()
    if len(body.split()) > _HOOK_REPAIR_WORDS or not script_is_usable(body, topic):
        return body
    if grounding_problem(body, topic, channel=channel) != "hook does not name the topic":
        return body
    return repair_hook(body, topic)


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
    from production.qa.relevance import dune_conflict

    if dune_conflict(script or "", geography or ""):
        return "sand_dunes"
    place = (geography or "").lower()
    if not place.strip() or re.search(r"dune|sahara|erg|sand sea|\bdesert\b", place):
        return ""
    if _SANDS.search(script or ""):
        return "sands"
    return ""


def strip_spoken_cta(script: str) -> str:
    """Drop a spoken follow line. The CTA track is appended on its own."""
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[.!?])\s+", (script or "").strip())
        if item.strip()
    ]
    kept = [item for item in sentences if not _SPOKEN_CTA.search(item)]
    return " ".join(kept).strip()


def camera_direction_problem(script: str) -> str:
    """A shot-list sentence is not narration."""
    for sentence in re.split(r"(?<=[.!?])\s+", script or ""):
        if _CAMERA_DIRECTION.search(sentence):
            return "camera direction in the narration"
    return ""


def tone_problem(script: str) -> str:
    """Speculation stays in the 'some researchers suggest' frame."""
    if _PSEUDO_SCIENCE.search(script or ""):
        return "tone: soften the claim"
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
    slug = (channel or "").strip().lower()
    documentary = (not slug) or flag(slug, "DOCUMENTARY_PIPELINE")
    if documentary:
        camera = camera_direction_problem(script)
        if camera:
            return camera
        tone = tone_problem(script)
        if tone:
            return tone
    geography = ""
    if documentary:
        try:
            from production.formats.economic_reel.shot_planner.entities import entity_for_line, load_entities

            rows = load_entities(coalesce_page(channel, "DOCUMENTARY_PIPELINE"))
            entity = entity_for_line(topic, rows, topic=topic)
            geography = entity.geography if entity is not None else ""
        except Exception:  # noqa: BLE001
            geography = ""
    return terrain_conflicts(script, geography)
