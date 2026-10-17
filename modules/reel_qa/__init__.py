# -*- coding: utf-8 -*-
"""Reel visual QA.

Trace:
  stills  -> ``retry.generate_and_gate`` (fail closed, N refined attempts)
  beats   -> ``beats.refine_reel_beats`` (one image per sentence group)
  final   -> ``pipeline.seal_compiled_reel`` (frames, audio, duration, JSON)
  publish -> ``publish.row_publishable`` (verdict must be ``pass``)
"""
from modules.reel_qa.models import StillGateResult
from modules.reel_qa.pipeline import seal_compiled_reel
from modules.reel_qa.publish import is_reel_post_type, report_allows_publish, row_publishable

__all__ = [
    "StillGateResult",
    "is_reel_post_type",
    "report_allows_publish",
    "row_publishable",
    "seal_compiled_reel",
]
