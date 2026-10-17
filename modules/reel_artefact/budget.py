# -*- coding: utf-8 -*-
"""Per-reel cap on Nano Banana calls. Real photos and reuse do not spend it."""
from __future__ import annotations

import os
import threading

_LOCK = threading.Lock()
_USED = 0


def nano_cap() -> int:
    raw = (os.getenv("REEL_NANOBANANA_MAX_PER_REEL") or "4").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 4


def reset_nano_budget() -> None:
    global _USED
    with _LOCK:
        _USED = 0


def nano_used() -> int:
    with _LOCK:
        return _USED


def take_nano_slot() -> bool:
    """Reserve one Nano Banana call. False once the reel cap is spent."""
    global _USED
    with _LOCK:
        if _USED >= nano_cap():
            return False
        _USED += 1
        return True
