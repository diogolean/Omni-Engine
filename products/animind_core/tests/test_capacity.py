"""Unpaid host sizing and official cost planning helpers."""
from __future__ import annotations

from products.animind_core.capacity import (
    OFFICIAL_PRICE_DATE,
    OFFICIAL_PRICE_URL,
    call_plan_payloads,
    estimate_character_cost,
    text_tokens,
)


def test_official_character_cost_plan_uses_composite_image_price():
    plan = estimate_character_cost("Ada Lovelace, cartoon bust")
    assert plan["ready"] is True
    assert plan["source"] == OFFICIAL_PRICE_URL
    assert plan["sourceDate"] == OFFICIAL_PRICE_DATE
    assert plan["tokenizer"]["providerCall"] is False
    assert plan["totals"]["imageCalls"] == 18
    assert plan["totals"]["estimatedMicro"] > 39_000 * 18  # input is charged too
    assert plan["totals"]["estimatedUsd"] != 0.156
    assert all(stage["promptBytes"] > 0 for stage in plan["stages"])


def test_call_plan_payloads_are_the_real_factory_prompts():
    stages = call_plan_payloads("subject")
    names = [stage.stage for stage in stages]
    assert names[0] == "master"
    assert "profile" in names
    assert "detect_master" in names
    assert text_tokens("abcd") == 1
