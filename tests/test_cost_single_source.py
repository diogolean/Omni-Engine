"""Prices and the reel cap each have one owner; values are unchanged."""
from __future__ import annotations

from core import cost_tracker, spend_ledger
from modules.reel_artefact import budget, pricing
from tools import audit_google_costs
from utils import gemini_usage


def test_flux_price_single_source():
    assert cost_tracker.FLUX_SCHNELL_USD_PER_IMAGE == pricing.FLUX_USD == 0.003


def test_flash_prices_single_source():
    assert audit_google_costs.FLASH_INPUT_USD_PER_1M is gemini_usage.FLASH_INPUT_USD_PER_1M
    assert (gemini_usage.FLASH_INPUT_USD_PER_1M, gemini_usage.FLASH_OUTPUT_USD_PER_1M) == (0.075, 0.30)


def test_reel_cap_has_one_owner(monkeypatch):
    monkeypatch.delenv("REEL_REEL_BUDGET_USD", raising=False)
    assert budget.reel_budget_usd() == spend_ledger.reel_cap_usd() == 0.05
    monkeypatch.setenv("REEL_REEL_BUDGET_USD", "0.08")
    assert budget.reel_budget_usd() == 0.08
    monkeypatch.setenv("REEL_REEL_BUDGET_USD", "-1")
    assert budget.reel_budget_usd() == 0.0
    monkeypatch.setenv("REEL_REEL_BUDGET_USD", "bad")
    assert budget.reel_budget_usd() == 0.05
