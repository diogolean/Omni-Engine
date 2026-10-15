# -*- coding: utf-8 -*-
"""Safety terms that every avatar prompt must carry.

Avatar YAML and prompt templates can add negative terms. They cannot drop
this list. :func:`merge_negatives` always unions it back in.
"""
from __future__ import annotations

# Canonical order. Keep this tuple as the single source of truth.
MANDATORY_NEGATIVE_TERMS: tuple[str, ...] = (
    "nude",
    "naked",
    "topless",
    "bottomless",
    "nipples",
    "exposed nipples",
    "areola",
    "exposed genitals",
    "genitals",
    "explicit",
    "porn",
    "nsfw",
    "child",
    "children",
    "toddler",
    "infant",
    "teen",
    "teenager",
    "underage",
    "minor",
    "preteen",
    "loli",
    "young girl",
    "young boy",
)


def _split_terms(text: str | None) -> list[str]:
    if not text:
        return []
    return [part.strip() for part in str(text).split(",") if part.strip()]


def merge_negatives(*chunks: str | None) -> str:
    """Comma-join negative chunks, then append any missing mandatory term.

    There is no switch to skip the mandatory list. Calling this with an
    empty string still returns every term in :data:`MANDATORY_NEGATIVE_TERMS`.
    """
    seen: set[str] = set()
    ordered: list[str] = []

    def add(term: str) -> None:
        key = term.casefold()
        if key in seen:
            return
        seen.add(key)
        ordered.append(term)

    for chunk in chunks:
        for term in _split_terms(chunk):
            add(term)
    for term in MANDATORY_NEGATIVE_TERMS:
        add(term)
    return ", ".join(ordered)
