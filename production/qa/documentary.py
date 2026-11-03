# -*- coding: utf-8 -*-
"""Strict documentary checks on a literal description of a still.

The vision model sees the image. This module is the fail-closed reading of
what it reports: a matching subject is not a pass when the picture is
anachronistic, non-photoreal, anatomically broken, or in the wrong place.
"""
from __future__ import annotations

import re

# Phrase, then the defect labels it proves. A phrase the spoken beat itself
# uses is not a defect.
_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("modern library", ("anachronism", "setting_contradicts_beat")),
    ("bookshelves", ("anachronism", "setting_contradicts_beat")),
    ("bookshelf", ("anachronism", "setting_contradicts_beat")),
    ("reading lamp", ("anachronism", "setting_contradicts_beat")),
    ("steampunk", ("anachronism",)),
    ("sci-fi", ("anachronism",)),
    ("science fiction", ("anachronism",)),
    ("futuristic", ("anachronism",)),
    ("spaceship", ("anachronism", "setting_contradicts_beat")),
    ("fighter jet", ("anachronism",)),
    ("hangar", ("anachronism", "setting_contradicts_beat")),
    ("cgi", ("non_photoreal",)),
    ("3d render", ("non_photoreal",)),
    ("miniature", ("non_photoreal",)),
    ("clay model", ("non_photoreal",)),
    ("illustration", ("non_photoreal",)),
    ("cartoon", ("non_photoreal",)),
    ("not photoreal", ("non_photoreal",)),
    ("mangled hand", ("anatomy",)),
    ("merging into", ("anatomy",)),
    ("extra finger", ("anatomy",)),
    ("bald man's head", ("anatomy",)),
    ("embedded in a bald", ("anatomy",)),
)

_NEGATED = re.compile(r"\b(?:no|not|without|never)\s+$", re.IGNORECASE)


def _mentioned(phrase: str, text: str) -> bool:
    """True when *phrase* appears and is not cancelled by no/not/without."""
    if not phrase or not text:
        return False
    for match in re.finditer(re.escape(phrase), text, flags=re.IGNORECASE):
        prefix = text[max(0, match.start() - 16):match.start()]
        if _NEGATED.search(prefix):
            continue
        return True
    return False


def documentary_failures(description: str, *, spoken: str = "") -> list[str]:
    """Defect labels for a literal caption. Empty means this check passed.

    Patterns that the spoken beat itself names are ignored, so a line that
    asks for a museum lamp is not failed for saying lamp. ``reading lamp``
    is the anachronism, not every lamp.
    """
    caption = " ".join((description or "").lower().split())
    beat = " ".join((spoken or "").lower().split())
    if not caption:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for phrase, labels in _PATTERNS:
        if not _mentioned(phrase, caption) or _mentioned(phrase, beat):
            continue
        for label in labels:
            if label in seen:
                continue
            seen.add(label)
            found.append(label)
    return found
