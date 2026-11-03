# -*- coding: utf-8 -*-
"""Sentence splitting, title-safe text, and word tests.

Shared by the director, the briefs, the metadata, and the offline stub, so
this module imports nothing from the package.
"""
from __future__ import annotations

import re

# A sentence also ends after a closing quote: ``you said "no." Then``.
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"'\u201d\u2019)\]]*\s+")
_STRAIGHT = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
    }
)
_NOT_PRINTABLE = re.compile(r"[^\x20-\x7e]")
_SECOND_PERSON = re.compile(r"\b(?:you|your|yours|yourself)\b", re.IGNORECASE)
_FIRST_PERSON = re.compile(r"\b(?:i|my|me|mine|myself)\b", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")
# Words that point back at an earlier line, so a sentence using them cannot stand alone.
CONTEXT_WORDS = frozenset("it its that this these those they them one ones there also too either both same extra".split())


def addresses_you(text: str) -> bool:
    """True when ``text`` speaks to someone as "you" (also "you're", "your", ...)."""
    return bool(_SECOND_PERSON.search(text or ""))


def speaks_for_self(text: str) -> bool:
    """True when ``text`` is in the first person singular ("I", "I'm", "my", "me", ...)."""
    return bool(_FIRST_PERSON.search(text or ""))


def points_back(text: str) -> bool:
    """True when ``text`` leans on an earlier line ("it", "that", "they", ...) to make sense."""
    return any(word.lower() in CONTEXT_WORDS for word in _WORD.findall(text or ""))


def sentences(text: str) -> list[str]:
    """Sentences of ``text``, splitting after closing quotes too."""
    return [chunk.strip() for chunk in _SENTENCE_END.split((text or "").strip()) if chunk.strip()]


def plain(text: str) -> str:
    """Printable ASCII on one line: straight quotes, no emoji, no control characters."""
    flat = " ".join(str(text or "").translate(_STRAIGHT).split())
    return " ".join(_NOT_PRINTABLE.sub("", flat).split())


def quotable(text: str) -> str:
    """``plain`` text that can sit inside a double-quoted title."""
    return plain(text).replace('"', "'").strip()


def trim_words(text: str, limit: int) -> str:
    """Cut ``text`` at a word boundary so it fits ``limit`` characters."""
    if len(text) <= limit:
        return text
    cut = text[: max(0, limit - 3)].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{cut}..." if cut else text[:limit]


__all__ = [
    "CONTEXT_WORDS",
    "addresses_you",
    "plain",
    "points_back",
    "quotable",
    "sentences",
    "speaks_for_self",
    "trim_words",
]
