# -*- coding: utf-8 -*-
"""Keep a sequence script under the platform ceiling.

The story is the length. A draft is not rewritten and not cut mid-sentence
to hit a word target. Only a soft ceiling applies: about 90 seconds of
narration (Facebook Reels), and only whole sentences are dropped from the
end. The opening sentence always stays.
"""
from __future__ import annotations

import re
from typing import Callable

_SENTENCE = re.compile(r"[^.!?]+[.!?]+|[^.!?]+$")
SOFT_NARRATION_S = 90.0
SOFT_WORDS_PER_S = 2.4


def word_band(target: int, tolerance: float = 0.15) -> tuple[int, int]:
    """Inclusive word range around ``target``. Kept for older callers."""
    center = max(1, int(target))
    span = max(0.0, float(tolerance))
    low = max(1, int(round(center * (1.0 - span))))
    high = max(low, int(round(center * (1.0 + span))))
    return low, high


def soft_word_ceiling(seconds: float = SOFT_NARRATION_S) -> int:
    """Words that fit in ``seconds`` at a documentary pace."""
    return max(40, int(round(float(seconds) * SOFT_WORDS_PER_S)))


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE.findall(text or "")]
    return [part for part in parts if part]


def soft_ceiling(text: str, *, max_seconds: float = SOFT_NARRATION_S) -> str:
    """Drop trailing sentences only when the script would pass the ceiling.

    A script already under the ceiling is unchanged, including a short one.
    The first sentence is never sliced, even when it alone is over the cap.
    """
    body = " ".join((text or "").split())
    if not body:
        return ""
    high = soft_word_ceiling(max_seconds)
    if len(body.split()) <= high:
        return body
    kept: list[str] = []
    count = 0
    for sentence in _sentences(body):
        taken = sentence.split()
        if not kept:
            kept.append(sentence)
            count += len(taken)
            continue
        if count + len(taken) > high:
            break
        kept.append(sentence)
        count += len(taken)
    return " ".join(kept) if kept else body


def trim_to_target(text: str, target: int, *, tolerance: float = 0.15) -> str:
    """Deprecated. Whole-sentence soft ceiling, not a hard word cut."""
    del target, tolerance
    return soft_ceiling(text)


Rewrite = Callable[[str, int, int, int], str]


def fit_word_target(
    text: str,
    target: int,
    *,
    tolerance: float = 0.15,
    rewrite: Rewrite | None = None,
) -> str:
    """Length follows the story. The rewrite-then-hard-trim loop is gone."""
    del target, tolerance, rewrite
    return soft_ceiling(text)
