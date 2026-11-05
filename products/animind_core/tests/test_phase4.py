import json

import pytest

from products.animind_core import image_models, llm_providers, script, stick


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{"message": {"content": json.dumps({"lines": [{"i": 0, "text": "A clear hook."}]})}}],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "prompt_cache_hit_tokens": 200,
            },
        }


def test_deepseek_openai_adapter_reports_conservative_cost(monkeypatch):
    seen = {}
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid/v1")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)

    def post(url, **kwargs):
        seen.update(url=url, kwargs=kwargs)
        return _Response()

    monkeypatch.setattr(llm_providers.httpx, "post", post)
    result = llm_providers.DeepSeekProvider().complete("prompt")
    assert seen["url"] == "https://example.invalid/v1/chat/completions"
    assert seen["kwargs"]["json"]["model"] == "deepseek-flash"
    assert seen["kwargs"]["json"]["thinking"] == {"type": "disabled"}
    assert result.lines == [{"i": 0, "text": "A clear hook."}]
    assert result.cost() == {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "costUsd": 0.0008412,
        "inputTokens": 1000,
        "outputTokens": 500,
        "cached": False,
        "status": "ok",
    }
    assert "not-a-real-key" not in json.dumps(result.cost())


def test_writer_router_falls_back_from_deepseek_to_gemini(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "configured")
    monkeypatch.setenv("GEMINI_API_KEY", "configured")
    monkeypatch.setattr(
        llm_providers.DeepSeekProvider,
        "complete",
        lambda self, prompt: (_ for _ in ()).throw(RuntimeError("temporary outage")),
    )
    gemini = lambda prompt: [{"i": 0, "text": "Fallback."}]
    monkeypatch.setattr(
        "production.cost.google_guardrail.get_google_cost_tracker",
        lambda: type("Tracker", (), {"snapshot": lambda self: {"entries": []}})(),
    )
    result, failures = llm_providers.generate("unique prompt", tmp_path, gemini)
    assert result is not None and result.provider == "google"
    assert result.lines[0]["text"] == "Fallback."
    assert failures == ["deepseek: temporary outage"]


def test_image_provider_registry_is_pluggable_but_paid_calls_are_disabled(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "configured")
    monkeypatch.setenv("FAL_KEY", "configured")
    assert isinstance(image_models.provider("nano-banana"), image_models.GeminiImageProvider)
    assert isinstance(image_models.provider("qwen-image"), image_models.QwenImageProvider)
    assert isinstance(image_models.provider("flux-pro"), image_models.FluxImageProvider)
    with pytest.raises(image_models.PaidImageGenerationDisabled):
        image_models.provider("qwen-image").generate("do not spend")
    public = {item["id"]: item for item in image_models.public_list()}
    assert public["qwen-image"]["gateway"] == "dashscope"
    assert public["flux-pro"]["billingUnit"] == "megapixel"
    assert all("keyEnv" not in item for item in public.values())


def test_three_hero_stick_presets_have_aiwake_bust_and_distinct_brows():
    brows = []
    for preset in ("teacher", "sage", "rebel"):
        spec = stick.PRESETS[preset]
        parts = stick._parts(  # generated in memory: no model and no paid call
            spec["skin"], spec["outfit"], spec["accessory"],
            head_shape=spec["head"], brow_style=spec["brow"], build=spec["build"],
        )
        assert parts["neck"][0].getbbox() is not None
        assert parts["body"][0].width >= 250
        brows.append(parts["brow_left"][0].tobytes())
    assert len(set(brows)) == 3


def test_thirty_second_debate_keeps_arc_in_five_beats():
    plan = script.plan_for("debate", 2, duration=30)
    assert len(plan) == 5
    assert [beat.speaker for beat in plan] == [0, 1, 0, 1, 0]
    assert {beat.act for beat in plan} >= {"abertura", "confronto", "virada", "fechamento"}
