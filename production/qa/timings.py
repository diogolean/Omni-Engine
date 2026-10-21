# -*- coding: utf-8 -*-
"""Drop SSML leftovers from subtitle timings without dropping spoken numbers."""
from __future__ import annotations

import re

_TAG = re.compile(
    r"^\[?(?:cackles?|chuckles?|cold\s*chuckle|arrogant\s*scoff|"
    r"deep\s*subtle\s*laugh|dry\s*laugh|laughs?|sighs?|whispers?|"
    r"pause|beat|silence)\]?$",
    re.IGNORECASE,
)
# ``1.5s`` is a leaked ``<break time="1.5s"/>`` fragment. A year or a
# quantity (``1934``, ``400``, ``1.5``) is narration and stays.
_SSML_DURATION = re.compile(r"^\d+(?:\.\d+)?s$", re.IGNORECASE)
_PUNCT = " .,;:!?<>/\"'"


def is_ssml_timing_token(token: str) -> bool:
    tok = (token or "").strip(_PUNCT)
    if not tok:
        return True
    lower = tok.lower()
    if lower in {"<", ">", "/", "s"}:
        return True
    if lower == "break" or lower.startswith("time="):
        return True
    if _TAG.match(tok) or _SSML_DURATION.match(tok):
        return True
    return False


def filter_spoken_timings(
    word_timings: list[tuple[str, float, float]] | None,
) -> list[tuple[str, float, float]]:
    """Keep spoken words, including years and figures. Drop SSML debris."""
    if not word_timings:
        return list(word_timings or [])
    kept: list[tuple[str, float, float]] = []
    for word, start, end in word_timings:
        if not word or is_ssml_timing_token(word):
            continue
        kept.append((word, start, end))
    return kept
