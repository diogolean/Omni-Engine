# -*- coding: utf-8 -*-
"""A new visual concept when every rung of a beat has failed the judge."""
from __future__ import annotations

import os
from typing import Callable

from modules.reel_artefact.prompts import _FORBID_OBJECT, _LOOK

_OBJECT = (
    "A quiet museum hall. Display cases are small in the distance.",
    "A display case seen from across the room, one lamp, the object too far to invent.",
    "The landscape of the region under open sky, no artefact in the frame.",
)
_EVENT = (
    "A wider view of the historical event: calm water, a wooden boat, simple composition.",
    "Hands and period tools only. No named artefact.",
    "The shoreline and the landscape of the place, uncluttered, period-correct.",
)
_SCENE = (
    "A wider establishing shot that still fits the narration.",
    "Hands and tools, a small detail, no named artefact close-up.",
    "A simple map or coastline of the place, no modern labels.",
    "The landscape of the place under open sky.",
)


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
    """One documentary prompt for another try. The judge threshold stays 0.70."""
    spoken = " ".join((narration or "").split())
    if event:
        pool = _EVENT
    elif kind == "object":
        pool = _OBJECT
    else:
        pool = _SCENE
    idea = pool[(max(1, int(attempt)) - 1) % len(pool)]
    named = f" Do not depict {name}." if name else ""
    when = f" Period: {period}." if period else ""
    bans = ", ".join(str(item).strip().rstrip(".") for item in negatives if str(item).strip())
    ban_line = f" {bans}." if bans else ""
    reason = " ".join((rejection or "").split())
    rejected = f" The previous still was rejected: {reason}." if reason else ""
    fallback = (
        f"{_LOOK} {idea}{named}{when}{ban_line} {_FORBID_OBJECT} "
        f"Narration: {spoken}.{rejected}"
    )
    disabled = (os.getenv("REEL_LADDER_LLM") or "").strip().lower() in {
        "0", "off", "false", "no",
    }
    if disabled or llm is None:
        return fallback
    try:
        text = llm(spoken, idea, reason)
    except Exception:  # noqa: BLE001
        return fallback
    if isinstance(text, str) and len(text.strip()) > 40:
        return text.strip()
    return fallback
