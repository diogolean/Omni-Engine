# -*- coding: utf-8 -*-
"""The cost record reports marginal spend; flat-plan usage is informational."""
from __future__ import annotations

import pytest

from core import spend_ledger
from core.cost_tracker import CostTracker


@pytest.fixture(autouse=True)
def _clean_ledger():
    spend_ledger.reset_all()
    yield
    spend_ledger.reset_all()


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
    assert entry["detail"]["top_text_model"] == "deepinfra:deepseek-ai/DeepSeek-V4-Flash"
    # The record carries every LLM call of the run, vision checks included.
    assert entry["cost_usd"] == pytest.approx(0.00112)
    assert tracker.category_totals()["text_label"].startswith("deepinfra:")


def test_script_without_ledger_calls_keeps_the_tier_estimate() -> None:
    tracker = CostTracker(page_id="ancient_knowledge", cost_tier="nano")
    tracker.track_text(char_count=4000)
    (entry,) = tracker.to_dict()["breakdown"]
    assert entry["model_key"] == "text_gemini_flash"


def test_record_uses_the_whole_run_ledger_not_the_estimate(monkeypatch) -> None:
    """s23d: estimate said text_gemini_flash $0.0003; the ledger saw $0.0035."""
    monkeypatch.delenv("ELEVENLABS_BILLING", raising=False)
    spend_ledger.record(provider="deepinfra", model="deepseek-ai/DeepSeek-V4-Flash",
                        cost_usd=0.0007, kind="text", input_tokens=1, output_tokens=1)
    spend_ledger.record(provider="openrouter", model="deepseek/deepseek-v4.1-flash",
                        cost_usd=0.0017, kind="text", input_tokens=1, output_tokens=1)
    spend_ledger.record(provider="openrouter", model="google/gemma-3-12b-it",
                        cost_usd=0.0011, kind="vision", input_tokens=1)
    tracker = CostTracker(page_id="ancient_knowledge", cost_tier="nano")
    tracker.track_text("text_gemini_flash", char_count=4000)
    tracker.track_image("image_flux_schnell", count=2)
    _reel(tracker)
    record = tracker.to_dict()
    text = [e for e in record["breakdown"] if e["operation"] == "text_generation"]
    assert len(text) == 1 and text[0]["model_key"] == "text_openrouter"
    assert record["marginal_usd"] == pytest.approx(0.0035 + 0.006)
    assert record["informational_usd"] == pytest.approx(0.06251)


def test_heavy_plan_usage_warns_but_stays_out_of_the_total(monkeypatch, caplog) -> None:
    monkeypatch.delenv("ELEVENLABS_BILLING", raising=False)
    monkeypatch.setenv("ELEVENLABS_LIST_WARN_USD", "0.05")
    tracker = CostTracker(page_id="ancient_knowledge", cost_tier="nano")
    _reel(tracker)
    with caplog.at_level("WARNING"):
        record = tracker.to_dict()
    assert record["plan_list_warning"] is True
    assert record["marginal_usd"] == 0.0
    assert "not counted toward the cap" in caplog.text
    monkeypatch.setenv("ELEVENLABS_LIST_WARN_USD", "0.20")
    assert tracker.to_dict()["plan_list_warning"] is False
