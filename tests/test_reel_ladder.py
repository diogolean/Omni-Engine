# -*- coding: utf-8 -*-
"""Fallback ladder: judged rungs, resume, caps, and the documentary Flux role."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from modules.reel_artefact.budget import (
    note_real_use,
    real_allowed,
    reel_spent,
    reset_nano_budget,
    reset_spend,
    reset_uses,
    still_uses,
)
from modules.reel_artefact.diversity import reset_diversity
from modules.reel_artefact.ladder import (
    RungContext,
    history_text,
    reprompt_from_defect,
    run_ladder,
    rung_names,
    rungs_for,
)
from modules.reel_artefact.reuse import remember_approved, reset_approved, reuse_into
from modules.reel_qa.config import RELEVANCE_THRESHOLD, relevance_threshold


def _jpeg(path: Path, color: tuple[int, int, int] = (40, 36, 28)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 96), color).save(path, format="JPEG")
    return path


def _fail() -> dict:
    return {
        "is_relevant": False,
        "relevance_score": 0.20,
        "qa_status": "scored",
        "rejection_reason": "documentary_fail:non_photoreal",
    }


def _pass(score: float = 0.91) -> dict:
    return {
        "is_relevant": True,
        "relevance_score": score,
        "qa_status": "scored",
        "rejection_reason": "",
    }


@pytest.fixture(autouse=True)
def _isolate_artefact_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Event downloads must not land in the channel's real artefact cache."""
    monkeypatch.setenv("REEL_ARTEFACT_CACHE_DIR", str(tmp_path / "artefact-cache"))


@pytest.fixture(autouse=True)
def _clean_reel_counters() -> None:
    reset_nano_budget()
    reset_spend()
    reset_uses()
    reset_approved()
    reset_diversity()
    yield
    reset_nano_budget()
    reset_spend()
    reset_uses()
    reset_approved()
    reset_diversity()


def test_judge_threshold_stays_at_070() -> None:
    assert RELEVANCE_THRESHOLD == 0.70
    assert relevance_threshold() >= 0.70


def test_flux_dev_is_not_on_the_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REEL_LADDER", "real,flux-dev,nb-ref,flux,reuse")
    assert rung_names() == ["real", "nb-ref", "flux", "reuse"]
    assert "flux-dev" not in rung_names()
    assert rungs_for("scene") == ["flux", "reuse"]


def test_a_beat_fails_every_rung_until_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REEL_LADDER", "real,nb-ref,nb-text,flux,reuse,extra")
    called: list[str] = []

    def _producer(name: str):
        def _make(ctx: RungContext) -> Path:
            called.append(name)
            return _jpeg(ctx.dest)

        return _make

    def _judge(path: Path, ctx: RungContext) -> dict:
        del ctx
        if "_reuse_" in path.name:
            return _pass(0.91)
        return _fail()

    result = run_ladder(
        beat_index=3,
        narration="The pendant sits in its case.",
        prompt="museum case",
        dest_dir=tmp_path,
        kind="object",
        text_only_safe=False,
        n_beats=10,
        producers={
            "real": _producer("real"),
            "nb-ref": _producer("nb-ref"),
            "nb-text": _producer("nb-text"),
            "flux": _producer("flux"),
            "reuse": _producer("reuse"),
            "extra": _producer("extra"),
        },
        judge=_judge,
    )
    assert result.passed is True
    assert result.rung == "reuse"
    assert result.first_pass is False
    assert result.score == pytest.approx(0.91)
    assert result.fail_reel is False
    assert called == ["real", "nb-ref", "flux", "reuse"]
    assert "nb-text" not in called
    assert "extra" not in called
    text = history_text(result.history)
    assert text == "real:fail,nb-ref:fail,nb-text:skip-unsafe,flux:fail,reuse:pass"
    assert any(
        row.rung == "nb-text" and row.status == "skip" and row.reason == "unsafe"
        for row in result.history
    )


def test_resume_skips_approved_beats(tmp_path: Path) -> None:
    saved = _jpeg(tmp_path / "approved.jpg")
    called = {"n": 0}

    class _Checkpoint:
        def approved_beat(self, index: int) -> dict | None:
            assert index == 2
            return {
                "path": str(saved),
                "rung": "flux",
                "kind": "scene",
                "approved": True,
                "first_pass": True,
                "score": 0.95,
                "reason": "",
                "verdict": _pass(0.95),
            }

    def _producer(ctx: RungContext) -> Path:
        del ctx
        called["n"] += 1
        raise AssertionError("approved beat was regenerated")

    result = run_ladder(
        beat_index=2,
        narration="A quiet museum hall.",
        prompt="museum hall",
        dest_dir=tmp_path / "fresh",
        kind="scene",
        producers={"flux": _producer, "reuse": _producer},
        judge=lambda _path, _ctx: _fail(),
        checkpoint=_Checkpoint(),
    )
    assert called["n"] == 0
    assert result.resumed is True
    assert result.passed is True
    assert result.path == saved
    assert result.first_pass is True
    assert result.score == pytest.approx(0.95)


def test_beat_budget_skips_nano_and_still_allows_flux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REEL_BEAT_BUDGET_USD", "0.01")
    monkeypatch.setenv("REEL_REEL_BUDGET_USD", "2")
    monkeypatch.setenv("REEL_LADDER", "nb-ref,flux")
    called: list[str] = []

    def _producer(name: str):
        def _make(ctx: RungContext) -> Path:
            called.append(name)
            return _jpeg(ctx.dest)

        return _make

    result = run_ladder(
        beat_index=0,
        narration="The pendant in its case.",
        prompt="museum case",
        dest_dir=tmp_path,
        kind="object",
        text_only_safe=False,
        producers={"nb-ref": _producer("nb-ref"), "flux": _producer("flux")},
        judge=lambda _path, _ctx: _pass(1.0),
    )
    assert called == ["flux"]
    assert result.passed is True
    assert result.rung == "flux"
    assert any(
        row.rung == "nb-ref" and row.reason == "beat-budget" for row in result.history
    )
    assert reel_spent() == pytest.approx(0.003)


def test_reel_budget_stops_the_next_flux_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REEL_REEL_BUDGET_USD", "0.004")
    monkeypatch.setenv("REEL_BEAT_BUDGET_USD", "0.20")
    monkeypatch.setenv("REEL_LADDER", "flux")

    def _flux(ctx: RungContext) -> Path:
        return _jpeg(ctx.dest)

    first = run_ladder(
        beat_index=0,
        narration="A landscape.",
        prompt="landscape",
        dest_dir=tmp_path / "a",
        kind="object",
        producers={"flux": _flux},
        judge=lambda _path, _ctx: _pass(0.90),
    )
    second = run_ladder(
        beat_index=1,
        narration="A museum hall.",
        prompt="museum hall",
        dest_dir=tmp_path / "b",
        kind="object",
        producers={"flux": _flux},
        judge=lambda _path, _ctx: _pass(0.90),
    )
    assert first.passed is True
    assert reel_spent() == pytest.approx(0.003)
    assert second.passed is False
    assert any(
        row.rung == "flux" and row.reason == "reel-budget" for row in second.history
    )
    assert history_text(second.history) == "flux:skip-reel-budget"


def test_reuse_cap_is_two_uses_including_the_original(tmp_path: Path) -> None:
    origin = _jpeg(tmp_path / "origin.jpg")
    remember_approved(origin, kind="object")
    key = str(origin.resolve())
    assert still_uses(key) == 1
    first = reuse_into(tmp_path / "copy1.jpg", 2, source=origin, kind="object", n_beats=20)
    assert first is not None and Path(first).is_file()
    assert still_uses(key) == 2
    second = reuse_into(tmp_path / "copy2.jpg", 2, source=origin, kind="object", n_beats=20)
    assert second is None
    assert still_uses(key) == 2


def test_reuse_fraction_blocks_the_fourth_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REEL_REUSE_MAX_PER_STILL", "10")
    monkeypatch.setenv("REEL_REUSE_MAX_FRACTION", "0.30")
    origin = _jpeg(tmp_path / "origin.jpg")
    made = []
    # Non-adjacent beats. The same still is not allowed on the beat beside it.
    for index, beat in enumerate((0, 2, 4, 6)):
        copied = reuse_into(
            tmp_path / f"copy{index}.jpg",
            beat,
            source=origin,
            kind="scene",
            n_beats=10,
        )
        made.append(copied)
    assert all(path is not None for path in made[:3])
    assert made[3] is None


def test_each_real_photo_repeats_at_most_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REEL_REAL_MAX_PER_PHOTO", "2")
    note_real_use("commons:antikythera-a")
    assert real_allowed("commons:antikythera-a") is True
    note_real_use("commons:antikythera-a")
    assert real_allowed("commons:antikythera-a") is False
    assert real_allowed("commons:antikythera-b") is True


def test_divers_and_commentary_are_scenes_not_artefact_photos() -> None:
    from modules.reel_artefact.role import beat_kind
    from modules.shot_planner.entities import Entity

    entity = Entity(
        id="antikythera",
        name="Antikythera mechanism",
        aliases=("antikythera",),
        kind="artifact",
        canonical_visual="corroded bronze fragments",
        era="2nd century BCE",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
    )
    divers = "Off Antikythera, Greece, divers recover the shipwreck from the sea."
    assert beat_kind(entity, narration=divers, setting="open sea") == "scene"
    workshop = "A craftsman in the workshop fits the gears."
    assert beat_kind(entity, narration=workshop) == "scene"
    commentary = "Some researchers believe these legends imply lost knowledge."
    assert beat_kind(entity, narration=commentary) == "scene"
    close = "The mechanism fills the frame in its museum case."
    assert beat_kind(entity, narration=close, shot_type="close") == "object"
    assert beat_kind(entity, narration=close, is_cta=True) == "scene"


def test_scene_ladder_never_calls_real_or_nano(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REEL_LADDER", "real,nb-ref,nb-text,flux,reuse")
    called: list[str] = []

    def _producer(name: str):
        def _make(ctx: RungContext) -> Path:
            called.append(name)
            return _jpeg(ctx.dest)

        return _make

    def _judge(path: Path, ctx: RungContext) -> dict:
        del ctx
        if path.name.endswith("_00.jpg"):
            return _fail()
        return _pass(0.95)

    result = run_ladder(
        beat_index=0,
        narration="Off Antikythera, Greece, divers work above the shipwreck.",
        prompt="Setting: open sea.",
        dest_dir=tmp_path,
        kind="scene",
        text_only_safe=False,
        n_beats=8,
        producers={
            "real": _producer("real"),
            "nb-ref": _producer("nb-ref"),
            "nb-text": _producer("nb-text"),
            "flux": _producer("flux"),
            "reuse": _producer("reuse"),
        },
        judge=_judge,
    )
    assert result.passed is True
    assert result.rung == "reuse"
    assert called == ["flux", "reuse"]
    assert history_text(result.history) == "flux:fail,reuse:pass"


def test_ak_flux_prompts_have_no_cyberpunk_anchor() -> None:
    from agents.media.providers.together_image import (
        flux_visual_role,
        sanitize_prompt_for_flux,
    )
    from modules.reel_artefact.route import generic_flux_prompt

    raw = generic_flux_prompt(
        "Setting: Desolate ancient landscape (e.g. a dry plateau at dusk)."
    )
    assert "(e.g. a dry plateau at dusk)" in raw
    assert "plateau" in raw
    role = flux_visual_role("ancient_knowledge")
    assert role == "documentary"
    cleaned = sanitize_prompt_for_flux(raw, visual_role=role).lower()
    assert "plateau" in cleaned
    assert "film grain" in cleaned
    assert "e.g." in cleaned
    for banned in ("cyberpunk", "dystopian", "ash rain", "crt"):
        assert banned not in cleaned
    slave = sanitize_prompt_for_flux("a quiet stone hall", visual_role="slave").lower()
    assert "cyberpunk" in slave


def test_defect_reprompt_stays_documentary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REEL_LADDER_LLM", "0")
    text = reprompt_from_defect(
        "Divers work above the wreck.",
        "old cyberpunk hall",
        "documentary_fail:non_photoreal",
    )
    low = text.lower()
    assert "photoreal" in low
    assert "photorealistic" not in low
    assert "35mm film grain" in low
    assert "natural colour" in low
    assert "real location" in low
    assert "divers work above the wreck" in low
    assert "the previous still was rejected: documentary_fail:non_photoreal" in low
    assert "real photograph, not a render" in low
    dated = reprompt_from_defect("A museum hall.", "", "anachronism").lower()
    assert "no modern objects" in dated


def test_cta_beat_gets_its_own_hold() -> None:
    from modules.reel_qa.cta_beat import with_cta_beat

    snips, durs, index = with_cta_beat(
        ["alpha", "beta"],
        [4.0, 8.0],
        "Follow Ancient Knowledge for more hidden mysteries.",
        4.5,
    )
    assert index == 2
    assert snips[-1] == "Follow Ancient Knowledge for more hidden mysteries."
    assert durs[-2] == pytest.approx(3.5)
    assert durs[-1] == pytest.approx(4.5)
    assert snips[0] == "alpha"
    assert snips[1] == "beta"


def test_reuse_cost_line_is_separate_from_commons() -> None:
    from core.cost_tracker import CostTracker, image_cost_lines

    tracker = CostTracker(page_id="ancient_knowledge")
    tracker.track_image("image_commons", count=2)
    tracker.track_image("image_reuse", count=17)
    cats = tracker.category_totals()
    assert cats["image_real_count"] == 2
    assert cats["image_reuse_count"] == 17
    rendered = "\n".join(image_cost_lines(cats))
    assert "Commons/real" in rendered
    assert "(2 stills)" in rendered
    assert "Image Gen (reuse)" in rendered
    assert "(17 stills)" in rendered
    assert "20 generation" not in rendered
    assert "FLUX Schnell" not in rendered


def test_a_failed_reuse_is_not_publishable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REEL_REPLAN_MAX", "0")
    monkeypatch.setenv("REEL_LADDER", "reuse")

    def _reuse(ctx: RungContext) -> Path:
        return _jpeg(ctx.dest)

    result = run_ladder(
        beat_index=4,
        narration="The pendant in its case.",
        prompt="museum case",
        dest_dir=tmp_path,
        kind="object",
        producers={"reuse": _reuse},
        judge=lambda _path, _ctx: _fail(),
    )
    assert result.passed is False
    assert result.kept is False
    assert result.path is None
    assert result.fail_reel is True
    assert result.score != 1.0


def test_replan_tries_a_new_concept_then_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REEL_REPLAN_MAX", "1")
    monkeypatch.setenv("REEL_LADDER_LLM", "0")
    monkeypatch.setenv("REEL_LADDER", "flux")
    seen: list[str] = []

    def _flux(ctx: RungContext) -> Path:
        seen.append(ctx.concept or ctx.prompt)
        return _jpeg(ctx.dest, (20 + len(seen), 30, 40))

    def _judge(path: Path, ctx: RungContext) -> dict:
        del path
        if ctx.replan:
            return _pass(0.92)
        return _fail()

    result = run_ladder(
        beat_index=0,
        narration="The pendant sits in its case.",
        prompt="museum case",
        dest_dir=tmp_path,
        kind="object",
        name="Quimbaya golden ornaments",
        producers={"flux": _flux},
        judge=_judge,
    )
    assert result.passed is True
    assert result.replans == 1
    assert result.first_pass is False
    assert result.kept is False
    assert "landscape" in seen[-1].lower() or "real place" in seen[-1].lower()
    assert "museum hall" not in seen[-1].lower()
    assert "complete mechanism" in seen[-1].lower()
    assert "gold figurine" in seen[-1].lower()


def test_replan_stops_when_the_beat_budget_is_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REEL_REPLAN_MAX", "2")
    monkeypatch.setenv("REEL_LADDER_LLM", "0")
    monkeypatch.setenv("REEL_BEAT_BUDGET_USD", "0.003")
    monkeypatch.setenv("REEL_LADDER", "flux")
    calls = {"n": 0}

    def _flux(ctx: RungContext) -> Path:
        calls["n"] += 1
        return _jpeg(ctx.dest, (9, 9, calls["n"]))

    result = run_ladder(
        beat_index=0,
        narration="A museum hall.",
        prompt="hall",
        dest_dir=tmp_path,
        kind="scene",
        producers={"flux": _flux},
        judge=lambda _path, _ctx: _fail(),
    )
    assert calls["n"] == 1
    assert result.passed is False
    assert result.fail_reel is True
    assert result.path is None
    assert result.kept is False


def test_accepted_rungs_drive_the_reel_counter() -> None:
    from modules.reel_artefact.logbook import note_accepted, note_source, reset, summary_line

    reset()
    note_source("nb-ref:gemini", "quimbaya", 1)
    note_source("nb-ref:gemini", "quimbaya", 2)
    note_accepted("flux")
    note_accepted("flux")
    note_accepted("reuse")
    note_accepted("event")
    line = summary_line()
    assert "flux=2" in line
    assert "reuse=1" in line
    assert "event=1" in line
    assert "nb-ref=0" in line


def test_cached_edit_is_reuse_and_a_third_copy_is_a_new_angle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from modules.reel_artefact.route import _variation
    from modules.shot_planner.entities import Entity

    monkeypatch.setenv("REEL_REUSE_MAX_PER_STILL", "2")
    monkeypatch.setenv("REEL_NANOBANANA_MAX_PER_REEL", "4")
    entity = Entity(
        id="quimbaya_ornaments",
        name="Quimbaya golden ornaments",
        aliases=("quimbaya",),
        kind="artifact",
        canonical_visual="small gold pendants",
        era="pre-Columbian",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
        canonical_shape="a small gold pendant",
        text_only_safe=False,
    )
    reference = _jpeg(tmp_path / "ref.jpg", (180, 140, 40))
    calls = {"n": 0, "prompts": []}

    def _generate(prompt: str, dest: Path, reference_image) -> str:
        del reference_image
        calls["n"] += 1
        calls["prompts"].append(prompt)
        Image.new("RGB", (64, 96), (calls["n"], 8, 8)).save(dest, format="JPEG")
        return "gemini-3.1-flash-lite-image"

    paths = []
    for index in range(3):
        made = _variation(
            entity,
            tmp_path / f"beat{index}.jpg",
            reference=reference,
            channel="ancient_knowledge",
            cache_dir=tmp_path / "cache",
            generate_image=_generate,
            license_name="",
            credit="",
            title=entity.name,
            source_url="",
            author="",
            kind="nb-ref",
            beat_index=index,
            slot=index,
            n_beats=19,
        )
        paths.append(made)
    assert paths[0] is not None and paths[1] is not None and paths[2] is not None
    assert calls["n"] == 2
    first = json.loads(Path(str(paths[0]) + ".source.json").read_text(encoding="utf-8"))
    second = json.loads(Path(str(paths[1]) + ".source.json").read_text(encoding="utf-8"))
    third = json.loads(Path(str(paths[2]) + ".source.json").read_text(encoding="utf-8"))
    assert first["provider"] == "nanobanana"
    assert second["provider"] == "reuse"
    assert third["provider"] == "nanobanana"
    assert first["image_hash"] == second["image_hash"]
    assert third["image_hash"] != first["image_hash"]
    assert "different framing" in calls["prompts"][1].lower()


def test_event_rungs_and_prompt_keep_the_artefact_out() -> None:
    from modules.reel_artefact.prompts import context_flux_prompt, event_flux_prompt

    assert rungs_for("scene", event=True) == ["event", "flux", "reuse"]
    text = event_flux_prompt(
        "In 1901, divers raised the shipwreck off Antikythera.",
        name="Antikythera Mechanism",
        period="1900-1901",
        negatives=("no modern scuba", "no modern boats"),
    ).lower()
    assert "1900-1901" in text
    assert "no modern scuba" in text
    assert "no modern boats" in text
    assert "do not depict antikythera mechanism" in text
    assert "complete mechanism" in text
    assert "film grain" in text
    context = context_flux_prompt(
        "The mechanism fills the case.",
        name="Antikythera Mechanism",
    ).lower()
    assert "landscape" in context or "real place" in context
    assert "museum hall" not in context
    assert "clean brass gears" in context
    assert "gold figurine" in context


def test_near_duplicate_frames_do_not_both_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REEL_REPLAN_MAX", "0")
    monkeypatch.setenv("REEL_LADDER", "flux")
    monkeypatch.setenv("REEL_STILL_HAMMING", "8")

    def _flux(ctx: RungContext) -> Path:
        return _jpeg(ctx.dest, (4, 4, 4))

    first = run_ladder(
        beat_index=0,
        narration="A crowd on the shore.",
        prompt="crowd",
        dest_dir=tmp_path / "a",
        kind="scene",
        producers={"flux": _flux},
        judge=lambda _path, _ctx: _pass(0.95),
    )
    second = run_ladder(
        beat_index=1,
        narration="Another crowd on the shore.",
        prompt="crowd again",
        dest_dir=tmp_path / "b",
        kind="scene",
        producers={"flux": _flux},
        judge=lambda _path, _ctx: _pass(0.95),
    )
    assert first.passed is True
    assert second.passed is False
    assert second.path is None
    assert any(row.reason == "near-duplicate" for row in second.history)


def test_ignore_checkpoint_does_not_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from modules.reel_artefact.checkpoint import ReelCheckpoint

    image = _jpeg(tmp_path / "saved.jpg")
    ckpt = ReelCheckpoint(tmp_path / "manifest.json")
    ckpt.save_beat(
        0,
        image=image,
        score=0.95,
        reason="",
        rung="flux",
        verdict=_pass(0.95),
        first_pass=True,
        kind="scene",
    )
    assert ckpt.approved_beat(0) is not None
    monkeypatch.setenv("REEL_CHECKPOINT", "ignore")
    assert ckpt.approved_beat(0) is None
    monkeypatch.setenv("REEL_CHECKPOINT", "clear")
    again = ReelCheckpoint.for_reel("ancient_knowledge", "smoke9-clear-test")
    if again.path.is_file():
        again.path.unlink()
    again.save_beat(
        0,
        image=image,
        score=0.95,
        reason="",
        rung="flux",
        verdict=_pass(0.95),
        first_pass=True,
        kind="scene",
    )
    ReelCheckpoint.for_reel("ancient_knowledge", "smoke9-clear-test")
    assert again.approved_beat(0) is None


def test_event_search_publishes_a_commons_still(tmp_path: Path) -> None:
    from agents.media.free_images.http import HttpResponse
    from modules.reel_artefact.events import acquire_event_photo
    from modules.shot_planner.entities import Entity

    entity = Entity(
        id="antikythera_mechanism",
        name="Antikythera Mechanism",
        aliases=("antikythera",),
        kind="artifact",
        canonical_visual="fragments",
        era="Hellenistic",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
        event_search=("Antikythera wreck sponge divers",),
        event_categories=("Category:Antikythera wreck",),
        event_period="1900-1901",
    )
    page = {
        "title": "File:Antikythera sponge divers 1901.jpg",
        "imageinfo": [{
            "url": "https://example.test/divers.jpg",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Divers.jpg",
            "thumburl": "https://example.test/divers.jpg",
            "width": 1600,
            "height": 1200,
            "mime": "image/jpeg",
            "extmetadata": {
                "ObjectName": {"value": "Antikythera sponge divers 1901"},
                "LicenseShortName": {"value": "Public domain"},
                "Artist": {"value": "Unknown"},
            },
        }],
    }

    class _Client:
        def get(self, url, params=None, headers=None):
            del headers
            params = params or {}
            if "divers.jpg" in url:
                return HttpResponse(200, _jpeg_bytes())
            if params.get("list") == "categorymembers":
                body = {"query": {"categorymembers": [
                    {"title": "File:Antikythera sponge divers 1901.jpg"},
                ]}}
                return HttpResponse(200, json.dumps(body).encode())
            if params.get("titles") or params.get("gsrsearch"):
                body = {"query": {"pages": {"1": page}}}
                return HttpResponse(200, json.dumps(body).encode())
            return HttpResponse(404, b"")

    dest = tmp_path / "event.jpg"
    made = acquire_event_photo(
        entity,
        dest,
        channel="ancient_knowledge",
        beat_index=1,
        n_beats=16,
        client=_Client(),
    )
    assert made == dest and dest.is_file()
    side = json.loads(Path(str(dest) + ".source.json").read_text(encoding="utf-8"))
    assert side["provider"] == "commons" or side["image_hash"].startswith("sha256:")
    assert side["image_hash"].startswith("sha256:")


def test_flux_scenes_come_from_the_channel_style_guide() -> None:
    from modules.reel_artefact.flux_policy import load_flux_policy

    policy = load_flux_policy("ancient_knowledge")
    allowed = " ".join(policy["allowed"]).lower()
    forbidden = " ".join(policy["forbidden"]).lower()
    assert "landscape" in allowed
    assert "aerial" in allowed or "ruins" in allowed
    assert "manuscript" not in allowed
    assert "museum" not in allowed
    assert "sea" in allowed
    assert "named artefact" in forbidden
    assert "gear" in forbidden


def test_object_flux_is_a_context_scene_and_keeps_edit_negatives(tmp_path: Path) -> None:
    from modules.reel_artefact.climb import _flux
    from modules.shot_planner.entities import Entity

    entity = Entity(
        id="antikythera_mechanism",
        name="Antikythera Mechanism",
        aliases=("antikythera",),
        kind="artifact",
        canonical_visual="corroded fragments",
        era="Hellenistic",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
        edit_negatives=("never intact gears", "corroded fragments only"),
    )
    seen: list[str] = []

    def _generate(text: str, **kwargs: object) -> str:
        del kwargs
        seen.append(text)
        return str(_jpeg(tmp_path / "out.jpg"))

    ctx = RungContext(
        beat_index=0,
        narration="The gears are cut with a precision that still surprises researchers.",
        prompt="draw a complete mechanism of clean brass gears",
        dest=tmp_path / "x.jpg",
        kind="object",
    )
    made = _flux(_generate, {}, entity)(ctx)
    assert made is not None
    prompt = seen[0].lower()
    head = prompt.split("do not depict")[0]
    assert "gear" not in head
    assert "pendant" not in head
    assert "landscape" in prompt or "aerial" in prompt or "coast" in prompt
    assert "museum hall" not in prompt
    assert "manuscript" not in prompt
    assert "never intact gears" in prompt
    assert "complete mechanism" in prompt
    assert "gold bird" in prompt


def test_flux_cap_stops_another_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REEL_FLUX_MAX_PER_REEL", "0")
    monkeypatch.setenv("REEL_REPLAN_MAX", "0")
    monkeypatch.setenv("REEL_LADDER", "flux")
    called = {"n": 0}

    def _flux(ctx: RungContext) -> Path:
        called["n"] += 1
        return _jpeg(ctx.dest)

    result = run_ladder(
        beat_index=0,
        narration="A wide landscape.",
        prompt="landscape",
        dest_dir=tmp_path,
        kind="scene",
        producers={"flux": _flux},
        judge=lambda _path, _ctx: _pass(0.95),
    )
    assert called["n"] == 0
    assert result.passed is False
    assert any(row.reason == "flux-cap" for row in result.history)


def test_replan_cap_stops_another_concept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REEL_REPLAN_MAX", "2")
    monkeypatch.setenv("REEL_REPLAN_MAX_PER_REEL", "0")
    monkeypatch.setenv("REEL_LADDER_LLM", "0")
    monkeypatch.setenv("REEL_LADDER", "flux")

    def _flux(ctx: RungContext) -> Path:
        return _jpeg(ctx.dest, (9, 9, 9))

    result = run_ladder(
        beat_index=2,
        narration="Cicero called it a lost blueprint.",
        prompt="intact brass gears",
        dest_dir=tmp_path,
        kind="scene",
        producers={"flux": _flux},
        judge=lambda _path, _ctx: _fail(),
    )
    assert result.passed is False
    assert result.replans == 0
    assert all("_p" not in (row.path or "") for row in result.history)


def test_a_blank_download_is_not_an_event_photo(tmp_path: Path) -> None:
    from modules.reel_artefact.events import acquire_event_photo
    from modules.reel_artefact.pixels import image_defect
    from modules.shot_planner.entities import Entity

    blank = Image.new("RGB", (1600, 1200), (250, 250, 250))
    assert image_defect(blank, nbytes=9000) == "blank"
    banner = Image.new("RGB", (2000, 200))
    pixels = banner.load()
    for y in range(200):
        for x in range(0, 2000, 8):
            pixels[x, y] = (x % 255, 40, 40)
    assert image_defect(banner) == "aspect"

    entity = Entity(
        id="blank_event_probe",
        name="Blank probe",
        aliases=("blank probe",),
        kind="artifact",
        canonical_visual="fragments",
        era="Hellenistic",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
        event_search=("Antikythera shipwreck 1901",),
        event_categories=("Category:Antikythera wreck",),
    )

    class _Client:
        def get(self, url, params=None, headers=None):
            from agents.media.free_images.http import HttpResponse

            del headers
            params = params or {}
            if url.endswith("blank.jpg"):
                buf_image = Image.new("RGB", (1600, 1200), (240, 240, 240))
                import io
                buf = io.BytesIO()
                buf_image.save(buf, format="JPEG")
                return HttpResponse(200, buf.getvalue())
            if params.get("list") == "categorymembers":
                body = {"query": {"categorymembers": [
                    {"title": "File:Blank placeholder.jpg"},
                ]}}
                return HttpResponse(200, json.dumps(body).encode())
            body = {"query": {"pages": {"1": {
                "title": "File:Blank placeholder.jpg",
                "imageinfo": [{
                    "url": "https://example.test/blank.jpg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:Blank.jpg",
                    "width": 1600,
                    "height": 1200,
                    "mime": "image/jpeg",
                    "extmetadata": {
                        "LicenseShortName": {"value": "Public domain"},
                        "Artist": {"value": "Unknown"},
                        "ObjectName": {"value": "Blank placeholder"},
                    },
                }],
            }}}}
            return HttpResponse(200, json.dumps(body).encode())

    made = acquire_event_photo(
        entity,
        tmp_path / "event.jpg",
        channel="ancient_knowledge",
        client=_Client(),
    )
    assert made is None


def test_event_cc_by_credit_is_written(tmp_path: Path) -> None:
    from modules.reel_artefact.events import acquire_event_photo
    from modules.shot_planner.entities import Entity

    entity = Entity(
        id="antikythera_mechanism",
        name="Antikythera Mechanism",
        aliases=("antikythera",),
        kind="artifact",
        canonical_visual="fragments",
        era="Hellenistic",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
        event_search=("Antikythera shipwreck 1901",),
        event_categories=("Category:Antikythera wreck",),
    )
    page = {
        "title": "File:Anticythera shipwreck. Standard diving dress.jpg",
        "imageinfo": [{
            "url": "https://example.test/dress.jpg",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Dress.jpg",
            "thumburl": "https://example.test/dress.jpg",
            "width": 1600,
            "height": 1200,
            "mime": "image/jpeg",
            "extmetadata": {
                "ObjectName": {"value": "Anticythera shipwreck standard diving dress"},
                "LicenseShortName": {"value": "CC BY-SA 3.0"},
                "Artist": {"value": "Example Diver"},
            },
        }],
    }

    class _Client:
        def get(self, url, params=None, headers=None):
            from agents.media.free_images.http import HttpResponse

            del headers
            params = params or {}
            if "dress.jpg" in url:
                return HttpResponse(200, _jpeg_bytes())
            if params.get("list") == "categorymembers":
                body = {"query": {"categorymembers": [{"title": page["title"]}]}}
                return HttpResponse(200, json.dumps(body).encode())
            body = {"query": {"pages": {"1": page}}}
            return HttpResponse(200, json.dumps(body).encode())

    dest = tmp_path / "event.jpg"
    made = acquire_event_photo(
        entity, dest, channel="ancient_knowledge", beat_index=0, client=_Client(),
    )
    assert made == dest
    side = json.loads(Path(str(dest) + ".source.json").read_text(encoding="utf-8"))
    assert side["credit_required"] is True
    assert "CC BY-SA" in side["credit_line"]
    assert "Example Diver" in side["credit_line"]


def test_final_frame_trusts_the_approved_still(tmp_path: Path) -> None:
    from modules.reel_qa.mp4_gate import SubtitleCue, gate_final_mp4

    still = _jpeg(tmp_path / "still.jpg", (30, 80, 40))

    def _frame(video: Path, at_s: float, dest: Path) -> Path:
        del video, at_s
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(still.read_bytes())
        return dest

    def _judge(*args: object, **kwargs: object) -> dict:
        raise AssertionError(args or kwargs)

    decision = gate_final_mp4(
        tmp_path / "reel.mp4",
        [SubtitleCue(text="birds but some researchers", start_s=24.0, end_s=26.8)],
        probe_fn=lambda _path: {
            "error": "",
            "duration_s": 40.0,
            "has_audio": True,
            "mean_volume_db": -16.0,
        },
        evaluate_fn=_judge,
        frame_fn=_frame,
        beats=[{
            "index": 4,
            "text": "These look like birds. But some researchers disagree.",
            "passed": True,
            "image_path": str(still),
            "score": 0.91,
            "prompt": "a museum archive",
        }],
    )
    assert decision["passed"] is True
    assert decision["frames"][0]["reason"] == "beat-approved"
    assert decision["frames"][0]["beat_index"] == 4


def test_a_different_final_frame_names_the_beat(tmp_path: Path) -> None:
    from modules.reel_qa.mp4_gate import SubtitleCue, gate_final_mp4

    still = tmp_path / "still.jpg"
    other = tmp_path / "other.jpg"
    _bar(still, horizontal=False)
    _bar(other, horizontal=True)

    def _frame(video: Path, at_s: float, dest: Path) -> Path:
        del video, at_s
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(other.read_bytes())
        return dest

    decision = gate_final_mp4(
        tmp_path / "reel.mp4",
        [SubtitleCue(text="birds but some researchers", start_s=24.0, end_s=26.8)],
        probe_fn=lambda _path: {
            "error": "",
            "duration_s": 40.0,
            "has_audio": True,
            "mean_volume_db": -16.0,
        },
        evaluate_fn=lambda *_args, **_kwargs: _fail(),
        frame_fn=_frame,
        beats=[{
            "index": 7,
            "text": "These look like birds. But some researchers disagree.",
            "passed": True,
            "image_path": str(still),
            "score": 0.91,
            "prompt": "archive",
        }],
    )
    assert decision["passed"] is False
    assert "frame_subtitle_mismatch" in decision["reasons"]
    assert decision["frames"][0]["beat_index"] == 7


def _bar(path: Path, *, horizontal: bool) -> None:
    image = Image.new("RGB", (64, 96), (12, 12, 12))
    pixels = image.load()
    for y in range(96):
        for x in range(64):
            if horizontal and y < 10:
                pixels[x, y] = (230, 20, 20)
            if not horizontal and x < 10:
                pixels[x, y] = (20, 220, 220)
    image.save(path, format="JPEG")


def test_once_stops_after_the_first_judged_still(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REEL_LADDER", "flux,flux,reuse")
    monkeypatch.setenv("REEL_REPLAN_MAX", "2")
    calls = {"n": 0}

    def _flux(ctx: RungContext) -> Path:
        calls["n"] += 1
        return _jpeg(ctx.dest, (30, 40, calls["n"]))

    result = run_ladder(
        beat_index=0,
        narration="Cicero called it a lost blueprint.",
        prompt="manuscript",
        dest_dir=tmp_path,
        kind="scene",
        producers={"flux": _flux},
        judge=lambda _path, _ctx: _fail(),
        once=True,
    )
    assert calls["n"] == 1
    assert result.passed is False
    assert result.next_rung == 1
    assert result.replans == 0


def test_fair_wave_gives_every_beat_one_attempt_before_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from functools import partial

    from modules.reel_artefact.climb import climb_act, reset_cursors
    from modules.reel_artefact.fair import run_fair

    monkeypatch.setenv("REEL_FLUX_MAX_PER_REEL", "2")
    monkeypatch.setenv("REEL_REPLAN_MAX_PER_REEL", "0")
    monkeypatch.setenv("REEL_LADDER_LLM", "0")
    reset_cursors()
    calls: list[int] = []

    def _generate(text: str, **kwargs: object) -> Path:
        del text
        stem = str(kwargs.get("output_stem") or "still")
        dest = Path(str(kwargs.get("output_directory"))) / f"{stem}.jpg"
        calls.append(1)
        return _jpeg(dest, (20, 30, len(calls) + 3))

    monkeypatch.setattr(
        "modules.reel_artefact.climb.evaluate_chunk_relevance",
        lambda *args, **kwargs: _fail(),
    )
    lines = (
        "Cicero called it a lost blueprint.",
        "Scholars disagree about the ancient record.",
    )
    jobs = {
        index: partial(
            climb_act,
            beat_index=index,
            narration=line,
            prompt="a manuscript",
            output_directory=tmp_path / f"b{index}",
            stem=f"act{index}",
            channel="ancient_knowledge",
            flux_generate=_generate,
            n_beats=2,
            shot_type="wide",
            camera="wide view",
            setting="a museum archive",
            shot_subject="an old manuscript on a desk",
            shot_era="Hellenistic",
        )
        for index, line in enumerate(lines)
    }

    def _sequential(batch: dict) -> dict:
        return {key: batch[key]() for key in sorted(batch)}

    results = run_fair(jobs, runner=_sequential)
    assert len(calls) == 2
    assert results[0].publishable is False
    assert results[1].publishable is False


def test_event_photo_prefers_the_dive_over_the_statue(
    tmp_path: Path,
) -> None:
    from agents.media.free_images.http import HttpResponse
    from modules.reel_artefact.events import acquire_event_photo
    from modules.shot_planner.entities import Entity

    entity = Entity(
        id="antikythera_mechanism",
        name="Antikythera Mechanism",
        aliases=("antikythera",),
        kind="artifact",
        canonical_visual="fragments",
        era="Hellenistic",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
        event_search=("Antikythera wreck sponge divers 1901",),
        event_categories=("Category:Antikythera wreck",),
        event_period="1900-1901",
    )
    pages = {
        "File:NAMA Antikythera mechanism fragment.jpg": {
            "title": "File:NAMA Antikythera mechanism fragment.jpg",
            "imageinfo": [{
                "url": "https://example.test/statue.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Statue.jpg",
                "width": 1600,
                "height": 1200,
                "mime": "image/jpeg",
                "extmetadata": {
                    "ObjectName": {"value": "Antikythera mechanism fragment Giovanni Dall'Orto"},
                    "LicenseShortName": {"value": "Public domain"},
                    "Artist": {"value": "Giovanni Dall'Orto"},
                },
            }],
        },
        "File:Anticythera shipwreck. Standard diving dress.jpg": {
            "title": "File:Anticythera shipwreck. Standard diving dress.jpg",
            "imageinfo": [{
                "url": "https://example.test/dress.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Dress.jpg",
                "width": 1600,
                "height": 1200,
                "mime": "image/jpeg",
                "extmetadata": {
                    "ObjectName": {"value": "Anticythera shipwreck standard diving dress 1901"},
                    "LicenseShortName": {"value": "Public domain"},
                    "Artist": {"value": "Unknown"},
                },
            }],
        },
    }

    class _Client:
        def get(self, url, params=None, headers=None):
            del headers
            params = params or {}
            if "dress.jpg" in url or "statue.jpg" in url:
                return HttpResponse(200, _jpeg_bytes())
            if params.get("list") == "categorymembers":
                body = {"query": {"categorymembers": [
                    {"title": "File:NAMA Antikythera mechanism fragment.jpg"},
                    {"title": "File:Anticythera shipwreck. Standard diving dress.jpg"},
                ]}}
                return HttpResponse(200, json.dumps(body).encode())
            title = str(params.get("titles") or "")
            chosen = [pages[name] for name in title.split("|") if name in pages]
            if not chosen:
                chosen = [pages["File:Anticythera shipwreck. Standard diving dress.jpg"]]
            body = {
                "query": {
                    "pages": {
                        str(index): page for index, page in enumerate(chosen, start=1)
                    },
                },
            }
            return HttpResponse(200, json.dumps(body).encode())

    made = acquire_event_photo(
        entity,
        tmp_path / "event.jpg",
        channel="ancient_knowledge",
        beat_index=0,
        n_beats=8,
        client=_Client(),
    )
    assert made is not None
    side = json.loads(Path(str(made) + ".source.json").read_text(encoding="utf-8"))
    assert "diving" in side["title"].lower()
    store = Path("channels_config/ancient_knowledge/store/artefact_cache")
    assert not (store / "antikythera_mechanism" / "events" / "event_00.jpg").is_file()


def test_a_checkerboard_is_not_a_photograph() -> None:
    import io

    from modules.reel_artefact.pixels import image_defect

    raw = _checker_bytes()
    with Image.open(io.BytesIO(raw)) as opened:
        assert image_defect(opened.convert("RGB"), nbytes=len(raw)) in {
            "checkerboard",
            "synthetic",
        }


def test_a_replan_that_names_the_object_is_dropped() -> None:
    from modules.reel_artefact.flux_policy import acceptable_flux_concept
    from modules.reel_artefact.replan import next_concept

    assert acceptable_flux_concept(
        "A gold ornament lying on an open book.",
        name="Quimbaya golden ornaments",
    ) is False
    assert acceptable_flux_concept(
        "An old manuscript on a wooden desk. Pages have no readable text.",
        name="Antikythera Mechanism",
    ) is True
    text = next_concept(
        "Cicero called it a lost blueprint.",
        kind="scene",
        event=False,
        name="Antikythera Mechanism",
        llm=lambda *_args: "Clean brass gears on an open book.",
    )
    assert "brass gears on" not in text.lower()
    assert "do not depict" in text.lower()


def _jpeg_bytes() -> bytes:
    """A noisy frame. Event tests must not publish a synthetic pattern."""
    import io
    import random

    rng = random.Random(4)
    image = Image.new("RGB", (320, 240))
    pixels = image.load()
    for y in range(240):
        for x in range(320):
            pixels[x, y] = (
                rng.randrange(20, 220),
                rng.randrange(30, 180),
                rng.randrange(40, 160),
            )
    buf = io.BytesIO()
    image.resize((1600, 1200), Image.Resampling.NEAREST).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _checker_bytes() -> bytes:
    import io

    buf = io.BytesIO()
    color = (70, 90, 110)
    tile = Image.new("RGB", (16, 16), color)
    pixels = tile.load()
    for y in range(16):
        for x in range(16):
            shift = 40 if (x + y) % 2 == 0 else 0
            pixels[x, y] = tuple(min(255, channel + shift) for channel in color)
    tile.resize((1600, 1200), Image.Resampling.NEAREST).save(buf, format="JPEG")
    return buf.getvalue()


# The near-zero cost defaults live in tests/test_near_zero_cost.py. CI runs
# them through this file, because the workflow list cannot be edited here.
from tests.test_near_zero_cost import *  # noqa: E402,F401,F403
