# -*- coding: utf-8 -*-
"""Smoke 13, through the same still entry point main.py uses.

The Antikythera reel published Göbekli Tepe and Puma Punku. Museum, hands,
and manuscript scenes failed the judge. Open beats then ended the reel
even though a landscape had already passed, and pass 1 spent two Flux
images on one beat.
"""
from __future__ import annotations

import json
import random
import threading
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from modules.reel_artefact.budget import still_uses
from modules.reel_artefact.motion import motion_sidecar_path, profile_for
from modules.reel_artefact.orchestrate import make_climb_job, run_reel_stills
from modules.reel_artefact.reuse import reuse_key_for
from modules.shot_planner.build import build_shot_list
from modules.shot_planner.plan import plan_for_reel
from modules.shot_planner.schema import Shot


_ANTIKYTHERA = "The Antikythera Mechanism"
_QUIMBAYA = "The Quimbaya golden ornaments, 600-1000 AD"
_DROPPED = {"museum_hall", "silhouette", "manuscript"}
_PLACES = {
    "landscape", "sea", "aerial", "detail", "golden", "fog", "ruins", "coast",
    "establishing", "atmosphere",
}
_FOREIGN = (
    "Its existence provokes a question that remains unanswered. "
    "What wonders lie hidden beneath ocean depths? "
    "What extraordinary achievements remain unexplored? "
    "Göbekli Tepe's pillars still stand in their enclosure. "
    "The Great Sphinx and the Antikythera Mechanism are often compared."
)


def test_smoke13_topic_entity_locks_planner_and_resolver(tmp_path: Path, monkeypatch) -> None:
    """A foreign entity_id is kept only when the spoken line names that entity."""
    monkeypatch.setenv("REEL_PLAN_CHECK", "off")
    monkeypatch.setenv("REEL_SHOT_PLANNER", "1")
    monkeypatch.setenv("REEL_LADDER_LLM", "0")
    monkeypatch.setenv("REEL_ARTIFACT_IMAGES", "1")
    monkeypatch.setenv("REEL_ARTEFACT_CACHE_DIR", str(tmp_path / "cache"))
    _reset()

    plan = plan_for_reel(
        channel="ancient_knowledge",
        topic=_ANTIKYTHERA,
        script=_FOREIGN,
        complete=lambda prompt, system=None: _foreign_plan(prompt),
    )
    assert plan is not None
    by_line = {shot.narration: shot for shot in plan.shots}
    unnamed = [
        by_line[line]
        for line in (
            "Its existence provokes a question that remains unanswered.",
            "What wonders lie hidden beneath ocean depths?",
            "What extraordinary achievements remain unexplored?",
        )
    ]
    # The model asked for Göbekli Tepe, Puma Punku, and the Great Sphinx.
    for shot in unnamed:
        assert shot.entity_id == "antikythera_mechanism"
    named_only = by_line["Göbekli Tepe's pillars still stand in their enclosure."]
    assert named_only.entity_id == "gobekli_tepe"
    both = by_line["The Great Sphinx and the Antikythera Mechanism are often compared."]
    assert both.entity_id == "antikythera_mechanism"

    seen: list[str] = []

    def _photo(entity, dest, **_kwargs):
        seen.append(entity.id)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _noisy(dest, 7)
        return dest

    import modules.reel_artefact.climb as climb

    monkeypatch.setattr(climb, "acquire_photo", _photo)
    _install_judge(lambda text: True)
    shot = Shot(
        id="s16",
        narration="Corroded fragments sat in a museum drawer for decades.",
        entity_id="gobekli_tepe",
        subject="Göbekli Tepe",
        canonical_visual="T-shaped pillars",
        shot_type="close",
        camera="close",
        setting="buried enclosure",
        era="about 9600 BC",
        mood="quiet",
        lighting="daylight",
        negatives=[],
        image_role="photo",
    )
    run_reel_stills({
        0: make_climb_job(
            beat_index=0,
            narration=shot.narration,
            prompt=shot.subject,
            output_directory=tmp_path / "resolver",
            stem="foreign",
            channel="ancient_knowledge",
            flux_generate=lambda *_args, **_kwargs: None,
            shot=shot,
            entity_id="gobekli_tepe",
            episode_subject=_ANTIKYTHERA,
            n_beats=1,
        ),
    })
    assert seen == ["antikythera_mechanism"]


def test_smoke13_flux_scenes_are_regional_places() -> None:
    """Abstract lines alternate place treatments. Museum, hands, and books are gone."""
    plan = build_shot_list(
        _QUIMBAYA,
        "Scholars disagree about that lost knowledge. "
        "Historians debate the ancient record. "
        "One theory proposes a forgotten craft. "
        "The precision involved remains a profound archaeological enigma. "
        "A legend surrounds that lost blueprint.",
    )
    scenes = [shot.scene_type for shot in plan.shots]
    assert len(scenes) >= 4
    assert not _DROPPED.intersection(scenes)
    assert all(shot.image_role == "flux" for shot in plan.shots)
    assert all(scene in _PLACES for scene in scenes)
    for earlier, later in zip(scenes, scenes[1:]):
        assert earlier != later
    assert len(set(scenes) & {"landscape", "aerial", "detail", "golden", "fog", "ruins"}) >= 2
    for shot in plan.shots:
        blob = f"{shot.subject} {shot.canonical_visual} {shot.setting} {shot.camera}".lower()
        assert "manuscript" not in blob
        assert "hands without" not in blob
        assert "museum archive" not in blob
        assert "museum hall" not in blob


def test_smoke13_first_pass_is_one_flux_and_a_landscape_finishes_the_reel(
    tmp_path: Path, monkeypatch,
) -> None:
    """Pass 1 is one Flux image per beat. An approved place then fills open beats, at most 3 uses."""
    monkeypatch.setenv("REEL_PLAN_CHECK", "off")
    monkeypatch.setenv("REEL_LADDER_LLM", "0")
    monkeypatch.setenv("REEL_ARTIFACT_IMAGES", "1")
    monkeypatch.setenv("REEL_ARTEFACT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("REEL_REPLAN_MAX", "0")
    monkeypatch.setenv("REEL_REPLAN_MAX_PER_REEL", "0")
    monkeypatch.setenv("REEL_REUSE_MAX_PER_STILL", "1")
    monkeypatch.setenv("REEL_FLUX_MAX_PER_REEL", "20")
    _reset()

    plan = build_shot_list(
        _QUIMBAYA,
        "The APPROVED VALLEY is the real place of the highlands. "
        "Scholars disagree about that lost knowledge. "
        "Historians debate the ancient record. "
        "One theory proposes a forgotten craft.",
    )
    assert len(plan.shots) == 4
    assert all(shot.image_role == "flux" for shot in plan.shots)
    flux_log: list[int] = []
    prompts: list[str] = []
    lock = threading.Lock()

    def _generate(prompt, output_stem=None, output_directory=None, **_kwargs):
        dest = Path(output_directory) / f"{output_stem}.jpg"
        beat = _beat_from_stem(str(output_stem or ""))
        _noisy(dest, 30 + beat * 17 + len(flux_log))
        with lock:
            flux_log.append(beat)
            prompts.append(str(prompt))
        return dest

    _install_judge(lambda text: "APPROVED VALLEY" in text)
    results = run_reel_stills({
        index: make_climb_job(
            beat_index=index,
            narration=shot.narration,
            prompt=shot.subject,
            output_directory=tmp_path / "reel",
            stem=f"q{index:02d}",
            channel="ancient_knowledge",
            flux_generate=_generate,
            shot=shot,
            episode_subject=_QUIMBAYA,
            n_beats=len(plan.shots),
        )
        for index, shot in enumerate(plan.shots)
    })

    # Pass 1 generates one Flux image for every beat before any beat is repeated.
    first_wave = flux_log[: len(plan.shots)]
    assert sorted(first_wave) == [0, 1, 2, 3]
    assert len(first_wave) == len(set(first_wave))
    for prompt in prompts:
        low = prompt.lower()
        assert "manuscript" not in low
        assert "hands without" not in low
        assert "museum archive" not in low
        assert "museum hall" not in low

    # The approved valley is copied onto the next two open beats, then stops at 3 uses.
    assert results[0].publishable
    assert str((results[0].verdict or {}).get("rung") or "") == "flux"
    origin = results[0].path
    assert origin is not None and origin.is_file()
    origin_move = profile_for(0)
    for index in (1, 2):
        saved = results[index]
        assert saved.publishable
        assert (saved.verdict or {}).get("rung") == "safety"
        assert saved.attempts[-1]["provider"] == "safety"
        assert saved.path is not None and saved.path.is_file()
        assert Image.open(saved.path).size == (1080, 1920)
        side = json.loads(motion_sidecar_path(saved.path).read_text(encoding="utf-8"))
        assert side["profile"] != origin_move
        assert side["profile"] in {"slow_push", "slow_pull", "pan_left", "pan_right"}
    assert not results[3].publishable
    assert still_uses(reuse_key_for(origin)) == 3


def _foreign_plan(prompt: str) -> str:
    del prompt
    from modules.shot_planner.build import split_sentences

    sentences = split_sentences(_FOREIGN)
    foreign_ids = (
        "gobekli_tepe",
        "puma_punku",
        "great_sphinx",
        "gobekli_tepe",
        "great_sphinx",
    )
    shots = []
    for index, (sentence, entity_id) in enumerate(zip(sentences, foreign_ids), start=1):
        shots.append({
            "id": f"s{index:02d}",
            "narration": sentence,
            "entity_id": entity_id,
            "subject": entity_id.replace("_", " "),
            "canonical_visual": "a foreign site the model invented",
            "shot_type": "wide",
            "camera": "wide documentary view",
            "setting": "somewhere else",
            "era": "unspecified",
            "mood": "quiet",
            "lighting": "daylight",
            "negatives": ["text"],
        })
    return json.dumps({
        "channel": "ancient_knowledge",
        "topic": _ANTIKYTHERA,
        "narration": _FOREIGN,
        "shots": shots,
    })


def _install_judge(passes) -> None:
    class _Provider:
        provider = "gemini"
        model_id = "models/gemini-2.5-flash"

        def evaluate(self, prompt, paths, labels=None):
            del paths, labels
            ok = bool(passes(str(prompt)))
            payload = {
                "image_description": "a documentary still",
                "shows_expected_subject": ok,
                "anachronism": False,
                "non_photoreal": False,
                "anatomy_defect": False,
                "setting_contradicts_beat": False,
                "contains_forbidden_object": False,
                "shape_mismatch": False,
                "is_relevant": ok,
                "relevance_score": 0.93 if ok else 0.2,
                "detected_subjects": ["place"],
                "rejection_reason": None if ok else "below",
            }
            return SimpleNamespace(
                text=json.dumps(payload),
                provider="gemini",
                model_id="models/gemini-2.5-flash",
            )

    import modules.reel_qa.relevance as relevance

    relevance._provider = lambda name: _Provider()  # type: ignore[assignment]


def _reset() -> None:
    from modules.reel_artefact.budget import reset_nano_budget, reset_spend, reset_uses
    from modules.reel_artefact.diversity import reset_diversity
    from modules.reel_artefact.reuse import reset_approved

    reset_spend()
    reset_nano_budget()
    reset_uses()
    reset_diversity()
    reset_approved()


def _beat_from_stem(stem: str) -> int:
    for part in stem.split("_"):
        if part.isdigit() and len(part) <= 2:
            return int(part)
    return 0


def _noisy(path: Path, seed: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    image = Image.new("RGB", (96, 128))
    pixels = []
    for _y in range(128):
        for _x in range(96):
            base = 40 + (seed % 50)
            pixels.append((
                base + rng.randrange(60),
                base + rng.randrange(40),
                base + rng.randrange(30),
            ))
    image.putdata(pixels)
    image.save(path, format="JPEG", quality=85)
