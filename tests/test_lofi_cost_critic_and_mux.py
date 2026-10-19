# -*- coding: utf-8 -*-
"""Near-zero LOFI defaults, the cheap still critic and the bounded voice mux."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image


def test_lofi_default_backend_is_schnell(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.economic_reel_lofi import config as lofi_cfg
    from core.economic_reel_lofi import image_gen

    monkeypatch.delenv("LOFI_FLUX_BACKEND", raising=False)
    monkeypatch.setattr(lofi_cfg, "LOFI_FLUX_BACKEND", "schnell")
    assert lofi_cfg.uses_flux_dev() is False
    assert "schnell" in image_gen.LOFI_IMAGE_MODEL.lower()
    usd, meta = lofi_cfg.lofi_image_cost_per_call_usd()
    assert meta["backend"] == "schnell"
    assert usd * 9 < 0.05


def test_lofi_still_is_booked_in_the_spend_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agents.media.providers import together_image
    from core import spend_ledger
    from core.economic_reel_lofi import image_gen

    monkeypatch.delenv("LOFI_FLUX_BACKEND", raising=False)

    def _fake_generate(self, prompt, output_path, **_kw):  # noqa: ANN001
        Image.new("RGB", (720, 1280), (120, 90, 60)).save(output_path)
        return Path(output_path)

    monkeypatch.setattr(together_image.TogetherImageGenerator, "generate_image", _fake_generate)
    monkeypatch.setattr(together_image.TogetherImageGenerator, "__init__", lambda self, **_k: None)
    spend_ledger.reset_all()
    image_gen.generate_scene_image("a quiet kitchen at dawn", tmp_path / "s.png")
    rows = [r for r in spend_ledger.run_records() if r.kind == "image"]
    assert len(rows) == 1
    assert "schnell" in rows[0].model.lower()
    assert rows[0].cost_usd > 0


def test_critic_defaults_to_the_cheap_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("google.genai")
    from agents.media.providers import visual_eval
    from quality.VisualQA_Agent import visual_critic

    monkeypatch.delenv("VISUALQA_CRITIC_PROVIDER", raising=False)
    assert visual_critic.critic_provider() == "cheap"

    seen: dict = {}

    class _Backend:
        name = "openrouter"

        def evaluate(self, prompt, image_paths, *, labels=None):  # noqa: ANN001
            seen["n"] = len(image_paths)
            return visual_eval.VisualEvalResult(
                text=json.dumps({"score": 8.0, "passed": True, "flaws": []}),
                provider="openrouter",
                model_id="google/gemma-3-12b-it",
            )

    monkeypatch.setattr(visual_eval, "resolve_visual_eval_provider", lambda **_k: _Backend())
    img = tmp_path / "c.png"
    Image.new("RGB", (64, 64)).save(img)
    refs = []
    for i in range(4):
        r = tmp_path / f"r{i}.png"
        Image.new("RGB", (64, 64)).save(r)
        refs.append(r)
    verdict = visual_critic._cheap_critic_verdict(
        ["judge this"], img, refs, candidate_label="c", channel_name="lofi_economic"
    )
    assert verdict.passed is True and verdict.score == 8.0
    assert seen["n"] == 1 + visual_critic.CHEAP_CRITIC_MAX_REFS


def test_gemini_text_label_names_the_real_provider() -> None:
    from agents.media.providers.gemini_utils import CheapTextResponse

    resp = CheapTextResponse(
        "{}", provider="deepinfra", model="deepseek-ai/DeepSeek-V4-Flash", prompt=1, output=1
    )
    provider, _, model = resp.model_version.partition(":")
    assert (provider, model) == ("deepinfra", "deepseek-ai/DeepSeek-V4-Flash")


def test_tests_do_not_load_the_real_dotenv() -> None:
    import os

    import dotenv

    assert os.environ.get("OMNI_SKIP_DOTENV") == "1"
    assert dotenv.load_dotenv() is False


def test_voice_mux_is_bounded_by_the_video_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core import reel_ffmpeg_render as rr
    from tests.test_reel_voice_mux_and_sentence_beats import _stills, _tone

    cmds: list[list[str]] = []
    real_run = rr._run

    def _spy(cmd: list[str]) -> None:
        cmds.append(list(cmd))
        real_run(cmd)

    monkeypatch.setattr(rr, "_run", _spy)
    voice = tmp_path / "voice.wav"
    _tone(voice, 440.0, 1.2)
    rr.compile_ffmpeg_reel(
        image_paths=_stills(tmp_path),
        act_durations=[0.6, 0.6],
        output_path=tmp_path / "reel.mp4",
        voice_audio=voice,
        require_voice=True,
        synthesize_drone=False,
        workers=1,
    )
    mux = [c for c in cmds if "-filter_complex" in c and "-map" in c]
    assert mux, "voice mux command not seen"
    i = mux[-1].index("-t")
    assert abs(float(mux[-1][i + 1]) - 1.2) < 0.05
