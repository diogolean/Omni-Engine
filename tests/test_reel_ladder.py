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
    assert rungs_for("scene") == ["flux", "flux", "reuse"]


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
    first = reuse_into(tmp_path / "copy1.jpg", 1, source=origin, kind="object", n_beats=20)
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
    for index in range(4):
        copied = reuse_into(
            tmp_path / f"copy{index}.jpg",
            index,
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
    assert result.rung == "flux"
    assert called == ["flux", "flux"]
    assert history_text(result.history) == "flux:fail,flux:pass"


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
    assert "museum hall" in seen[-1].lower() or "display case" in seen[-1].lower()
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

    assert rungs_for("scene", event=True) == ["event", "flux", "flux", "reuse"]
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
    assert "museum hall" in context or "display case" in context
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


def _jpeg_bytes() -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (1600, 1200), (70, 90, 110)).save(buf, format="JPEG")
    return buf.getvalue()
