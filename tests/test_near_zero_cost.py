# -*- coding: utf-8 -*-
"""Near-zero cost defaults for the Ancient Knowledge reel (2026-10-19)."""
from __future__ import annotations

import random
from pathlib import Path

import pytest
from PIL import Image

_KEYS = (
    "OPENROUTER_API_KEY", "DEEPINFRA_API_KEY", "DEEPSEEK_API_KEY", "TOGETHER_API_KEY",
)


@pytest.fixture
def prod(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Production defaults: undo the legacy switches from conftest."""
    for name in (
        "REEL_NANOBANANA_MAX_PER_REEL", "REEL_LOCAL_GATE", "REEL_REEL_BUDGET_USD",
        "REEL_BEAT_BUDGET_USD", "REEL_QA_MODEL_CHAIN", "REEL_QA_FRAME_SAMPLES",
        "REEL_QA_JUDGE_RETRIES", "MAX_GOOGLE_COST_PER_RUN_USD", "REEL_LADDER",
        "GOOGLE_COST_ALLOW_ABOVE_CEILING", "CHEAP_TEXT_CHAIN", "CHEAP_VISION_CHAIN",
        *_KEYS,
    ):
        monkeypatch.delenv(name, raising=False)
    import config

    for name in _KEYS:
        monkeypatch.setattr(config, name, None, raising=False)
    from production.cost.spend_ledger import reset_all
    from production.formats.economic_reel.artefact.budget import reset_spend
    from production.formats.economic_reel.artefact.provider_health import reset_providers

    reset_all()
    reset_spend()
    reset_providers()
    return monkeypatch


def test_nano_banana_is_off_and_the_reel_cap_is_five_cents(prod) -> None:
    from production.formats.economic_reel.artefact.budget import beat_budget_usd, nano_cap, reel_budget_usd
    from production.formats.economic_reel.artefact.ladder import rung_names
    from production.formats.economic_reel.artefact.pricing import NANOBANANA_MODELS

    assert nano_cap() == 0
    assert reel_budget_usd() == pytest.approx(0.05)
    assert beat_budget_usd() == pytest.approx(0.02)
    assert rung_names() == ["real", "reuse", "flux"]
    assert "gemini-2.5-flash-image" not in NANOBANANA_MODELS
    prod.setenv("REEL_NANOBANANA_MAX_PER_REEL", "2")
    assert "nb-ref" in rung_names()


def test_judges_default_to_the_cheap_chain(prod) -> None:
    from production.qa.judges.visual_eval import OpenRouterVisualEval
    from production.qa.config import frame_samples, judge_retries, model_chain

    assert model_chain() == ["qwen"]
    assert frame_samples() == 2
    assert judge_retries() == 1
    chain = OpenRouterVisualEval(api_key="k")._model_chain()
    assert chain[0] == "google/gemma-3-12b-it"
    assert "qwen/qwen3.5-flash-02-23" not in chain


def test_gemini_cap_is_clamped_and_prices_count_thinking(prod) -> None:
    from production.cost import google_guardrail as gg

    prod.setenv("MAX_GOOGLE_COST_PER_RUN_USD", "5.00")
    assert gg.max_google_cost_usd() == pytest.approx(0.05)
    prod.setenv("GOOGLE_COST_ALLOW_ABOVE_CEILING", "1")
    assert gg.max_google_cost_usd() == pytest.approx(5.0)
    # 1M in + 1M out on 2.5 Flash is $2.80, not the old $0.375.
    assert gg.text_cost_usd(1_000_000, 1_000_000, model_id="models/gemini-2.5-flash") == (
        pytest.approx(2.80)
    )
    usage = {"usage_metadata": {
        "prompt_token_count": 100, "candidates_token_count": 50, "thoughts_token_count": 900,
    }}
    assert gg.extract_usage_tokens(usage) == (100, 950)


def test_gemini_text_config_turns_thinking_off(prod) -> None:
    pytest.importorskip("google.genai")
    from production.cost.google_guardrail import cheap_text_config

    cfg = cheap_text_config(None, "models/gemini-2.5-flash")
    assert cfg.thinking_config.thinking_budget == 0
    assert cfg.max_output_tokens == 4096
    pro = cheap_text_config(None, "models/gemini-2.5-pro")
    assert pro.thinking_config is None
    as_dict = cheap_text_config({"temperature": 0.2}, "gemini-2.5-flash")
    assert as_dict["thinking_config"] == {"thinking_budget": 0}


def test_reel_cap_covers_llm_calls_and_stills(prod) -> None:
    from production.cost import spend_ledger
    from production.formats.economic_reel.artefact.budget import reset_spend, try_spend

    spend_ledger.record(provider="openrouter", model="m", cost_usd=0.04)
    assert try_spend(0.003) is True
    assert try_spend(0.01) is False
    with pytest.raises(spend_ledger.SpendCapExceeded):
        spend_ledger.preflight(0.01, provider="deepinfra", model="m")
    reset_spend()
    assert spend_ledger.llm_spent() == 0.0
    assert spend_ledger.run_total() == pytest.approx(0.04)


def test_cheap_llm_takes_the_first_live_provider(prod) -> None:
    from production.providers.llm import cheap_llm
    from production.cost import spend_ledger

    prod.setenv("OPENROUTER_API_KEY", "or-test")
    prod.setenv("DEEPINFRA_API_KEY", "di-test")
    seen: list[str] = []

    def _post(url: str, key: str, body: dict, timeout: float) -> dict:
        seen.append(body["model"])
        if body["model"] == "deepseek-ai/DeepSeek-V4-Flash":
            return {"choices": [{"message": {"content": ""}}], "usage": {}}
        return {
            "choices": [{"message": {"content": '```json\n{"ok": true}\n```'}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 6, "cost": 0.00001},
        }

    prod.setattr(cheap_llm, "_post_json", _post)
    result = cheap_llm.complete("hi", json_mode=True, source="test")
    assert seen == ["deepseek-ai/DeepSeek-V4-Flash", "deepseek/deepseek-v4.1-flash"]
    assert result.text == '{"ok": true}'
    assert result.provider == "openrouter"
    assert spend_ledger.llm_spent() == pytest.approx(0.00001 + 0.0, abs=1e-6)


def test_cheap_llm_without_keys_is_unavailable(prod) -> None:
    from production.providers.llm import cheap_llm

    assert cheap_llm.available("text") is False
    with pytest.raises(cheap_llm.CheapLLMUnavailable):
        cheap_llm.complete("hi")


def test_text_only_gemini_request_is_served_by_the_cheap_chain(prod) -> None:
    from production.providers.llm import gemini_utils
    from production.providers.llm import cheap_llm

    prod.setenv("GEMINI_TEXT_CHEAP_FIRST", "force")
    prod.setenv("DEEPINFRA_API_KEY", "di-test")
    prod.setattr(cheap_llm, "_post_json", lambda *a, **k: {
        "choices": [{"message": {"content": "A short script."}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
    })

    class _NoGemini:
        class models:  # noqa: N801
            @staticmethod
            def generate_content(**_kwargs):
                raise AssertionError("Gemini must not be called")

    resp = gemini_utils.generate_content_with_model_fallback(
        _NoGemini(), ["models/gemini-2.5-flash"], contents=["Write it."],
    )
    assert resp.text == "A short script."
    assert resp.candidates[0].content.parts[0].text == "A short script."
    assert gemini_utils._text_only_blob([b"\x89PNG"]) is None
    # The live fallback tail can list an image model; the head decides.
    tail = ["models/gemini-2.5-flash", "models/gemini-nano-banana-2.1"]
    resp = gemini_utils.generate_content_with_model_fallback(
        _NoGemini(), tail, contents=["Write it."],
    )
    assert resp.provider == "deepinfra"


def _noise(path: Path, size=(800, 1400)) -> Path:
    rng = random.Random(7)
    image = Image.new("RGB", size)
    image.putdata([(rng.randrange(256),) * 3 for _ in range(size[0] * size[1])])
    image.save(path)
    return path


def test_local_gate_rejects_a_blank_frame_without_a_model(prod, tmp_path: Path) -> None:
    from production.qa.local_gate import check_still, reset_gate
    from production.qa.relevance import evaluate_chunk_relevance

    prod.setenv("REEL_LOCAL_EMBED", "off")
    reset_gate()
    flat = tmp_path / "flat.png"
    Image.new("RGB", (1080, 1920), (90, 90, 90)).save(flat)
    verdict = evaluate_chunk_relevance(flat, "The sea at dawn.", providers=["nobody"])
    assert verdict["hard_reject"] is True
    assert verdict["local_gate"]["reasons"][0].startswith("blur")
    sharp = _noise(tmp_path / "sharp.png")
    first = check_still(sharp)
    assert first.ok is True
    again = check_still(sharp)
    assert "duplicate" in again.reasons


def test_scene_library_reuses_an_approved_scene_after_the_cooldown(
    prod, tmp_path: Path,
) -> None:
    from production.formats.economic_reel.artefact import scene_library as lib

    prod.setenv("REEL_SCENE_LIBRARY_DIR", str(tmp_path / "lib"))
    still = _noise(tmp_path / "sea.png", size=(64, 112))
    lib.start_reel()
    lib.add("ancient_knowledge", still, scene_type="sea", geography="Aegean coast Greece",
            prompt="calm Aegean sea at dawn near Antikythera")
    assert lib.find("ancient_knowledge", scene_type="sea", geography="Aegean coast Greece",
                    prompt="Aegean sea dawn") is None  # used in this reel
    for _ in range(3):
        lib.start_reel()
    hit = lib.find("ancient_knowledge", scene_type="sea", geography="Aegean coast Greece",
                   prompt="Aegean sea at dawn")
    assert hit is not None and hit.is_file()
    assert lib.find("ancient_knowledge", scene_type="sky", geography="Aegean coast Greece",
                    prompt="Aegean sea at dawn") is None


def test_subscription_voice_is_logged_but_not_capped(monkeypatch) -> None:
    from production.cost import spend_ledger

    spend_ledger.reset_all()
    monkeypatch.setenv("REEL_REEL_BUDGET_USD", "0.05")
    spend_ledger.record_informational(
        provider="elevenlabs", model="eleven_multilingual_v2", characters=990, plan="pro",
    )
    assert spend_ledger.reel_total() == 0.0
    spend_ledger.preflight(0.049, provider="deepinfra", model="x")
    info = spend_ledger.summary()["informational"]
    assert info["characters"] == 990
    assert "elevenlabs:eleven_multilingual_v2" in info["by_model"]
    assert info["by_model"]["elevenlabs:eleven_multilingual_v2"]["cost_usd"] == 0.0
    spend_ledger.reset_all()
