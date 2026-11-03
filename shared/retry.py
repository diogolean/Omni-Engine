# -*- coding: utf-8 -*-
"""Backoff for a dropped DNS lookup. A billing error is not retried here."""
from __future__ import annotations

import os
import time

_NETWORK = (
    "getaddrinfo",
    "connecterror",
    "connectionerror",
    "connection refused",
    "timed out",
    "timeout",
    "nameresolution",
    "name resolution",
    "temporary failure",
    "503",
    "429",
    "unavailable",
)


def is_network_error(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    text = f"{type(exc).__name__} {exc}".lower()
    return any(needle in text for needle in _NETWORK)


def retry_sleep(attempt: int) -> None:
    """``REEL_RETRY_SLEEP_S=0`` skips the wait. Otherwise 1s, 2s, 4s, capped at 8."""
    raw = (os.getenv("REEL_RETRY_SLEEP_S") or "").strip()
    if raw == "0":
        return
    try:
        base = float(raw) if raw else 1.0
    except ValueError:
        base = 1.0
    time.sleep(min(8.0, base * (2 ** max(0, int(attempt) - 1))))
