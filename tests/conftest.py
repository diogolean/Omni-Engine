# -*- coding: utf-8 -*-
"""Shared test defaults.

Production runs near-zero by default: Nano Banana is off and a pure-Python
gate screens stills before any vision model. Older tests exercise the Nano
Banana rungs and the LLM judge with tiny stub images, so they get the old
switches back here. ``tests/test_near_zero_cost.py`` clears both to test
the production defaults.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _legacy_reel_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REEL_NANOBANANA_MAX_PER_REEL", "4")
    monkeypatch.setenv("REEL_LOCAL_GATE", "0")
    monkeypatch.setenv("REEL_REEL_BUDGET_USD", "2.0")
    monkeypatch.setenv("REEL_BEAT_BUDGET_USD", "0.20")
    try:
        from core.spend_ledger import reset_all

        reset_all()
    except Exception:  # noqa: BLE001
        pass
    _reset_reel_state()


def _reset_reel_state() -> None:
    """Start every test as a fresh reel, the way the orchestrator starts one.

    Still-use counters, spend, diversity, approved stills and claimed pool
    photographs are module state for one reel. Without this, a test that ran a whole reel left its
    counters behind and the next ladder test skipped its reuse rung.
    """
    try:
        from modules.reel_artefact.asset_pool import reset_claims
        from modules.reel_artefact.budget import reset_nano_budget, reset_spend, reset_uses
        from modules.reel_artefact.diversity import reset_diversity
        from modules.reel_artefact.logbook import reset as reset_sources
        from modules.reel_artefact.reuse import reset_approved
    except Exception:  # noqa: BLE001
        return
    reset_claims()
    reset_nano_budget()
    reset_spend()
    reset_uses()
    reset_diversity()
    reset_sources()
    reset_approved()
