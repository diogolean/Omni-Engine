# -*- coding: utf-8 -*-
"""Reject a still that is a near-duplicate of one already accepted this reel."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from PIL import Image

_LOCK = threading.Lock()
_FRAMES: list[int] = []


def reset_diversity() -> None:
    with _LOCK:
        _FRAMES.clear()


def hamming_limit() -> int:
    """Bits that may differ before two frames count as different pictures."""
    raw = (os.getenv("REEL_STILL_HAMMING") or "").strip()
    try:
        return max(0, int(raw)) if raw else 8
    except ValueError:
        return 8


def dhash(path: Path | str) -> int | None:
    """64-bit difference hash. Pillow only, so the check runs without numpy."""
    try:
        with Image.open(path) as opened:
            gray = opened.convert("L").resize((9, 8), Image.LANCZOS)
            pixels = list(gray.getdata())
    except (OSError, ValueError):
        return None
    bits = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def claim_diverse(path: Path | str) -> bool:
    """True when ``path`` is different enough from accepted frames.

    The hash is recorded on success so a parallel beat sees it immediately.
    """
    digest = dhash(path)
    if digest is None:
        return True
    limit = hamming_limit()
    with _LOCK:
        if any(_hamming(digest, prev) <= limit for prev in _FRAMES):
            return False
        _FRAMES.append(digest)
        return True


def forget_diverse(path: Path | str) -> None:
    """Drop the hash when a later check rejects the frame."""
    digest = dhash(path)
    if digest is None:
        return
    with _LOCK:
        if digest in _FRAMES:
            _FRAMES.remove(digest)
