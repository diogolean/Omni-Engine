# -*- coding: utf-8 -*-
"""The cost record reports marginal spend; flat-plan usage is informational."""
from __future__ import annotations

import pytest

from core import spend_ledger
from core.cost_tracker import CostTracker


def _reel(tracker: CostTracker) -> None:
    tracker.track_audio(char_count=1017)
    tracker.track_sfx(calls=1)
    tracker.track_music(api=True)
    tracker.track_image("image_commons", count=11)


def test_plan_audio_is_informational_and_outside_the_total(monkeypatch) -> None:
    monkeypatch.delenv("ELEVENLABS_BILLING", raising=False)
    tracker = CostTracker(page_id="ancient_knowledge", cost_tier="nano")
    _reel(tracker)
    record = tracker.to_dict()
    assert record["total_estimated_usd"] == 0.0
    assert record["marginal_usd"] == 0.0
    assert record["informational_usd"] == pytest.approx(0.03051 + 0.002 + 0.03)
    plan = [e for e in record["breakdown"] if e.get("informational")]
    assert {e["model_key"] for e in plan} == {
        "elevenlabs_tts", "elevenlabs_sfx", "music_api_per_call",
    }
    assert all(e["cost_usd"] == 0.0 and e["billing"] == "subscription" for e in plan)


def test_metered_billing_restores_list_price(monkeypatch) -> None:
    monkeypatch.setenv("ELEVENLABS_BILLING", "metered")
    tracker = CostTracker(page_id="ancient_knowledge", cost_tier="nano")
    _reel(tracker)
    assert tracker.to_dict()["total_estimated_usd"] == pytest.approx(0.06251)


def test_local_music_is_zero_and_not_informational(monkeypatch) -> None:
    monkeypatch.delenv("ELEVENLABS_BILLING", raising=False)
    tracker = CostTracker(page_id="ancient_knowledge")
    tracker.track_music(api=False)
    (entry,) = tracker.to_dict()["breakdown"]
    assert entry["model_key"] == "music_local_per_bed"
    assert entry["cost_usd"] == 0.0 and not entry.get("informational")


def test_script_line_names_the_provider_that_served_it() -> None:
    tracker = CostTracker(page_id="ancient_knowledge", cost_tier="nano")
    spend_ledger.record(
        provider="deepinfra", model="deepseek-ai/DeepSeek-V4-Flash",
        cost_usd=0.00012, kind="text", input_tokens=900, output_tokens=300,
    )
    spend_ledger.record(
        provider="gemini", model="gemini-2.5-flash", cost_usd=0.001,
        kind="vision", input_tokens=10,
    )
    tracker.track_text(char_count=4000)
    (entry,) = tracker.to_dict()["breakdown"]
    assert entry["model_key"] == "text_deepinfra"
    assert entry["model"] == "deepseek-ai/DeepSeek-V4-Flash"
    assert entry["cost_usd"] == pytest.approx(0.00012)
    assert tracker.category_totals()["text_label"].startswith("deepinfra:")


def test_script_without_ledger_calls_keeps_the_tier_estimate() -> None:
    tracker = CostTracker(page_id="ancient_knowledge", cost_tier="nano")
    tracker.track_text(char_count=4000)
    (entry,) = tracker.to_dict()["breakdown"]
    assert entry["model_key"] == "text_gemini_flash"
