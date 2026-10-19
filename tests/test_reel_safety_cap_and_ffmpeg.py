# -*- coding: utf-8 -*-
"""Safety cap, blank bands, ffmpeg fallback, and script length."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from agents.media.free_images.http import HttpResponse
from modules.reel_artefact.budget import (
    reset_spend,
    reset_uses,
    safety_uses,
    scene_regen_allowed,
    take_replan_slot,
)
from modules.reel_artefact.diversity import reset_diversity
from modules.reel_artefact.fetch import list_candidates
from modules.reel_artefact.flux_policy import fields_for_scene, object_line_strength
from modules.reel_artefact.pixels import image_defect
from modules.reel_artefact.reuse import (
    note_beat_key,
    remember_approved,
    rescue_place,
    reset_approved,
)
from modules.reel_artefact.script_length import fit_word_target
from modules.reel_artefact.source import write_source_sidecar
from modules.reel_qa.relevance import _verdict_from_payload, arid_water_conflict
from modules.shot_planner.entities import Entity, load_entities


def _reset() -> None:
    reset_spend()
    reset_uses()
    reset_diversity()
    reset_approved()


def _jpeg(path: Path, seed: int) -> None:
    import random

    rng = random.Random(seed)
    image = Image.new("RGB", (640, 960), (40, 80, 30))
    pixels = image.load()
    for y in range(0, 960, 8):
        for x in range(0, 640, 8):
            tone = (
                rng.randrange(20, 220),
                rng.randrange(20, 180),
                rng.randrange(20, 140),
            )
            for dy in range(8):
                for dx in range(8):
                    pixels[x + dx, y + dy] = tone
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="JPEG", quality=90)


def _place(path: Path, key: str) -> None:
    _jpeg(path, seed=abs(hash(key)) % 10000)
    write_source_sidecar(
        path,
        {
            "provider": "flux",
            "still_kind": "scene",
            "scene_type": "landscape",
            "reuse_key": key,
        },
    )
    remember_approved(path, kind="scene")


def test_the_next_beat_cannot_reuse_the_same_still(tmp_path: Path) -> None:
    _reset()
    origin = tmp_path / "valley.jpg"
    _place(origin, "valley")
    note_beat_key(0, "valley")
    assert rescue_place(tmp_path / "beside.jpg", 1, n_beats=8) is None
    saved = rescue_place(tmp_path / "later.jpg", 2, n_beats=8)
    assert saved is not None and saved.is_file()
    assert safety_uses() == 1


def test_safety_copies_stop_at_three_per_reel(tmp_path: Path) -> None:
    _reset()
    for index, beat in enumerate((0, 1, 2)):
        path = tmp_path / f"place_{index}.jpg"
        _place(path, f"place-{index}")
        note_beat_key(beat, f"place-{index}")
    placed = []
    for beat in (4, 6, 8, 10):
        saved = rescue_place(tmp_path / f"safe_{beat}.jpg", beat, n_beats=12)
        if saved is not None:
            placed.append(beat)
    assert placed == [4, 6, 8]
    assert safety_uses() == 3
    assert rescue_place(tmp_path / "safe_10.jpg", 10, n_beats=12) is None


def test_zero_replan_cap_does_not_keep_generating(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REEL_REPLAN_MAX_PER_REEL", "0")
    reset_spend()
    assert scene_regen_allowed("scene") is False


def test_a_spent_replan_cap_still_allows_a_cheap_landscape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REEL_REPLAN_MAX_PER_REEL", "2")
    monkeypatch.setenv("REEL_FLUX_MAX_PER_REEL", "10")
    monkeypatch.setenv("REEL_REEL_BUDGET_USD", "2")
    reset_spend()
    assert scene_regen_allowed("scene") is False
    assert take_replan_slot() is True
    assert take_replan_slot() is True
    assert scene_regen_allowed("scene") is True
    assert scene_regen_allowed("object") is False


def test_a_white_band_and_a_fog_frame_are_rejected_before_the_judge() -> None:
    varied = Image.new("RGB", (200, 400))
    pixels = varied.load()
    for y in range(400):
        for x in range(200):
            pixels[x, y] = ((x * 3 + y) % 255, (x * 5) % 200, (y * 2) % 180)
    assert image_defect(varied) == ""

    banded = varied.copy()
    for y in range(0, 80):
        for x in range(200):
            pixels_b = banded.load()
            pixels_b[x, y] = (255, 255, 255)
    assert image_defect(banded) == "band"

    dark = varied.copy()
    dark_px = dark.load()
    for y in range(320, 400):
        for x in range(200):
            dark_px[x, y] = (0, 0, 0)
    assert image_defect(dark) == "band"

    # A blown-out stripe inside the frame (bright sky or sea in an old
    # photograph) is part of the picture, not padding.
    interior = varied.copy()
    interior_px = interior.load()
    for y in range(160, 240):
        for x in range(200):
            interior_px[x, y] = (255, 255, 255)
    assert image_defect(interior) == ""

    fog = Image.new("RGB", (180, 320))
    fog_px = fog.load()
    for y in range(320):
        for x in range(180):
            tone = 206 + (x // 36) * 6 + (y // 64) * 4
            fog_px[x, y] = (tone, tone, tone)
    assert image_defect(fog) == "fog"

    blank = Image.new("RGB", (160, 240), (250, 250, 250))
    assert image_defect(blank) == "blank"


def test_pillars_and_reliefs_are_object_beats_and_arid_places_reject_a_lake() -> None:
    assert object_line_strength("The carved pillars still stand in the enclosure.") == 2
    assert object_line_strength("Stone reliefs cover the wall.") == 2
    assert object_line_strength("Scholars disagree about the lost record.") == 0
    gobekli = next(item for item in load_entities("ancient_knowledge") if item.id == "gobekli_tepe")
    assert "limestone hills" in gobekli.geography.lower()
    assert "farmland" in gobekli.geography.lower()
    assert "no lake" in gobekli.geography.lower()
    assert gobekli.coastal is False
    fields = fields_for_scene("landscape", gobekli)
    visual = fields["canonical_visual"].lower()
    assert "no lake" in visual
    folded = fields["subject"].lower()
    assert "şanlıurfa" in folded or "sanliurfa" in folded
    assert arid_water_conflict("circular temples beside a lake", gobekli.geography)
    assert arid_water_conflict("dry limestone and dust", gobekli.geography) is False
    detail = fields_for_scene("detail", gobekli)["canonical_visual"].lower()
    assert "no water" in detail


def test_a_site_photo_with_a_conservation_roof_is_not_an_anachronism(tmp_path: Path) -> None:
    image = tmp_path / "site.jpg"
    Image.new("RGB", (32, 32), (80, 70, 60)).save(image)
    kept = _verdict_from_payload(
        {
            "image_description": "limestone pillars under a modern roof and a walkway",
            "is_relevant": True,
            "relevance_score": 0.86,
            "anachronism": True,
            "detected_subjects": ["pillars"],
        },
        image_path=image,
        chunk_text="The carved pillars of the enclosure.",
        banned_subjects=None,
        provider="gemini",
        model_id="test",
        score_against="site",
        geography="an arid dry limestone plateau, no lake",
    )
    assert kept["is_relevant"] is True
    assert "anachronism" not in (kept.get("documentary_defects") or [])
    scuba = _verdict_from_payload(
        {
            "image_description": "divers in scuba gear beside the stones",
            "is_relevant": True,
            "relevance_score": 0.9,
            "anachronism": True,
        },
        image_path=image,
        chunk_text="The site.",
        banned_subjects=None,
        provider="gemini",
        model_id="test",
        score_against="site",
    )
    assert scuba["is_relevant"] is False
    assert "anachronism" in (scuba.get("documentary_defects") or [])
    lake = _verdict_from_payload(
        {
            "image_description": "circular temples beside a lake",
            "is_relevant": True,
            "relevance_score": 0.9,
        },
        image_path=image,
        chunk_text="The plateau.",
        banned_subjects=None,
        provider="gemini",
        model_id="test",
        score_against="shot",
        geography="an arid dry limestone plateau near Şanlıurfa, no lake and no river",
    )
    assert lake["is_relevant"] is False
    assert "setting_contradicts_beat" in (lake.get("documentary_defects") or [])


def test_commons_runs_before_openverse_and_openverse_is_only_a_fallback() -> None:
    hits = {"commons": 0, "openverse": 0}
    page = {
        "title": "File:Baghdad battery jar.jpg",
        "imageinfo": [{
            "url": "https://images.example/battery.jpg",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Baghdad_battery_jar.jpg",
            "width": 1600,
            "height": 1200,
            "mime": "image/jpeg",
            "extmetadata": {
                "LicenseShortName": {"value": "CC0"},
                "Artist": {"value": "Museum"},
            },
        }],
    }

    class _Client:
        def get(self, url, params=None, headers=None):
            del headers
            params = params or {}
            if "commons.wikimedia.org" in url or "wikimedia.org" in url:
                hits["commons"] += 1
                payload = {"query": {"pages": {"1": page}}}
                return HttpResponse(200, json.dumps(payload).encode())
            if "openverse.org" in url:
                hits["openverse"] += 1
                return HttpResponse(200, b'{"results":[]}')
            return HttpResponse(404, b"")

    entity = Entity(
        id="baghdad_battery",
        name="Baghdad Battery",
        aliases=("baghdad battery",),
        kind="artifact",
        canonical_visual="a clay jar",
        era="Parthian",
        setting="dry ground",
        confusions=(),
        channels=("ancient_knowledge",),
        commons_search=("Baghdad battery", "pile de Bagdad"),
    )
    found = list_candidates(entity, client=_Client())
    assert hits["commons"] >= 1
    assert hits["openverse"] == 0
    assert found

    class _EmptyCommons:
        def get(self, url, params=None, headers=None):
            del headers
            params = params or {}
            if "openverse.org" in url:
                hits["openverse"] += 1
                payload = {
                    "results": [{
                        "title": "Baghdad battery jar",
                        "license": "cc0",
                        "url": "https://images.example/battery.jpg",
                        "width": 1600,
                        "height": 1200,
                    }]
                }
                return HttpResponse(200, json.dumps(payload).encode())
            hits["commons"] += 1
            return HttpResponse(404, b"")

    hits["commons"] = 0
    hits["openverse"] = 0
    found = list_candidates(entity, client=_EmptyCommons())
    assert hits["openverse"] >= 1
    assert any(item.provider == "openverse" for item in found)


def test_a_long_script_keeps_whole_sentences_under_the_soft_ceiling() -> None:
    from modules.reel_artefact.script_length import soft_ceiling, soft_word_ceiling

    opening = "In 1936 archaeologists found a clay jar near Baghdad."
    sentence = "The jar held a copper tube and an iron rod."
    extra = " ".join([sentence] * 40)
    script = f"{opening} {extra}"
    assert len(script.split()) > 250
    kept = soft_ceiling(script)
    assert kept.startswith(opening)
    assert sentence in kept
    assert len(kept.split()) <= soft_word_ceiling()
    # The rewrite callback is ignored. Nothing is sliced mid-sentence.
    rewritten = fit_word_target(
        script,
        148,
        rewrite=lambda _body, _target, _low, _high: " ".join(["word"] * 140),
    )
    assert rewritten == kept
    assert kept.endswith(".")


def test_the_reel_planner_skips_the_second_gemini_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from modules.shot_planner.plan import plan_for_reel
    from tests.test_shot_planner_llm import _BCE, _bad_model_json

    monkeypatch.setenv("REEL_SHOT_PLANNER", "1")
    monkeypatch.setattr("modules.shot_planner.fit._llm_enabled", lambda: True)
    called = {"n": 0}

    def _ask(plan) -> None:
        called["n"] += 1
        del plan

    monkeypatch.setattr("modules.shot_planner.fit._ask_once", _ask)
    plan = plan_for_reel(
        channel="ancient_knowledge",
        topic="Lost OOPArts",
        script=_BCE,
        complete=lambda prompt, system=None: _bad_model_json(),
    )
    assert plan is not None
    assert called["n"] == 0


def test_ffmpeg_is_the_default_and_an_error_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.reel_ffmpeg_render import (
        ass_document,
        attempt_ffmpeg,
        ken_burns_filter,
        phrase_cues,
        sequence_renderer,
    )

    monkeypatch.delenv("REEL_RENDERER", raising=False)
    assert sequence_renderer() == "ffmpeg"
    monkeypatch.setenv("REEL_RENDERER", "moviepy")
    assert sequence_renderer() == "moviepy"
    assert attempt_ffmpeg(lambda: Path("should-not-run.mp4")) is None
    monkeypatch.setenv("REEL_RENDERER", "ffmpeg")
    monkeypatch.setattr("core.reel_ffmpeg_render.ffmpeg_exe", lambda: "ffmpeg")
    with pytest.raises(RuntimeError, match="no libass"):
        attempt_ffmpeg(lambda: (_ for _ in ()).throw(RuntimeError("no libass")))
    target = Path("reel.mp4")
    assert attempt_ffmpeg(lambda: target) == target
    graph = ken_burns_filter(0, 90)
    assert "scale=2160:3840" in graph
    assert "0.70*(1-pow(1-on/" in graph
    cues = phrase_cues(
        [("one", 1.0, 1.2), ("two", 1.2, 1.4), ("three", 1.4, 1.6), ("four", 1.6, 1.9)],
        hook_text="The jar",
        hook_until_s=2.0,
        cta_text="Follow",
        cta_start_s=8.0,
        end_s=10.0,
    )
    styles = [row[0] for row in cues]
    assert "Hook" in styles and "Sub" in styles and "Cta" in styles
    script = ass_document(cues)
    assert "Style: Hook" in script
    assert "&H0000E6FF" in script


@pytest.mark.skipif(
    not __import__("core.reel_ffmpeg_render", fromlist=["ffmpeg_has_libass"]).ffmpeg_has_libass(),
    reason="ffmpeg libass missing",
)
def test_a_short_ffmpeg_reel_writes_a_file(tmp_path: Path) -> None:
    from core.reel_ffmpeg_render import compile_ffmpeg_reel

    frames = []
    for index, color in enumerate(((120, 80, 40), (40, 90, 140))):
        path = tmp_path / f"still_{index}.jpg"
        Image.new("RGB", (320, 480), color).save(path, quality=85)
        frames.append(path)
    out = tmp_path / "sample.mp4"
    compile_ffmpeg_reel(
        image_paths=frames,
        act_durations=[0.4, 0.4],
        output_path=out,
        word_timings=[("stone", 0.1, 0.3)],
        hook_text="Hook",
        cta_text="Follow",
        cta_start_s=0.5,
        workers=2,
    )
    assert out.is_file()
    assert out.stat().st_size > 1000
