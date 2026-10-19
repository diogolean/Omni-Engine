# -*- coding: utf-8 -*-
"""Pipeline entrypoints.

Stage bodies live in ``engine.pipeline``. The names ``main.py`` and the
CLI already import stay here: ``produce``, ``run_pipeline``,
``_produce_variant_worker``, ``_produce_agentic``,
``_persist_agentic_postplanner``, and ``run_test_images_debug_mode``.
"""
from __future__ import annotations

from engine.pipeline.agentic import _persist_agentic_postplanner, _produce_agentic
from engine.pipeline.batch import produce, run_pipeline
from engine.pipeline.debug_images import run_test_images_debug_mode
from engine.pipeline.variant import _produce_variant_worker

__all__ = [
    "_persist_agentic_postplanner",
    "_produce_agentic",
    "_produce_variant_worker",
    "produce",
    "run_pipeline",
    "run_test_images_debug_mode",
]
