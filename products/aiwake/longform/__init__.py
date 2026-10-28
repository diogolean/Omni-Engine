# -*- coding: utf-8 -*-
"""Horizontal YouTube episodes for Aiwake, plus the Shorts cut from them.

The debate, the judge, and the ffmpeg avatar renderer stay where they are.
This package plans the chapters, writes the YouTube metadata, cuts the
vertical teasers, and refuses a file that is not ready to publish.
"""
from __future__ import annotations

from .cost import CostEstimate, estimate_episode_cost
from .plan import EpisodePlan, build_episode_plan
from .qa import QAReport, duration_window
from .seats import resolve_longform_seats

__all__ = [
    "CostEstimate",
    "EpisodePlan",
    "QAReport",
    "build_episode_plan",
    "duration_window",
    "estimate_episode_cost",
    "resolve_longform_seats",
]
