# -*- coding: utf-8 -*-
"""Per-reel cap on Nano Banana calls. Real photos and reuse do not spend it."""
from __future__ import annotations

import os
import threading
from pathlib import Path

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


def _usd(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    try:
        return max(0.0, float(raw)) if raw else default
    except ValueError:
        return default


def beat_budget_usd() -> float:
    """Most one beat may spend on generated stills. Real photos and reuse are $0."""
    return _usd("REEL_BEAT_BUDGET_USD", 0.20)


def reel_budget_usd() -> float:
    """Most one reel may spend on generated stills."""
    return _usd("REEL_REEL_BUDGET_USD", 2.00)


_REEL_SPENT = 0.0


_FLUX_USED = 0
_REPLAN_USED = 0


def flux_max_per_reel() -> int:
    """Most Flux generations one reel may start, on top of the dollar cap."""
    return _count("REEL_FLUX_MAX_PER_REEL", 18)


def replan_max_per_reel() -> int:
    """Most re-plans one reel may start, on top of the per-beat limit."""
    return _count("REEL_REPLAN_MAX_PER_REEL", 6)


def take_flux_slot() -> bool:
    """Reserve one Flux generation. False once the reel cap is spent."""
    global _FLUX_USED
    with _LOCK:
        if _FLUX_USED >= flux_max_per_reel():
            return False
        _FLUX_USED += 1
        return True


def release_flux_slot() -> None:
    """Give the slot back when Flux produced no file."""
    global _FLUX_USED
    with _LOCK:
        _FLUX_USED = max(0, _FLUX_USED - 1)


def take_replan_slot() -> bool:
    """Reserve one re-plan. False once the reel cap is spent."""
    global _REPLAN_USED
    with _LOCK:
        if _REPLAN_USED >= replan_max_per_reel():
            return False
        _REPLAN_USED += 1
        return True


def reset_spend() -> None:
    global _REEL_SPENT, _FLUX_USED, _REPLAN_USED
    with _LOCK:
        _REEL_SPENT = 0.0
        _FLUX_USED = 0
        _REPLAN_USED = 0


def reel_spent() -> float:
    with _LOCK:
        return _REEL_SPENT


def try_spend(amount: float) -> bool:
    """Reserve ``amount`` against the reel cap. $0 always fits."""
    global _REEL_SPENT
    cost = max(0.0, float(amount))
    with _LOCK:
        if cost <= 0:
            return True
        if _REEL_SPENT + cost > reel_budget_usd() + 1e-9:
            return False
        _REEL_SPENT += cost
        return True


def refund(amount: float) -> None:
    global _REEL_SPENT
    cost = max(0.0, float(amount))
    with _LOCK:
        _REEL_SPENT = max(0.0, _REEL_SPENT - cost)


def _count(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        return max(0, int(raw)) if raw else default
    except ValueError:
        return default


def real_max_per_photo() -> int:
    """How many beats may publish the same real photograph."""
    return _count("REEL_REAL_MAX_PER_PHOTO", 2)


def reuse_max_per_still() -> int:
    """How many beats may show one still, the original included."""
    return _count("REEL_REUSE_MAX_PER_STILL", 2)


def reuse_max_fraction() -> float:
    """Largest share of the reel that may be a reused still."""
    return min(1.0, _usd("REEL_REUSE_MAX_FRACTION", 0.30))


_REAL_USES: dict[str, int] = {}
_STILL_USES: dict[str, int] = {}
_SEEN_PATHS: set[str] = set()
_REUSE_BEATS = 0


def reset_uses() -> None:
    """Clear per-reel photo and reuse counters. Call beside the nano reset."""
    global _REUSE_BEATS
    with _LOCK:
        _REAL_USES.clear()
        _STILL_USES.clear()
        _SEEN_PATHS.clear()
        _REUSE_BEATS = 0


def real_uses(key: str) -> int:
    with _LOCK:
        return int(_REAL_USES.get(key or "", 0))


def real_allowed(key: str) -> bool:
    if not key:
        return True
    return real_uses(key) < real_max_per_photo()


def note_real_use(key: str) -> None:
    if not key:
        return
    with _LOCK:
        _REAL_USES[key] = _REAL_USES.get(key, 0) + 1


def still_uses(key: str) -> int:
    with _LOCK:
        return int(_STILL_USES.get(key or "", 0))


def reuse_beats() -> int:
    with _LOCK:
        return _REUSE_BEATS


def note_original(path: str, key: str) -> None:
    """Count the first publish of a still. A second call for the same file is a no-op."""
    resolved = str(path or "")
    token = key or resolved
    if not token:
        return
    with _LOCK:
        if resolved and resolved in _SEEN_PATHS:
            return
        if resolved:
            _SEEN_PATHS.add(resolved)
        _STILL_USES[token] = _STILL_USES.get(token, 0) + 1


def reuse_room(key: str, n_beats: int) -> tuple[bool, str]:
    """Whether another copy of ``key`` still fits the per-still and per-reel caps."""
    with _LOCK:
        if _STILL_USES.get(key or "", 0) >= reuse_max_per_still():
            return False, "still"
        beats = max(0, int(n_beats or 0))
        if beats > 0 and (_REUSE_BEATS + 1) / beats > reuse_max_fraction() + 1e-9:
            return False, "fraction"
    return True, ""


def note_reuse(path: str, key: str) -> None:
    resolved = str(path or "")
    token = key or resolved
    if not token:
        return
    global _REUSE_BEATS
    with _LOCK:
        if resolved and resolved in _SEEN_PATHS:
            return
        if resolved:
            _SEEN_PATHS.add(resolved)
        _STILL_USES[token] = _STILL_USES.get(token, 0) + 1
        _REUSE_BEATS += 1


def file_hash_key(path: str) -> str:
    """Stable id for identical bytes, including a cache file copied to a new dest."""
    import hashlib

    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return "sha256:" + digest


def claim_still(path: str, key: str, n_beats: int) -> tuple[bool, str]:
    """Reserve one use of ``key``. The same hash cannot fill the reel.

    The first use is the original. Later uses count toward the reuse fraction.
    A path that was already reserved returns success without a second increment.
    """
    resolved = str(path or "")
    token = key or resolved
    if not token:
        return True, ""
    global _REUSE_BEATS
    with _LOCK:
        if resolved and resolved in _SEEN_PATHS:
            return True, ""
        uses = int(_STILL_USES.get(token, 0))
        if uses >= reuse_max_per_still():
            return False, "still"
        beats = max(0, int(n_beats or 0))
        if uses >= 1 and beats > 0 and (_REUSE_BEATS + 1) / beats > reuse_max_fraction() + 1e-9:
            return False, "fraction"
        if resolved:
            _SEEN_PATHS.add(resolved)
        _STILL_USES[token] = uses + 1
        if uses >= 1:
            _REUSE_BEATS += 1
        return True, ""


def release_still(path: str, key: str) -> None:
    """Give back a reservation when the judge rejects the still."""
    resolved = str(path or "")
    token = key or resolved
    if not token:
        return
    global _REUSE_BEATS
    with _LOCK:
        if resolved and resolved not in _SEEN_PATHS:
            return
        if resolved:
            _SEEN_PATHS.discard(resolved)
        uses = int(_STILL_USES.get(token, 0))
        if uses <= 0:
            return
        if uses >= 2:
            _REUSE_BEATS = max(0, _REUSE_BEATS - 1)
        if uses == 1:
            _STILL_USES.pop(token, None)
        else:
            _STILL_USES[token] = uses - 1
