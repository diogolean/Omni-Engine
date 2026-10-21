# -*- coding: utf-8 -*-
"""Reel visual QA.

Trace:
  stills  -> ``retry.generate_and_gate`` (fail closed, N refined attempts)
  beats   -> ``beats.refine_reel_beats`` (one image per sentence group)
  final   -> ``pipeline.seal_compiled_reel`` (frames, audio, duration, JSON)
  publish -> ``publish.row_publishable`` (verdict must be ``pass``)

Imports stay lazy so a judge under ``production.qa.judges`` does not load the
mp4 gate.
"""
from __future__ import annotations

__all__ = [
    "StillGateResult",
    "is_reel_post_type",
    "report_allows_publish",
    "row_publishable",
    "seal_compiled_reel",
]


def __getattr__(name: str):
    if name == "StillGateResult":
        from production.qa.models import StillGateResult

        return StillGateResult
    if name in {"is_reel_post_type", "report_allows_publish", "row_publishable"}:
        from production.qa import publish

        return getattr(publish, name)
    if name == "seal_compiled_reel":
        from production.qa.pipeline import seal_compiled_reel

        return seal_compiled_reel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
