# -*- coding: utf-8 -*-
"""Build a short subject query for Commons and Openverse.

The subject is the named entity, or the first two or three words of the
title. The article sentence is not added. A longer subject broadens by
dropping the last word.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?", re.IGNORECASE)

_STOP = frozenset(
    {
        "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
        "from", "by", "at", "as", "is", "are", "was", "were", "be", "this",
        "that", "these", "those", "it", "its", "into", "over", "under", "than",
        "then", "how", "what", "when", "where", "why", "who", "whom", "which",
        "your", "you", "our", "their", "his", "her", "not", "no", "yes",
        "cinematic", "photography", "photograph", "photo", "ultra", "realistic",
        "hyper", "detailed", "lighting", "light", "shot", "style", "aesthetic",
        "background", "scene", "image", "render", "flux", "prompt", "vertical",
        "portrait", "landscape", "film", "grain", "bokeh", "dramatic", "soft",
        "warm", "cold", "dark", "bright", "high", "quality", "8k", "4k",
        "masterpiece", "trending", "artstation",
        "world", "worlds", "world's", "most", "mysterious", "unbelievable",
        "incredible", "hidden", "secret", "amazing", "shocking",
    }
)

_SUBJECT_WORDS = 3
_TRAILING_GENERIC = frozenset(
    {"book", "books", "story", "article", "history", "mystery", "mysteries", "site", "place"}
)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN.findall(text or ""):
        token = match.lower().strip("'")
        if len(token) < 3 or token in _STOP or token in seen:
            continue
        if token.isdigit():
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _subject_tokens(entity: str, topic: str, cap: int) -> list[str]:
    """Entity words first. The title is used only when the entity adds none."""
    tokens = tokenize(entity)[:cap] if (entity or "").strip() else []
    if not tokens:
        tokens = tokenize(topic)[:cap]
    while len(tokens) > 2 and tokens[-1] in _TRAILING_GENERIC:
        tokens.pop()
    return tokens


def query_variants(
    *,
    topic: str = "",
    entity: str = "",
    sentence: str = "",
    limit: int = _SUBJECT_WORDS,
) -> list[str]:
    """Short subject, then a broader prefix when the subject is three words.

    ``sentence`` is accepted so callers can keep passing the article. It is
    not part of the query. ``limit`` caps the subject at two or three words.
    """
    del sentence
    cap = max(1, min(int(limit), _SUBJECT_WORDS))
    tokens = _subject_tokens(entity, topic, cap)
    if not tokens:
        return []
    variants = [" ".join(tokens)]
    if len(tokens) > 2:
        broader = " ".join(tokens[:2])
        if broader not in variants:
            variants.append(broader)
    return variants


def build_query(
    *,
    topic: str = "",
    entity: str = "",
    sentence: str = "",
    limit: int = _SUBJECT_WORDS,
) -> str:
    """Two or three subject words. The article sentence is left out."""
    variants = query_variants(topic=topic, entity=entity, sentence=sentence, limit=limit)
    return variants[0] if variants else ""
