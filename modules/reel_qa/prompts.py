# -*- coding: utf-8 -*-
"""Prompt refinement for a rejected narration beat."""
from __future__ import annotations

import re

_ENTITY = re.compile(r"\b[A-Z][a-zA-Z0-9'’\-]+(?:\s+[A-Z][a-zA-Z0-9'’\-]+){0,3}\b")
_SITE = re.compile(
    r"(?i:\b(?:at|in|near|off|from|outside|inside|across)\s+)"
    r"([A-Z][A-Za-z0-9'’\-]+(?:\s+[A-Z][A-Za-z0-9'’\-]+){0,3})"
    r"(?:,\s*([A-Z][A-Za-z0-9'’\-]+))?"
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
    """Place phrase, keeping ``City, Region`` so ``London`` is not left alone."""
    blob = " ".join(p for p in parts if p)
    match = _SITE.search(blob)
    if not match:
        return ""
    place = match.group(1).strip()
    region = (match.group(2) or "").strip()
    if region:
        return f"{place}, {region}"
    return place


def refine_prompt(
    base_prompt: str,
    sentence: str,
    *,
    entities: list[str] | None = None,
    site: str = "",
    rejection_reason: str = "",
    attempt: int = 1,
    episode_subject: str = "",
) -> str:
    """Rebuild a rejected prompt with the object first and the place second.

    A leading ``Site: London`` made Flux draw a city alley for a hammer
    found in London, Texas. The narration sentence stays in the prompt so
    the judge and the model still see the exact line.
    """
    spoken = (sentence or "").strip() or (base_prompt or "").strip()[:240]
    names = [e for e in (entities or []) if e][:4]
    subject = spoken
    try:
        from modules.reel_grounding.brief import build_episode_brief, visual_for_beat

        brief = build_episode_brief(episode_subject or spoken, spoken)
        subject = visual_for_beat(brief, spoken).subject
    except Exception:  # noqa: BLE001
        subject = spoken
    bits = [
        f"PRIMARY SUBJECT: {subject}.",
        f"Narration sentence: {spoken}.",
    ]
    if names:
        bits.append("Named entity: " + ", ".join(names) + ".")
    if (site or "").strip():
        bits.append(
            f"The place is context only, not the subject: {site.strip()}. "
            "Do not replace the subject with a cityscape or alley of that name."
        )
    reason = (rejection_reason or "").strip()
    if reason and reason not in {"below_relevance_threshold"}:
        bits.append(f"The previous image was rejected: {reason}. Do not repeat that element.")
    bits.append(
        "Photoreal documentary still of only the primary subject named in that "
        f"sentence. Camera variation {int(attempt)}. "
        "No extra landmarks, no unrelated monument, no text, no watermark."
    )
    return " ".join(bits)
