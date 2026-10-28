# -*- coding: utf-8 -*-
"""Horizontal YouTube episodes for Aiwake, plus the Shorts made from them.

The debate room, the voices, and the ffmpeg avatar renderer stay where they
are. This package plans the episode, directs it on the clock, writes the
YouTube copy and chapters, makes the vertical Shorts, prices the run, and
refuses a package that is not ready to publish.
"""
from __future__ import annotations

from .cost import CostActual, CostEstimate, actual_episode_cost, estimate_episode_cost
from .plan import EpisodePlan, build_episode_plan
from .qa import QAReport, duration_window
from .seats import resolve_longform_seats

__all__ = [
    "CostActual",
    "CostEstimate",
    "EpisodePlan",
    "QAReport",
    "actual_episode_cost",
    "build_episode_plan",
    "duration_window",
    "estimate_episode_cost",
    "resolve_longform_seats",
]
