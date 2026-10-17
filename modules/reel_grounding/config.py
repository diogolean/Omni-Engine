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


def memory_enabled() -> bool:
    """Read and write the per-topic QA ledger. On by default."""
    return _on("REEL_VISUAL_MEMORY", True)
