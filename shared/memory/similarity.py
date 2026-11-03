# -*- coding: utf-8 -*-
"""Lexical overlap used when the memory service is offline."""

from __future__ import annotations

import re
from typing import Any, Iterable

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")


def tokenise(text: str) -> list[str]:
    return [match.group(0).lower() for match in _WORD_RE.finditer(text or "")]


def normalise(text: str) -> str:
    return " ".join((text or "").lower().split())


def _jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def lexical_similarity(left: str, right: str) -> float:
    """0–1 overlap. Identical normalised text scores 1."""
    if normalise(left) and normalise(left) == normalise(right):
        return 1.0
    return _jaccard(tokenise(left), tokenise(right))


def hit_metadata(hit: Any) -> dict[str, Any]:
    if isinstance(hit, dict):
        meta = hit.get("metadata") or hit.get("customMetadata") or {}
    else:
        meta = getattr(hit, "metadata", None) or getattr(hit, "custom_metadata", None) or {}
    return dict(meta) if isinstance(meta, dict) else {}


def hit_score(hit: Any) -> float:
    if hit is None:
        return 0.0
    if isinstance(hit, dict):
        raw = hit.get("similarity", hit.get("score", hit.get("similarity_score", 0.0)))
    else:
        raw = (
            getattr(hit, "similarity", None)
            or getattr(hit, "score", None)
            or getattr(hit, "similarity_score", 0.0)
        )
    try:
        return float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0


def hit_text(hit: Any) -> str:
    if isinstance(hit, dict):
        for key in ("memory", "chunk", "content", "text"):
            value = hit.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""
    for attr in ("memory", "chunk", "content", "text"):
        value = getattr(hit, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return ""
