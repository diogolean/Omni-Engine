import json

import pytest

from products.animind_core import image_models as im


def test_default_is_nano_banana(monkeypatch):
    monkeypatch.delenv("ANIMIND_IMAGE_MODEL", raising=False)
    assert im.selected().model == "gemini-2.5-flash-image"


def test_flux_listed_but_unavailable_and_no_key_names(monkeypatch):
    monkeypatch.setenv("BFL_API_KEY", "x")
    lst = {m["id"]: m for m in im.public_list()}
    assert "flux-pro" in lst and lst["flux-pro"]["available"] is False
    assert "keyEnv" not in lst["flux-pro"]


def test_config_override_and_unknown(monkeypatch):
    monkeypatch.setenv("ANIMIND_IMAGE_MODELS", json.dumps({"flux-pro": {"credits": 2}}))
    assert im.selected("flux-pro").credits == 2
    with pytest.raises(ValueError):
        im.selected("nope")


def test_ledger_counts_per_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_FREE_CREDITS", "5")
    led = im.CreditLedger(tmp_path / "c.json")
    led.record("u", "nano-banana", 3, month="2026-10")
    row = led.record("u", "flux-pro", 1, month="2026-10")
    assert row == {"images": 1, "credits": 1, "usd": 0.04}
    assert led.used_credits("u", "2026-10") == 4 and led.free_left("u", "2026-10") == 1
