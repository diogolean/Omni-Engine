# -*- coding: utf-8 -*-
"""A new Flux-safe scene when every rung of a beat has failed the judge."""
from __future__ import annotations

import os
from typing import Callable

from production.formats.economic_reel.artefact.flux_policy import acceptable_flux_concept, safe_flux_prompt


def replan_limit() -> int:
    raw = (os.getenv("REEL_REPLAN_MAX") or "").strip()
    try:
        return max(0, int(raw)) if raw else 2
    except ValueError:
        return 2


def next_concept(
    narration: str,
    *,
    kind: str,
    event: bool,
    name: str = "",
    period: str = "",
    rejection: str = "",
    attempt: int = 1,
    negatives: tuple[str, ...] | list[str] = (),
    llm: Callable[..., str | None] | None = None,
) -> str:
    """One allowed scene. The judge threshold stays 0.70.

    An LLM line is kept only when it stays inside the same ban. Otherwise
    the deterministic Flux-safe scene is the prompt.
    """
    fallback = safe_flux_prompt(
        narration,
        name=name,
        negatives=negatives,
        period=period,
        attempt=attempt,
        event=event,
        kind=kind,
        rejection=rejection,
    )
    disabled = (os.getenv("REEL_LADDER_LLM") or "").strip().lower() in {
        "0", "off", "false", "no",
    }
    if disabled or llm is None:
        return fallback
    try:
        text = llm(narration, fallback, rejection)
    except Exception:  # noqa: BLE001
        return fallback
    if not isinstance(text, str) or len(text.strip()) <= 40:
        return fallback
    cleaned = text.strip()
    # The model line is kept only when it does not name the entity or a part.
    # A rejected concept is dropped. The safe scene is the whole prompt.
    if not acceptable_flux_concept(cleaned, name=name):
        return fallback
    if "do not depict" not in cleaned.lower():
        cleaned = f"{cleaned} Do not depict {name}." if name else cleaned
        if not acceptable_flux_concept(cleaned, name=name):
            return fallback
    return cleaned
