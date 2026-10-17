# -*- coding: utf-8 -*-
"""Prompt refinement for a rejected narration beat."""
from __future__ import annotations

import re

_ENTITY = re.compile(r"\b[A-Z][a-zA-Z0-9'’\-]+(?:\s+[A-Z][a-zA-Z0-9'’\-]+){0,3}\b")
_SITE = re.compile(
    r"\b(?:at|in|near|off|from|outside|inside|across)\s+"
    r"([A-Z][A-Za-z0-9'’\-]+(?:\s+[A-Z][A-Za-z0-9'’\-]+){0,3})"
)
_STOP = {
    "The", "This", "That", "When", "What", "Inside", "After", "Before",
    "There", "Here", "Then", "And", "But", "Photoreal",
}


def named_entities(*parts: str) -> list[str]:
    found: list[str] = []
    for part in parts:
        for match in _ENTITY.findall(part or ""):
            head = match.split()[0]
            if head in _STOP:
                continue
            if match not in found:
                found.append(match)
    return found[:8]


def extract_site(*parts: str) -> str:
    blob = " ".join(p for p in parts if p)
    match = _SITE.search(blob)
    return match.group(1).strip() if match else ""


def refine_prompt(
    base_prompt: str,
    sentence: str,
    *,
    entities: list[str] | None = None,
    site: str = "",
    rejection_reason: str = "",
    attempt: int = 1,
) -> str:
    """Rebuild the image prompt around one spoken sentence and its proper nouns."""
    spoken = (sentence or "").strip() or (base_prompt or "").strip()[:240]
    names = [e for e in (entities or []) if e][:4]
    bits = [f"Narration sentence: {spoken}."]
    if names:
        bits.append("Named entity: " + ", ".join(names) + ".")
    if (site or "").strip():
        bits.append(f"Site: {site.strip()}.")
    reason = (rejection_reason or "").strip()
    if reason and reason not in {"below_relevance_threshold"}:
        bits.append(f"The previous image was rejected: {reason}. Do not repeat that element.")
    bits.append(
        "Photoreal documentary still of only the subject, object, and place "
        f"named in that sentence. Camera variation {int(attempt)}. "
        "No extra landmarks, no unrelated monument, no text, no watermark."
    )
    return " ".join(bits)
