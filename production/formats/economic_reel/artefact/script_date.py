# -*- coding: utf-8 -*-
"""The topic's date belongs in the opening of the narration."""
from __future__ import annotations

import re

_DATE = re.compile(
    r"\b(?:"
    r"\d{1,2}(?:st|nd|rd|th)\s+century\s+(?:bc|bce|ad|ce)"
    r"|\d{3,4}\s*(?:-|–|to|and)\s*\d{3,4}\s*(?:bc|bce|ad|ce)"
    r"|\d{3,4}\s*(?:bc|bce|ad|ce)"
    r")\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def topic_date_phrase(topic: str) -> str:
    """The date as the topic writes it, or empty when the topic has none."""
    match = _DATE.search(topic or "")
    return " ".join(match.group(0).split()) if match else ""


def _date_key(phrase: str) -> tuple[tuple[str, ...], str]:
    low = phrase.lower().replace("bce", "bc").replace("ce", "ad")
    era = "bc" if re.search(r"\bbc\b", low) else "ad" if re.search(r"\bad\b", low) else ""
    return tuple(re.findall(r"\d+", low)), era


def states_date(text: str, phrase: str) -> bool:
    """True when ``text`` carries the same year numbers and era as ``phrase``."""
    nums, era = _date_key(phrase)
    if not nums:
        return False
    low = (text or "").lower().replace("bce", "bc").replace("ce", "ad")
    if era and not re.search(rf"\b{era}\b", low):
        return False
    found = re.findall(r"\d+", low)
    return all(number in found for number in nums)


def _sentences(narration: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE.split(narration or "") if part.strip()]
    if parts:
        return parts
    text = " ".join((narration or "").split())
    return [text] if text else []


def ensure_topic_date(narration: str, topic: str) -> str:
    """Put the topic date in the first two sentences.

    A later sentence that already states it moves up behind the opening
    line. A script that never states it gains one short sentence there.
    """
    phrase = topic_date_phrase(topic)
    if not phrase:
        return narration
    sentences = _sentences(narration)
    if not sentences:
        return f"It dates to the {phrase}."
    if states_date(" ".join(sentences[:2]), phrase):
        return narration
    for index, sentence in enumerate(sentences):
        if not states_date(sentence, phrase):
            continue
        rest = sentences[:index] + sentences[index + 1:]
        ordered = [rest[0], sentence, *rest[1:]] if rest else [sentence]
        return " ".join(ordered)
    insert = f"It dates to the {phrase}."
    if len(sentences) == 1:
        return f"{sentences[0]} {insert}"
    return " ".join([sentences[0], insert, *sentences[1:]])
