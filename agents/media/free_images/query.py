# -*- coding: utf-8 -*-
"""Build a short search query from a topic, a named entity, and a sentence."""

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
    }
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


def build_query(
    *,
    topic: str = "",
    entity: str = "",
    sentence: str = "",
    limit: int = 8,
) -> str:
    """Prefer the named entity, then the topic, then leftover sentence tokens."""
    pieces: list[str] = []
    for token in tokenize(entity):
        pieces.append(token)
    for token in tokenize(topic):
        if token not in pieces:
            pieces.append(token)
    if len(pieces) < limit:
        for token in tokenize(sentence):
            if token not in pieces:
                pieces.append(token)
            if len(pieces) >= limit:
                break
    return " ".join(pieces[:limit])
