# -*- coding: utf-8 -*-
"""Give the closing CTA its own still instead of holding the last narration frame."""
from __future__ import annotations


def with_cta_beat(
    snippets: list[str],
    durations: list[float],
    cta_text: str,
    cta_hold: float,
) -> tuple[list[str], list[float], int | None]:
    """Return snippets, durations, and the CTA beat index.

    Audio-first timing folds the CTA hold into the last narration still, so
    the judge scores the CTA line against that artefact frame. Peel the hold
    back and append a CTA beat. When the last slot is already only the CTA
    window, replace its line with the CTA text.
    """
    text = " ".join((cta_text or "").split())
    snips = [str(item or "") for item in (snippets or [])]
    durs = [float(item or 0.0) for item in (durations or [])]
    if not text or not snips or not durs or len(snips) != len(durs):
        return snips, durs, None
    if " ".join(snips[-1].split()) == text:
        return snips, durs, len(snips) - 1
    hold = max(0.5, float(cta_hold or 0.0))
    if durs[-1] > hold + 0.4:
        durs[-1] = max(0.5, durs[-1] - hold)
        snips.append(text)
        durs.append(hold)
        return snips, durs, len(snips) - 1
    snips[-1] = text
    return snips, durs, len(snips) - 1
