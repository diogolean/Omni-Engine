# -*- coding: utf-8 -*-
"""Provider chain, Flux generic scenes, and the shared still/frame judge."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from agents.media.free_images.http import HttpResponse
from modules.reel_artefact.hook import wrap_reel_generate
from modules.reel_artefact.route import acquire_photo, generic_flux_prompt
from modules.reel_qa.frames import strip_subtitle_band
from modules.reel_qa.mp4_gate import SubtitleCue, beat_for_cue, gate_final_mp4
from modules.reel_qa.relevance import _verdict_from_payload
from modules.shot_planner.entities import Entity
from modules.shot_planner.render import render_shot
from modules.shot_planner.schema import Shot


def _jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (180, 140, 40)).save(buf, format="JPEG")
    return buf.getvalue()


def _page(title: str, width: int, height: int, license_name: str, url: str) -> dict:
    return {
        "title": title,
        "imageinfo": [
            {
                "url": url,
                "descriptionurl": "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_"),
                "width": width,
                "height": height,
                "mime": "image/jpeg",
                "extmetadata": {
                    "ObjectName": {"value": title.replace("File:", "")},
                    "LicenseShortName": {"value": license_name},
                    "Artist": {"value": "Example Photographer"},
                    "ImageDescription": {"value": title},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
                },
            }
        ],
    }


class _Client:
    def __init__(self, pages: dict, files: dict[str, bytes]) -> None:
        self.pages = pages
        self.files = files

    def get(self, url, params=None, headers=None):
        del headers
        params = params or {}
        if url in self.files:
            return HttpResponse(200, self.files[url])
        if params.get("titles"):
            payload = {"query": {"pages": self.pages["titles"]}}
            return HttpResponse(200, json.dumps(payload).encode())
        if params.get("gsrsearch"):
            payload = {"query": {"pages": self.pages["search"]}}
            return HttpResponse(200, json.dumps(payload).encode())
        return HttpResponse(404, b"")


def _entity() -> Entity:
    return Entity(
        id="sample",
        name="Sample artefact",
        aliases=("sample",),
        kind="artifact",
        canonical_visual="the object",
        era="ancient",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
        canonical_shape="a distinct shape",
        commons_search=("sample artefact",),
        commons_titles=("File:Small.jpg",),
    )


def test_chain_edits_the_reference_when_the_photo_is_too_small(tmp_path: Path) -> None:
    from modules.reel_artefact.budget import reset_nano_budget

    reset_nano_budget()
    url = "https://example.test/small.jpg"
    client = _Client(
        pages={
            "titles": {"1": _page("File:Small.jpg", 400, 300, "Public domain", url)},
            "search": {},
        },
        files={url: _jpeg(400, 300)},
    )

    def _generate(prompt: str, dest: Path, reference) -> str:
        assert reference is not None and Path(reference).is_file()
        assert "delta wings" in prompt
        assert "no propeller" in prompt
        assert "no cockpit" in prompt
        Image.new("RGB", (1080, 1920), (30, 30, 30)).save(dest, format="JPEG")
        return "gemini-3.1-flash-lite-image"

    dest = tmp_path / "out.jpg"
    path = acquire_photo(
        _quimbaya(),
        dest,
        channel="ancient_knowledge",
        mode="chain",
        client=client,
        cache_dir=tmp_path / "cache",
        generate_image=_generate,
    )
    assert path == dest and dest.is_file()
    side = json.loads(Path(str(dest) + ".source.json").read_text(encoding="utf-8"))
    assert side["provider"] == "nanobanana"
    assert side["model_id"] == "gemini-3.1-flash-lite-image"
    assert side["reference_image"]


def test_flux_beats_do_not_ask_for_the_artefact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REEL_ARTIFACT_IMAGES", "1")
    shot = Shot(
        id="s1",
        narration="An archaeologist works at dusk beside the excavation.",
        entity_id="quimbaya_ornaments",
        subject="Quimbaya golden ornaments",
        canonical_visual="small gold pendants with delta wings",
        shot_type="wide",
        camera="wide documentary view, open sky",
        setting="excavation at dusk",
        era="ancient",
        mood="quiet",
        lighting="dusk",
        image_role="flux",
        canonical_shape="a small gold pendant with delta wings, a tail fin, and a stylised head",
    )
    rendered = render_shot(
        shot,
        config={"style_lock": "", "hard_negatives": [], "identity_negatives": []},
    )
    positive = rendered.positive
    assert "documentary scene" in positive.lower()
    assert "delta wings" not in positive.lower()
    assert "no close-up" in generic_flux_prompt("Setting: a museum hall.").lower()


def test_flag_off_does_not_wrap_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REEL_ARTIFACT_IMAGES", raising=False)
    monkeypatch.delenv("REEL_ARTIFACT_IMAGE_PROVIDER", raising=False)
    sentinel = object()
    wrapped, extra = wrap_reel_generate(sentinel, channel="ancient_knowledge", shot=None)
    assert wrapped is sentinel
    assert extra == {}


def test_frame_judge_uses_the_beat_prompt_and_drops_subtitles(tmp_path: Path) -> None:
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"mp4")
    frame = tmp_path / "frame.jpg"
    Image.new("RGB", (90, 160), (255, 0, 0)).save(frame, format="JPEG")
    seen: dict = {}

    def _judge(path, text, prompt, **kwargs):
        seen["text"] = text
        seen["prompt"] = prompt
        seen["shape"] = kwargs.get("canonical_shape")
        with Image.open(path) as image:
            seen["size"] = image.size
        return {
            "is_relevant": True,
            "relevance_score": 0.95,
            "qa_status": "scored",
            "rejection_reason": None,
        }

    def _probe(path: Path) -> dict:
        del path
        return {
            "error": "",
            "duration_s": 22.0,
            "has_audio": True,
            "mean_volume_db": -16.0,
            "max_volume_db": -3.0,
        }

    def _grab(video_path: Path, at_s: float, dest: Path) -> Path:
        del video_path, at_s
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(frame.read_bytes())
        return dest

    cue = SubtitleCue(text="small gold pendant", start_s=0.5, end_s=1.5)
    decision = gate_final_mp4(
        video,
        [cue],
        probe_fn=_probe,
        evaluate_fn=_judge,
        frame_fn=_grab,
        work_dir=tmp_path / "frames",
        beats=[
            {
                "text": "A small gold pendant with delta wings sits in the case.",
                "prompt": "EXPECTED SUBJECT: Quimbaya golden ornaments.",
                "canonical_shape": "delta wings, tail fin, stylised head",
                "reference_image": "",
            }
        ],
    )
    assert decision["passed"] is True
    assert seen["prompt"].startswith("EXPECTED SUBJECT")
    assert "delta wings" in seen["text"]
    assert seen["shape"].startswith("delta wings")
    assert seen["size"][1] < 160
    assert beat_for_cue(cue, [{"text": "unrelated"}]) is None


def test_shape_mismatch_fails_the_still(tmp_path: Path) -> None:
    image = tmp_path / "still.jpg"
    Image.new("RGB", (32, 32), (0, 0, 0)).save(image, format="JPEG")
    verdict = _verdict_from_payload(
        {
            "image_description": "a museum photograph of a gold pendant",
            "is_relevant": True,
            "relevance_score": 0.95,
            "detected_subjects": ["pendant"],
            "shape_mismatch": True,
            "anachronism": False,
            "non_photoreal": False,
            "anatomy_defect": False,
            "setting_contradicts_beat": False,
        },
        image_path=image,
        chunk_text="The pendant.",
        banned_subjects=None,
        provider="test",
        model_id="test",
    )
    assert verdict["is_relevant"] is False
    assert "shape_mismatch" in verdict["rejection_reason"]


def test_subtitle_band_is_cropped_before_the_frame_judge(tmp_path: Path) -> None:
    src = tmp_path / "frame.jpg"
    Image.new("RGB", (100, 200), (1, 2, 3)).save(src, format="JPEG")
    clean = strip_subtitle_band(src, tmp_path / "clean.jpg")
    assert clean is not None
    assert Image.open(clean).size[1] == int(200 * 0.76)


def _quimbaya() -> Entity:
    return Entity(
        id="quimbaya_ornaments",
        name="Quimbaya golden ornaments",
        aliases=("quimbaya",),
        kind="artifact",
        canonical_visual="small gold pendants",
        era="pre-Columbian",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
        canonical_shape="a small gold pendant with delta wings, a tail fin, and a stylised head",
        commons_search=("Quimbaya gold pendant",),
        commons_titles=("File:Precolombina cultura prc.jpg",),
        text_only_safe=False,
        edit_negatives=(
            "no propeller",
            "no modern aircraft",
            "no straight wings",
            "no cockpit",
        ),
    )


def test_chain_publishes_a_large_photo_without_calling_nano(tmp_path: Path) -> None:
    url = "https://example.test/pendant.jpg"
    client = _Client(
        pages={
            "titles": {"1": _page("File:Precolombina cultura prc.jpg", 2000, 1400, "CC0", url)},
            "search": {},
        },
        files={url: _jpeg(2000, 1400)},
    )
    calls = {"n": 0}

    def _generate(prompt: str, dest: Path, reference) -> str:
        del prompt, dest, reference
        calls["n"] += 1
        return "gemini-3.1-flash-lite-image"

    dest = tmp_path / "out.jpg"
    path = acquire_photo(
        _quimbaya(),
        dest,
        channel="ancient_knowledge",
        mode="chain",
        client=client,
        cache_dir=tmp_path / "cache",
        generate_image=_generate,
    )
    assert path == dest and calls["n"] == 0
    side = json.loads(Path(str(dest) + ".source.json").read_text(encoding="utf-8"))
    assert side["provider"] == "commons"
    assert side["model_id"] == ""


def test_a_failed_edit_can_still_use_the_commons_frame(tmp_path: Path) -> None:
    url = "https://example.test/gears.jpg"
    client = _Client(
        pages={
            "titles": {"1": _page("File:Gears.jpg", 2000, 1400, "CC0", url)},
            "search": {},
        },
        files={url: _jpeg(2000, 1400)},
    )

    def _generate(prompt: str, dest: Path, reference) -> str:
        del prompt, dest, reference
        raise RuntimeError("GEMINI_API_KEY missing")

    dest = tmp_path / "out.jpg"
    path = acquire_photo(
        _entity(),
        dest,
        channel="ancient_knowledge",
        mode="chain",
        client=client,
        cache_dir=tmp_path / "cache",
        generate_image=_generate,
    )
    assert path == dest and dest.is_file()
    side = json.loads(Path(str(dest) + ".source.json").read_text(encoding="utf-8"))
    assert side["provider"] == "commons"


def test_text_only_runs_only_when_the_entity_marks_it_safe(tmp_path: Path) -> None:
    class _Miss:
        def get(self, url, params=None, headers=None):
            del url, params, headers
            return HttpResponse(404, b"")

    calls = {"n": 0}

    def _generate(prompt: str, dest: Path, reference) -> str:
        del prompt
        calls["n"] += 1
        assert reference is None
        Image.new("RGB", (768, 1344), (9, 9, 9)).save(dest, format="JPEG")
        return "gemini-2.5-flash-image"

    blocked = acquire_photo(
        _entity(),
        tmp_path / "blocked.jpg",
        channel="ancient_knowledge",
        mode="chain",
        client=_Miss(),
        cache_dir=tmp_path / "blocked-cache",
        generate_image=_generate,
    )
    assert blocked is None
    assert calls["n"] == 0
    allowed_entity = Entity(
        id="baghdad_battery",
        name="Baghdad Battery",
        aliases=("baghdad battery",),
        kind="artifact",
        canonical_visual="a clay jar",
        era="Parthian",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
        canonical_shape="a clay jar with a copper cylinder and an iron rod",
        text_only_safe=True,
    )
    allowed = acquire_photo(
        allowed_entity,
        tmp_path / "allowed.jpg",
        channel="ancient_knowledge",
        mode="chain",
        client=_Miss(),
        cache_dir=tmp_path / "allowed-cache",
        generate_image=_generate,
    )
    assert allowed is not None and calls["n"] == 1


def test_preview_ref_refuses_a_missing_reference(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from modules.reel_artefact.preview import main, resolve_entity

    found = resolve_entity("quimbaya", "ancient_knowledge")
    assert found is not None and found.id == "quimbaya_ornaments"
    assert found.text_only_safe is False
    monkeypatch.setattr("modules.reel_artefact.preview.fetch_artefact", lambda *_a, **_k: None)
    assert main(["antikythera", "--ref"]) == 1
    assert "Ref-edit refused" in capsys.readouterr().err


def test_preview_prints_the_reference_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from modules.reel_artefact.preview import main

    reference = tmp_path / "reference.jpg"
    Image.new("RGB", (40, 30), (1, 2, 3)).save(reference, format="JPEG")

    class _Fetched:
        reference_path = reference

    dest = tmp_path / "out.jpg"

    def _acquire(entity, path, **kwargs):
        del entity
        assert kwargs.get("mode") == "nanobanana"
        path.write_bytes(reference.read_bytes())
        Path(str(path) + ".source.json").write_text(
            json.dumps({
                "provider": "nanobanana",
                "model_id": "gemini-2.5-flash-image",
                "reference_image": str(reference),
            }),
            encoding="utf-8",
        )
        return path

    monkeypatch.setattr(
        "modules.reel_artefact.preview.fetch_artefact",
        lambda *_a, **_k: _Fetched(),
    )
    monkeypatch.setattr("modules.reel_artefact.preview.acquire_photo", _acquire)
    monkeypatch.setattr("utils.pipeline_paths.review_path", lambda *_parts: dest)
    assert main(["quimbaya", "--ref"]) == 0
    printed = capsys.readouterr().out
    assert "model=gemini-2.5-flash-image" in printed
    assert "reference=yes" in printed
    assert f"path={dest}" in printed


def test_google_image_cost_stays_on_the_reel_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from core.google_guardrail import (
        GoogleCostTracker,
        active_channel,
        bind_cost_channel,
        bind_run_channel,
        image_unit_cost_usd,
    )

    monkeypatch.setenv("ACTIVE_PAGE", "anna_protocol")
    monkeypatch.setenv("GEMINI_IMAGE_SIZE", "1K")
    bind_run_channel("ancient_knowledge")
    try:
        bind_cost_channel("ancient_knowledge")
        bind_cost_channel(None)
        assert active_channel() == "ancient_knowledge"
        seen: dict[str, str] = {}

        def _worker() -> None:
            seen["channel"] = active_channel()

        thread = threading.Thread(target=_worker)
        thread.start()
        thread.join()
        assert seen["channel"] == "ancient_knowledge"
        assert image_unit_cost_usd("models/gemini-2.5-flash-image") == 0.039
        assert image_unit_cost_usd("gemini-nano-banana-2.1") == 0.0336
        tracker = GoogleCostTracker()
        tracker.record(
            model="gemini-2.5-flash-image",
            kind="image",
            images=1,
            source="reel_artefact.nanobanana",
        )
        assert tracker._entries[-1].cost_usd == 0.039
        assert tracker._entries[-1].channel == "ancient_knowledge"
    finally:
        bind_run_channel(None)
        bind_cost_channel(None)


def test_a_second_beat_reuses_the_cached_edit(tmp_path: Path) -> None:
    from modules.reel_artefact.budget import reset_nano_budget
    from modules.reel_artefact.motion import motion_override

    reset_nano_budget()
    url = "https://example.test/small.jpg"
    client = _Client(
        pages={
            "titles": {"1": _page("File:Small.jpg", 400, 300, "Public domain", url)},
            "search": {},
        },
        files={url: _jpeg(400, 300)},
    )
    calls = {"n": 0}

    def _generate(prompt: str, dest: Path, reference) -> str:
        del prompt, reference
        calls["n"] += 1
        Image.new("RGB", (768, 1344), (12, 12, 12)).save(dest, format="JPEG")
        return "gemini-3.1-flash-lite-image"

    first = acquire_photo(
        _quimbaya(),
        tmp_path / "beat1.jpg",
        channel="ancient_knowledge",
        mode="chain",
        client=client,
        cache_dir=tmp_path / "cache",
        generate_image=_generate,
        beat_index=0,
    )
    second = acquire_photo(
        _quimbaya(),
        tmp_path / "beat2.jpg",
        channel="ancient_knowledge",
        mode="chain",
        client=client,
        cache_dir=tmp_path / "cache",
        generate_image=_generate,
        beat_index=1,
    )
    assert first is not None and second is not None
    assert calls["n"] == 1
    second_side = json.loads(Path(str(second) + ".source.json").read_text(encoding="utf-8"))
    assert second_side["provider"] == "reuse"
    assert motion_override(first)["profile"] != motion_override(second)["profile"]


def test_nano_cap_stops_a_fifth_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from modules.reel_artefact.budget import reset_nano_budget

    monkeypatch.setenv("REEL_NANOBANANA_MAX_PER_REEL", "1")
    reset_nano_budget()
    calls = {"n": 0}

    def _generate(prompt: str, dest: Path, reference) -> str:
        del prompt, reference
        calls["n"] += 1
        Image.new("RGB", (768, 1344), (3, 3, 3)).save(dest, format="JPEG")
        return "gemini-3.1-flash-lite-image"

    class _Miss:
        def get(self, url, params=None, headers=None):
            del url, params, headers
            return HttpResponse(404, b"")

    safe = Entity(
        id="baghdad_battery",
        name="Baghdad Battery",
        aliases=("baghdad battery",),
        kind="artifact",
        canonical_visual="a clay jar",
        era="Parthian",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
        canonical_shape="a clay jar with a copper cylinder and an iron rod",
        text_only_safe=True,
    )
    other = Entity(
        id="sample_two",
        name="Second jar",
        aliases=("second",),
        kind="artifact",
        canonical_visual="a clay jar",
        era="Parthian",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
        canonical_shape="a clay jar",
        text_only_safe=True,
    )
    first = acquire_photo(
        safe,
        tmp_path / "one.jpg",
        channel="ancient_knowledge",
        mode="chain",
        client=_Miss(),
        cache_dir=tmp_path / "one",
        generate_image=_generate,
    )
    second = acquire_photo(
        other,
        tmp_path / "two.jpg",
        channel="ancient_knowledge",
        mode="chain",
        client=_Miss(),
        cache_dir=tmp_path / "two",
        generate_image=_generate,
    )
    assert first is not None and second is None
    assert calls["n"] == 1


def test_commentary_uses_a_documentary_frame_not_the_artefact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from modules.reel_artefact.reuse import remember_approved, reset_approved

    monkeypatch.setenv("REEL_ARTIFACT_IMAGES", "1")
    monkeypatch.delenv("REEL_ARTIFACT_IMAGE_PROVIDER", raising=False)
    monkeypatch.setattr("modules.reel_artefact.route.fetch_gallery", lambda *_a, **_k: [])
    reset_approved()
    approved = tmp_path / "approved.jpg"
    Image.new("RGB", (1080, 1920), (40, 30, 20)).save(approved, format="JPEG")
    remember_approved(approved)
    shot = Shot(
        id="s8",
        narration="Some researchers believe these legends imply lost knowledge.",
        entity_id="quimbaya_ornaments",
        subject="Quimbaya golden ornaments",
        canonical_visual="small gold pendants",
        shot_type="wide",
        camera="wide documentary view",
        setting="a quiet museum hall",
        era="ancient",
        mood="quiet",
        lighting="soft",
        image_role="flux",
        canonical_shape="a small gold pendant with delta wings, a tail fin, and a stylised head",
    )
    calls = {"n": 0, "prompt": ""}

    def _generate(prompt, **kwargs):
        calls["n"] += 1
        calls["prompt"] = str(prompt or "")
        dest_path = Path(kwargs["output_directory"]) / f"{kwargs['output_stem']}.jpg"
        Image.new("RGB", (64, 96), (12, 12, 12)).save(dest_path, format="JPEG")
        return dest_path

    wrapped, _extra = wrap_reel_generate(
        _generate, channel="ancient_knowledge", shot=shot,
    )
    dest = wrapped(
        "A documentary scene.",
        output_stem="reel_act08_r01",
        output_directory=tmp_path,
    )
    assert calls["n"] == 1
    assert dest is not None and Path(dest).is_file()
    low = calls["prompt"].lower()
    assert "film grain" in low
    assert "cyberpunk" not in low
    assert "dystopian" not in low


def test_cheapest_nanobanana_model_is_first(monkeypatch: pytest.MonkeyPatch) -> None:
    from modules.reel_artefact.nanobanana import model_chain

    monkeypatch.delenv("REEL_NANOBANANA_MODEL", raising=False)
    monkeypatch.delenv("REEL_NANOBANANA_MODELS", raising=False)
    assert model_chain()[0] == "gemini-3.1-flash-lite-image"
    monkeypatch.setenv(
        "REEL_NANOBANANA_MODELS",
        "gemini-2.5-flash-image, gemini-3.1-flash-lite-image",
    )
    assert model_chain()[0] == "gemini-2.5-flash-image"
    monkeypatch.setenv("REEL_NANOBANANA_MODEL", "gemini-nano-banana-2.1")
    assert model_chain()[0] == "gemini-nano-banana-2.1"


def test_cost_lines_split_nano_flux_retries_real_and_tts() -> None:
    from core.cost_tracker import CostTracker

    tracker = CostTracker(page_id="ancient_knowledge")
    tracker.track_image("image_nanobanana_lite", count=1)
    tracker.track_image("image_flux_schnell", count=3)
    tracker.track_image("image_commons", count=2)
    tracker.track_image("image_reuse", count=17)
    tracker.track_audio(char_count=1200)
    cats = tracker.category_totals()
    assert cats["image_nano_count"] == 1
    assert cats["image_nano_cost"] == pytest.approx(0.0336)
    assert cats["image_flux_count"] == 3
    assert cats["image_flux_cost"] == pytest.approx(0.009)
    assert cats["image_real_count"] == 2
    assert cats["image_reuse_count"] == 17
    assert cats["image_cost"] == pytest.approx(0.0426)
    from core.cost_tracker import image_cost_lines

    rendered = "\n".join(image_cost_lines(cats))
    assert "Commons/real" in rendered and "(2 stills)" in rendered
    assert "Image Gen (reuse)" in rendered and "(17 stills)" in rendered
    assert "20 generation" not in rendered
    assert cats["tts_chars"] == 1200
    assert cats["audio_cost"] == pytest.approx(1200 * 0.00003)


def test_flux_retries_bill_and_a_real_photo_does_not(tmp_path: Path) -> None:
    from modules.reel_artefact.pricing import generation_bill
    from modules.reel_artefact.source import write_source_sidecar

    flux = tmp_path / "flux.jpg"
    real = tmp_path / "real.jpg"
    nano = tmp_path / "nano.jpg"
    for path in (flux, real, nano):
        Image.new("RGB", (8, 8), (1, 1, 1)).save(path, format="JPEG")
    write_source_sidecar(real, {"provider": "commons", "model_id": ""})
    write_source_sidecar(
        nano, {"provider": "nanobanana", "model_id": "gemini-3.1-flash-lite-image"},
    )
    assert generation_bill(flux, extra_generations=2) == ("image_flux_schnell", 3)
    assert generation_bill(real, extra_generations=2) == ("image_commons", 1)
    assert generation_bill(nano, extra_generations=2) == ("image_nanobanana_lite", 1)
