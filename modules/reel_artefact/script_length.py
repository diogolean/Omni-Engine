# -*- coding: utf-8 -*-
"""Keep a sequence script inside the word target.

148 words ± 15% is 126–170. A draft that runs long (Baghdad came out at
259) is rewritten once when a model is available, then cut on sentence
boundaries until it fits. The opening, which carries the topic date, stays.
"""
from __future__ import annotations

import re
from typing import Callable

_SENTENCE = re.compile(r"[^.!?]+[.!?]+|[^.!?]+$")


def word_band(target: int, tolerance: float = 0.15) -> tuple[int, int]:
    """Inclusive word range around ``target``."""
    center = max(1, int(target))
    span = max(0.0, float(tolerance))
    low = max(1, int(round(center * (1.0 - span))))
    high = max(low, int(round(center * (1.0 + span))))
    return low, high


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE.findall(text or "")]
    return [part for part in parts if part]


def trim_to_target(text: str, target: int, *, tolerance: float = 0.15) -> str:
    """Drop sentences from the end until the script is inside the band.

    A script already inside the band is unchanged. A script that is short
    stays short: this function only cuts.
    """
    body = " ".join((text or "").split())
    if not body:
        return ""
    _low, high = word_band(target, tolerance)
    words = body.split()
    if len(words) <= high:
        return body
    kept: list[str] = []
    count = 0
    for sentence in _sentences(body):
        taken = sentence.split()
        if count + len(taken) <= high:
            kept.append(sentence)
            count += len(taken)
            continue
        room = high - count
        if room > 0 and not kept:
            kept.append(" ".join(taken[:room]))
        break
    if not kept:
        return " ".join(words[:high])
    return " ".join(kept)


Rewrite = Callable[[str, int, int, int], str]


def fit_word_target(
    text: str,
    target: int,
    *,
    tolerance: float = 0.15,
    rewrite: Rewrite | None = None,
) -> str:
    """Rewrite once when outside the band, then hard-trim anything still long."""
    low, high = word_band(target, tolerance)
    current = " ".join((text or "").split())
    count = len(current.split()) if current else 0
    if current and low <= count <= high:
        return current
    if rewrite is not None and current:
        try:
            revised = rewrite(current, int(target), low, high)
        except Exception:  # noqa: BLE001
            revised = ""
        revised = " ".join((revised or "").split())
        revised_count = len(revised.split()) if revised else 0
        if revised and low <= revised_count <= high:
            return revised
        if revised and revised_count > 0:
            current = revised
    return trim_to_target(current, target, tolerance=tolerance)
