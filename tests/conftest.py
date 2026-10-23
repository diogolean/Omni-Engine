# -*- coding: utf-8 -*-
"""Shared test defaults.

Production runs near-zero by default: Nano Banana is off and a pure-Python
gate screens stills before any vision model. Older tests exercise the Nano
Banana rungs and the LLM judge with tiny stub images, so they get the old
switches back here. ``tests/test_near_zero_cost.py`` clears both to test
the production defaults.
"""
from __future__ import annotations

import os

import pytest

# A developer machine keeps tokens, OUTPUT_PATH, and Drive ids in .env.
# Tests that need one of these set it themselves. The prefixes are the ones
# that made the Windows run pick a live pin, a live board, or a live ledger.
_HERMETIC_PREFIXES = (
    "PINTEREST_",
    "B2_",
    "AWS_",
    "GOOGLE_",
    "DRIVE_",
    "ELEVENLABS_",
    "ANTHROPIC_",
    "OPENAI_",
    "DEEPSEEK_",
    "GEMINI_",
    "TOGETHER_",
    "REPLICATE_",
    "FACEBOOK_",
    "YOUTUBE_",
    "TIKTOK_",
)
_HERMETIC_KEYS = (
    "OUTPUT_PATH",
    "OUTPUTS_DIR",
    "ASSETS_PATH",
    "FONT_DIRS",
    "FFMPEG_BINARY",
    "FFPROBE_BINARY",
    "LIBRARY_SOURCE",
    "MIN_INTERVAL_HOURS",
    "MAX_INTERVAL_HOURS",
    "POSTING_INTERVAL_HOURS",
    "USE_CLAUDE",
    "CLAUDE_MODEL",
    "OMNI_LEDGER_PATH",
    "OMNI_DRIVE_ROOT",
    "ACTIVE_PAGE",
)


def _portable_font(path: str) -> bool:
    """Keep repo and Linux fonts. Drop the Windows Fonts directory."""
    lowered = str(path).replace("\\", "/").lower()
    return "/windows/fonts/" not in lowered


@pytest.fixture(autouse=True)
def _hermetic_machine(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Drop the developer .env, ledger, and system fonts for this test."""
    monkeypatch.setenv("OMNI_SKIP_DOTENV", "1")
    for key in list(os.environ):
        if key.startswith(_HERMETIC_PREFIXES) or key in _HERMETIC_KEYS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "outputs"))
    try:
        from production.formats.pins import image_transformer as cards
    except Exception:  # noqa: BLE001
        return
    monkeypatch.setattr(
        cards,
        "_SERIF_CANDIDATES",
        [path for path in cards._SERIF_CANDIDATES if _portable_font(path)],
    )
    monkeypatch.setattr(
        cards,
        "_SANS_CANDIDATES",
        [path for path in cards._SANS_CANDIDATES if _portable_font(path)],
    )


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
