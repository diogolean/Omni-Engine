# -*- coding: utf-8 -*-
"""Flags for beat grounding and the visual-memory ledger."""
from __future__ import annotations

import os


def _on(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "off", "no"}


def grounding_enabled() -> bool:
    """Rewrite beat prompts so the episode object leads. On by default."""
    return _on("REEL_VISUAL_GROUNDING", True)


def grounding_enabled_for(channel: str) -> bool:
    """Grounding for this page. Ancient Knowledge only unless
    ``REEL_VISUAL_GROUNDING_CHANNELS`` lists more (comma-separated).

    Master Mei and the other sequence pages keep their own prompts and style.
    """
    if not grounding_enabled():
        return False
    raw = os.getenv("REEL_VISUAL_GROUNDING_CHANNELS") or "ancient_knowledge"
    allowed = {part.strip().lower() for part in raw.split(",") if part.strip()}
    return (channel or "").strip().lower() in allowed


def memory_enabled() -> bool:
    """Read and write the per-topic QA ledger. On by default."""
    return _on("REEL_VISUAL_MEMORY", True)


def angle_memory_enabled() -> bool:
    """Remember the narrative angle used on a topic. On by default.

    Set ``REEL_ANGLE_MEMORY=0`` to leave script prompts unchanged.
    """
    return _on("REEL_ANGLE_MEMORY", True)


def angle_memory_for(channel_id: str, narrative_mode: str = "") -> bool:
    """Ancient Knowledge investigative scripts only.

    Master Mei warrior scripts stay on their own theme lock.
    """
    if not angle_memory_enabled():
        return False
    if (narrative_mode or "").strip().lower() == "warrior_discipline":
        return False
    return (channel_id or "").strip().lower() == "ancient_knowledge"
