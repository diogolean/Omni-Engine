"""Failures seen on a real Gemini run: writer, mouth QA, TTS, duration, cache, paths, routes."""
from __future__ import annotations

import asyncio
import json
import re
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from products.animind_core import frameqa, llm_providers, script

H = {"Authorization": "Bearer test-key"}

# A bare array. The old greedy ``\{.*\}`` match runs from the first object to
# the last and json.loads raises Extra data (the shape seen in production).
BARE_ARRAY = """[
  {"i": 0, "text": "Does curiosity beat certainty?"},
  {"i": 1, "text": "A joke hides the premise."},
  {"i": 2, "text": "Explaining it shows the trick."},
  {"i": 3, "text": "So ask what the laugh was protecting."},
  {"i": 4, "text": "The punchline was doing that work."},
  {"i": 5, "text": "Leave the trick visible."},
  {"i": 6, "text": "Then the laugh still teaches."}
]
"""


def test_bare_array_used_to_raise_extra_data_and_now_parses():
    blob = re.search(r"\{.*\}", BARE_ARRAY, re.S).group(0)
    with pytest.raises(json.JSONDecodeError) as exc:
        json.loads(blob)
    assert "Extra data" in str(exc.value)
    lines = llm_providers._json_lines(BARE_ARRAY)
    assert [line["i"] for line in lines] == list(range(7))
    assert lines[0]["text"].startswith("Does curiosity")
    wrapped = llm_providers._json_lines('{"lines": [{"i": 0, "text": "Hi"}]}')
    assert wrapped == [{"i": 0, "text": "Hi"}]


def test_gemini_35_flash_lite_does_not_send_thinking_budget_zero(monkeypatch):
    from production.cost import google_guardrail as gg

    monkeypatch.setenv("ALLOW_GOOGLE_API", "true")
    monkeypatch.setenv("MAX_GOOGLE_COST_PER_RUN_USD", "1")
    monkeypatch.setenv("GOOGLE_COST_ALLOW_ABOVE_CEILING", "1")
    gg.reset_google_cost_tracker()
    spec = gg.cheap_text_config({"temperature": 0.8}, "gemini-3.5-flash-lite")
    assert spec["thinking_config"].get("thinking_budget") != 0
    assert spec["thinking_config"].get("thinking_level") == "minimal"
    # 2.5 Flash still turns thinking off. That model accepts budget 0.
    flash = gg.cheap_text_config({"temperature": 0.2}, "gemini-2.5-flash")
    assert flash["thinking_config"] == {"thinking_budget": 0}

    class _Models:
        def generate_content(self, **kwargs):
            thinking = kwargs["config"]["thinking_config"]
            if thinking.get("thinking_budget") == 0:
                raise RuntimeError("400 INVALID_ARGUMENT. Request contains an invalid argument.")
            return types.SimpleNamespace(
                text='{"lines": []}',
                usage_metadata=types.SimpleNamespace(prompt_token_count=8, candidates_token_count=4),
            )

    gg.guarded_generate_content(
        types.SimpleNamespace(models=_Models()),
        model="gemini-3.5-flash-lite",
        contents="write a script",
        config={"temperature": 0.8},
        source="unit",
    )


def test_deepseek_stays_the_default_writer_and_gemini_parses_a_bare_array(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANIMIND_FORCE_MOCK", raising=False)
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "1")
    order = llm_providers.provider_order(lambda _prompt: [])
    assert order[0].id == "deepseek"
    assert any(item.id == "google" for item in order)

    monkeypatch.setattr(script, "_gemini", lambda prompt: llm_providers._json_lines(BARE_ARRAY))
    out = script.write(
        "solo", "why jokes get explained", ["Albert Einstein"],
        tone="humor", duration=30, use_llm=True, sheets=[{"quotes": []}],
    )
    assert out["writer"] == "gemini"
    assert not out["lines"][0]["text"].startswith("No joke")
    assert out["lines"][0]["text"].startswith("Does curiosity")


def test_thirty_second_solo_is_longer_than_four_beats():
    out = script.write(
        "solo", "why jokes get explained", ["Albert Einstein"],
        tone="humor", duration=30, use_llm=False, sheets=[{"quotes": []}],
    )
    assert len(out["lines"]) > 4
    assert "explained" not in out["lines"][0]["text"].lower()
    assert "jokes" in out["lines"][0]["text"].lower()
    estimated = script.runtime_seconds(out["lines"])
    assert abs(estimated - 30) <= script.duration_tolerance(30), estimated
    # Without a duration the historical four-beat monologue stays put.
    assert len(script.plan_for("solo", 1)) == 4


def test_an_unfitting_quote_is_paraphrased_and_still_sourced():
    sheet = {"quotes": [{
        "id": "q-sea",
        "text": "The sea is calm tonight and very wide.",
        "source": "Poems, Dover Beach",
        "sourceKind": "primary",
        "url": "https://en.wikiquote.org/wiki/Matthew_Arnold",
        "work": "Dover Beach",
    }]}
    out = script.write(
        "solo", "why jokes get explained", ["Ada"],
        tone="humor", duration=30, use_llm=False, sheets=[sheet],
    )
    quotes = [line for line in out["lines"] if line["kind"] == "quote"]
    assert all("sea" not in line["text"].lower() for line in quotes)
    labelled = [line for line in out["lines"] if line.get("basis", {}).get("id") == "q-sea"]
    assert labelled, out["lines"]
    assert labelled[0]["basis"]["source"] == "Poems, Dover Beach"
    assert labelled[0]["kind"] == "paraphrase"
    for index in range(1, len(out["lines"])):
        assert out["lines"][index]["replyTo"]["line"] == index - 1
        assert out["lines"][index]["replyTo"]["idea"]


def test_measured_tts_lands_within_two_seconds_of_the_target():
    from products.animind_core import render

    lines = [
        {"text": "Curiosity opens the first question today.", "pauseAfterS": 0.4, "act": "abertura"},
        {"text": "Evidence has to survive a serious rival.", "pauseAfterS": 0.4, "act": "abertura"},
        {"text": "Practice shows where this claim breaks apart.", "pauseAfterS": 0.4, "act": "confronto"},
        {"text": "Doubt keeps every easy answer honest enough.", "pauseAfterS": 0.4, "act": "confronto"},
        {"text": "Method matters more than a loud slogan here.", "pauseAfterS": 0.35, "act": "fechamento"},
    ]
    # The last take is long, the way a 30s request came back at 33s.
    speech = [4.4, 4.5, 4.4, 4.5, 7.2]
    spoken = {}
    per_word = speech[-1] / len(lines[-1]["text"].split())

    def respeak(text: str) -> float:
        spoken["text"] = text
        return max(0.9, per_word * len(text.split()))

    fitted, durations = render.fit_measured_duration(lines, speech, 30.0, respeak)
    total = render.timeline_seconds(durations, fitted)
    assert abs(total - 30.0) <= render.MEASURED_TOLERANCE_S, total
    assert [line["text"] for line in fitted[:-1]] == [line["text"] for line in lines[:-1]]
    assert fitted[-1]["text"] != lines[-1]["text"]
    assert spoken["text"] == fitted[-1]["text"]


def test_tts_retries_no_audio_received(monkeypatch, tmp_path):
    from products.animind_core import render

    sleeps: list[float] = []

    async def _sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(render.asyncio, "sleep", _sleep)
    calls = {"n": 0}

    class NoAudioReceived(Exception):
        pass

    class _Communicate:
        def __init__(self, text, voice, rate="+0%", pitch="+0Hz"):
            self.text = text

        async def save(self, path):
            calls["n"] += 1
            if calls["n"] < 3:
                raise NoAudioReceived("No audio was received")
            Path(path).write_bytes(b"audio")

    module = types.ModuleType("edge_tts")
    module.Communicate = _Communicate
    monkeypatch.setitem(sys.modules, "edge_tts", module)
    dest = tmp_path / "line.mp3"
    asyncio.run(render._tts("Hello", "en-US-AriaNeural", dest))
    assert calls["n"] == 3
    assert dest.read_bytes() == b"audio"
    assert sleeps == [0.4, 1.2]


def test_mouth_qa_keeps_a_video_and_repairs_a_shut_viseme(tmp_path):
    from products.animind_core import render

    video = tmp_path / "preview.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftyp")
    report = {
        "pass": False,
        "badFrames": 14,
        "examples": [(3, "char 1 left B: open 0.09 vs drawn 0.00")],
    }
    settled = render.settle_frame_qa(video, report)
    assert settled["degraded"] is True
    assert "14" in settled["warnings"][0]
    assert "0.09" in settled["warnings"][0]
    missing = tmp_path / "missing.mp4"
    with pytest.raises(RuntimeError, match="frame mouth QA failed"):
        render.settle_frame_qa(missing, report)

    view = tmp_path / "left" / "mouths"
    view.mkdir(parents=True)
    Image.new("RGBA", (64, 24), (0, 0, 0, 0)).save(view / "B.png")
    notes = frameqa.repair_closed_visemes(view.parent)
    assert notes and "safe mouth" in notes[0]
    assert frameqa.happy_cavity(Image.open(view / "B.png")) >= frameqa.VISEME_CAV_MIN["B"]


def test_outputs_without_output_path_stay_outside_the_repo(monkeypatch):
    import shared.paths as paths

    monkeypatch.delenv("OUTPUT_PATH", raising=False)
    monkeypatch.delenv("OUTPUTS_DIR", raising=False)
    monkeypatch.setenv("OMNI_SKIP_DOTENV", "1")
    monkeypatch.setattr(paths, "_warned_local_fallback", False)
    root = paths.outputs_root().resolve()
    repo = paths.repo_root().resolve()
    assert root != (repo / "outputs").resolve()
    assert repo not in root.parents
    assert root.name == "outputs"


def test_preview_and_scenarios_match_the_app_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    from products.animind_core.app import app

    client = TestClient(app)
    preview = client.post("/v1/preview", headers=H, json={
        "character": {"name": "Ada", "prompt": "a calm teacher"},
        "characterType": "stick",
        "artStyle": "cartoon",
        "language": "en-US",
        "index": 0,
    })
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["source"] == "core" and body["kind"] == "stick" and body["name"] == "Ada"
    assert body["imageUrl"].endswith(".png")
    image = client.get("/v1/files/" + body["imageUrl"].split("/v1/files/")[1])
    assert image.status_code == 200 and image.content[:8] == b"\x89PNG\r\n\x1a\n"

    scenario = client.post("/v1/scenarios", headers=H, json={
        "mode": "auto", "characterNames": ["Albert Einstein", "Sócrates"],
    })
    assert scenario.status_code == 200, scenario.text
    assert scenario.json()["scenario"] == "agora" and scenario.json()["source"] == "core"
    prompted = client.post("/v1/scenarios", headers=H, json={
        "mode": "prompt", "prompt": "a starship above Mars", "characterNames": ["Ada"],
    })
    assert prompted.status_code == 200
    assert prompted.json()["scenario"] == "space"
    assert "built-in" in prompted.json()["note"]
    assert client.post("/v1/preview", json={}).status_code == 401


def _bust(flat: bool) -> Image.Image:
    from PIL import ImageDraw

    image = Image.new("RGBA", (80, 100), (255, 0, 255, 255) if flat else (30, 70, 160, 255))
    if not flat:
        ImageDraw.Draw(image).ellipse((20, 15, 60, 60), fill=(240, 210, 180, 255))
        ImageDraw.Draw(image).rectangle((15, 60, 65, 98), fill=(20, 40, 90, 255))
    return image


def test_a_failed_bust_does_not_replace_the_passing_cache(tmp_path, monkeypatch):
    from products.animind_core import avatar, image_models

    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TOGETHER_API_KEY", "not-a-real-key")
    calls = {"n": 0}

    def generate(self, prompt, **options):  # noqa: ARG001
        calls["n"] += 1
        dest = options["destination"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        _bust(flat=calls["n"] > 2).save(dest)
        return image_models.ImageResult(dest, "together", self.spec.model, 0.06)

    monkeypatch.setattr(image_models.TogetherImageProvider, "generate", generate)
    kwargs = dict(skin=tmp_path / "skin", url=lambda path: path.as_uri(), model_id="together-qwen-image")
    first = avatar.create_avatar("a teacher with a moustache", "cartoon", **kwargs)
    assert first["qa"]["score"] == 100 and calls["n"] == 2
    passed = (tmp_path / "data" / "avatar_cache").glob("*/passing/source_left.png")
    before = next(passed).read_bytes()
    avatar.create_avatar("a teacher with a moustache", "cartoon", reuse=False, **kwargs)
    assert calls["n"] == 4  # the failed attempt was paid once
    again = avatar.create_avatar("a teacher with a moustache", "cartoon", **kwargs)
    assert calls["n"] == 4  # the passing bust is reused; no third bill
    assert again["cached"] is True and again["qa"]["score"] == 100
    assert next((tmp_path / "data" / "avatar_cache").glob("*/passing/source_left.png")).read_bytes() == before


def test_avatar_image_cap_is_thirty_cents(tmp_path, monkeypatch):
    from products.animind_core import avatar, image_models

    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TOGETHER_API_KEY", "not-a-real-key")
    calls = {"n": 0}

    def generate(self, prompt, **options):  # noqa: ARG001
        calls["n"] += 1
        dest = options["destination"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        _bust(flat=False).save(dest)
        return image_models.ImageResult(dest, "together", self.spec.model, 0.20)

    monkeypatch.setattr(image_models.TogetherImageProvider, "generate", generate)
    kwargs = dict(skin=tmp_path / "skin", url=lambda path: path.as_uri(), model_id="together-qwen-image")
    avatar.create_avatar("a teacher", "cartoon", **kwargs)
    assert calls["n"] == 2
    avatar.create_avatar("a teacher", "cartoon", reuse=False, **kwargs)
    assert calls["n"] == 2  # 0.40 already spent; the next set would pass US$0.30
