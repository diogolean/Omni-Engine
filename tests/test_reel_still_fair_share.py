# -*- coding: utf-8 -*-
"""Still entry point main.py uses, including fair share and planned photos.

Unit tests climbed one beat on the calling thread, so they never saw the
worker pool drop the fair-share flag, or a planned photograph routed to Flux.
"""
from __future__ import annotations

import io
import json
import logging
import random
import re
import threading
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from production.formats.economic_reel.artefact.orchestrate import make_climb_job, run_reel_stills
from production.formats.economic_reel.artefact.role import is_event
from production.formats.economic_reel.shot_planner.build import build_shot_list
from production.formats.economic_reel.shot_planner.plan import plan_for_reel


_QUIMBAYA = "The Quimbaya golden ornaments, 600-1000 AD"
_ANTIKYTHERA = "The Antikythera Mechanism"


def test_reel_stills_through_the_main_entry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REEL_PLAN_CHECK", "off")
    monkeypatch.setenv("REEL_LADDER_LLM", "0")
    monkeypatch.setenv("REEL_ARTIFACT_IMAGES", "1")
    monkeypatch.setenv("REEL_ARTEFACT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("REEL_NANOBANANA_MAX_PER_REEL", "4")
    _reset()

    # 6. No sea for an Andean culture, including the line smoke 12 sent to sea,
    # and three landscapes in a row, which used to step onto "calm sea".
    quimbaya_plan = build_shot_list(
        _QUIMBAYA,
        "The highlands of Colombia are the real place. "
        "The region is the real place. "
        "The landscape of the real place is the valley. "
        "The precision involved remains a profound archaeological enigma. "
        + " ".join(
            f"Gold pendant number {index} rests in a museum case."
            for index in range(8)
        ),
    )
    scenes = [shot.scene_type for shot in quimbaya_plan.shots]
    assert "sea" not in scenes
    enigma = next(
        shot for shot in quimbaya_plan.shots
        if "enigma" in shot.narration.lower()
    )
    assert enigma.scene_type != "sea"
    assert enigma.image_role == "flux"
    places = [
        shot for shot in quimbaya_plan.shots
        if shot.scene_type in {"landscape", "establishing", "atmosphere"}
    ]
    assert places
    for shot in places:
        blob = f"{shot.subject} {shot.canonical_visual} {shot.setting}".lower()
        assert "cauca" in blob
        assert "cloud forest" in blob or "quindío" in blob or "quindio" in blob
        assert "mesa" not in blob

    # 4. Object beats stay within the supply: each real photo twice, and
    # four Nano Banana edits each reused once. Quimbaya has no usable
    # photograph on file, so the cap is 8.
    photos = [shot for shot in quimbaya_plan.shots if shot.image_role == "photo"]
    assert len(photos) <= 8

    # The model itself asked for a calm sea. The planner entry rewrites it.
    monkeypatch.setenv("REEL_SHOT_PLANNER", "1")
    modelled = plan_for_reel(
        channel="ancient_knowledge",
        topic=_QUIMBAYA,
        script="The precision involved remains a profound archaeological enigma.",
        complete=_sea_model,
    )
    assert modelled is not None
    assert modelled.shots[0].scene_type != "sea"
    assert "cauca" in modelled.shots[0].canonical_visual.lower()
    monkeypatch.delenv("REEL_SHOT_PLANNER", raising=False)

    anti_plan = build_shot_list(
        _ANTIKYTHERA,
        "The Antikythera Mechanism came out of a shipwreck. "
        "Sponge divers recovered it in 1901. "
        + " ".join(
            f"Corroded fragment number {index} sits in a museum drawer."
            for index in range(12)
        ),
    )
    wreck = anti_plan.shots[0]
    assert wreck.scene_type == "sea"
    assert "aegean" in wreck.canonical_visual.lower() or "kythira" in wreck.canonical_visual.lower()
    assert len([shot for shot in anti_plan.shots if shot.image_role == "photo"]) <= (6 + 4) * 2

    # The run uses the same job builder and the same pool main.py calls.
    logs: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            logs.append(record.getMessage())

    handler = _Handler()
    climb_log = logging.getLogger("production.formats.economic_reel.artefact.climb")
    climb_log.addHandler(handler)
    climb_log.setLevel(logging.INFO)
    import production.qa.relevance as relevance

    original_provider = relevance._provider
    try:
        _run_quimbaya(tmp_path / "quimbaya", monkeypatch, logs)
        _reset()
        _run_antikythera(tmp_path / "antikythera", monkeypatch, logs)
    finally:
        climb_log.removeHandler(handler)
        relevance._provider = original_provider

    # 7. Scene, role, and judge mode are on the run log.
    beat_logs = [line for line in logs if line.startswith("LADDER beat |")]
    assert any("judge=shot" in line and "role=flux" in line for line in beat_logs)
    assert any("judge=event" in line for line in beat_logs)
    assert any("judge=object" in line and "role=photo" in line for line in beat_logs)
    assert any("scene=landscape" in line or "scene=establishing" in line for line in beat_logs)


def _run_quimbaya(folder: Path, monkeypatch, logs: list[str]) -> None:
    """Every flux beat gets one attempt before any beat is retried."""
    del logs
    plan = build_shot_list(
        _QUIMBAYA,
        "The highlands of Colombia are the real place. "
        "The region is the real place. "
        "Scholars disagree about that lost knowledge. "
        "Historians debate the ancient record. "
        "The precision involved remains a profound archaeological enigma. "
        "One theory proposes a forgotten craft. "
        "The landscape of the valley is the real place.",
    )
    flux_beats = [
        index for index, shot in enumerate(plan.shots)
        if shot.image_role == "flux"
        and not is_event(shot.narration, camera=shot.camera, setting=shot.setting)
    ]
    assert len(flux_beats) >= 5
    monkey_cap = len(flux_beats) + 2
    _set_flux_cap(monkeypatch, monkey_cap)
    flux_log: list[tuple[int, str]] = []
    lock = threading.Lock()
    shot_calls = {"n": 0}

    def _generate(prompt, output_stem=None, output_directory=None, **_kwargs):
        dest = Path(output_directory) / f"{output_stem}.jpg"
        beat = _beat_from_stem(str(output_stem or ""))
        _noisy(dest, 20 + beat)
        with lock:
            flux_log.append((beat, str(prompt)))
        return dest

    _install_judge(shot_calls, len(flux_beats))
    jobs = {
        index: make_climb_job(
            beat_index=index,
            narration=shot.narration,
            prompt=shot.subject,
            output_directory=folder,
            stem=f"q{index:02d}",
            channel="ancient_knowledge",
            flux_generate=_generate,
            shot=shot,
            episode_subject=_QUIMBAYA,
            n_beats=len(plan.shots),
        )
        for index, shot in enumerate(plan.shots)
    }
    run_reel_stills(jobs)

    # 1. Pass 1 is one attempt per beat. A repeat starts only after that.
    order = [beat for beat, _prompt in flux_log]
    seen: set[int] = set()
    for beat in order:
        if beat in seen:
            break
        seen.add(beat)
    assert set(flux_beats) <= seen
    counts: dict[int, int] = {}
    for beat, _prompt in flux_log:
        counts[beat] = counts.get(beat, 0) + 1
    assert all(counts.get(beat, 0) >= 1 for beat in flux_beats)
    assert max(counts.values()) <= 2

    # 3. The Flux prompt names the Colombian geography, not a generic desert.
    place_prompts = [
        prompt for _beat, prompt in flux_log
        if "cauca" in prompt.lower() or "andes" in prompt.lower()
    ]
    assert place_prompts
    for prompt in place_prompts:
        assert "cloud forest" in prompt.lower() or "quindío" in prompt.lower() or "quindio" in prompt.lower()
        # "mesas" in the negative prompt is the ban, not a mesa scene.
        assert re.search(r"\bmesa\b", prompt.lower()) is None
        assert "egypt" not in prompt.lower()


def _run_antikythera(folder: Path, monkeypatch, logs: list[str]) -> None:
    del logs
    plan = build_shot_list(
        _ANTIKYTHERA,
        "The Antikythera Mechanism came out of a shipwreck. "
        "Sponge divers recovered it in 1901. "
        "Corroded fragments sat in a museum drawer for decades. "
        "The gears are cut with a precision that still surprises researchers. "
        "The real place is the Greek coast. "
        "Scholars disagree about the ancient record.",
    )
    photo_beats = [index for index, shot in enumerate(plan.shots) if shot.image_role == "photo"]
    assert photo_beats
    flux_beats = [
        index for index, shot in enumerate(plan.shots)
        if shot.image_role == "flux"
        and not is_event(shot.narration, camera=shot.camera, setting=shot.setting)
    ]
    _set_flux_cap(monkeypatch, max(4, len(flux_beats) + 2))
    photo_log: list[tuple[int, bool]] = []
    lock = threading.Lock()
    prompts: list[str] = []
    shot_calls = {"n": 0}

    def _generate(prompt, output_stem=None, output_directory=None, **_kwargs):
        dest = Path(output_directory) / f"{output_stem}.jpg"
        _noisy(dest, 80 + _beat_from_stem(str(output_stem or "")))
        return dest

    def _photo(entity, dest, allow_nano=True, beat_index=0, text_only=False, **_kwargs):
        del entity
        with lock:
            photo_log.append((int(beat_index), bool(allow_nano)))
        if not allow_nano or text_only:
            return None
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _noisy(dest, 90 + int(beat_index))
        ref = dest.with_name(dest.stem + "_ref.jpg")
        _noisy(ref, 3)
        from production.formats.economic_reel.artefact.source import write_source_sidecar

        write_source_sidecar(dest, {
            "provider": "nanobanana",
            "still_kind": "object",
            "canonical_shape": "corroded bronze gear fragments of a mechanical device",
            "reference_image": str(ref),
            "author": "Museum",
        })
        return dest

    monkeypatch.setattr("modules.reel_artefact.climb.acquire_photo", _photo)
    monkeypatch.setattr(
        "modules.reel_artefact.events.RequestsClient",
        lambda *args, **kwargs: _EventClient(),
    )
    _install_judge(shot_calls, max(1, len(flux_beats)), prompts)
    jobs = {
        index: make_climb_job(
            beat_index=index,
            narration=shot.narration,
            prompt=shot.subject,
            output_directory=folder,
            stem=f"a{index:02d}",
            channel="ancient_knowledge",
            flux_generate=_generate,
            shot=shot,
            episode_subject=_ANTIKYTHERA,
            n_beats=len(plan.shots),
        )
        for index, shot in enumerate(plan.shots)
    }
    results = run_reel_stills(jobs)

    # 4. A planned photograph reaches Nano Banana. It is not painted as a scene.
    assert any(allow_nano for _beat, allow_nano in photo_log)
    for index in photo_beats:
        verdict = (results[index].verdict or {}) if results.get(index) else {}
        assert verdict.get("judge_mode") == "object"
        assert results[index].publishable is True

    # 2. Flux scenes are judged against the planned shot. Event photos are
    # not judged on artefact shape, even when the model says shape_mismatch.
    event_prompts = [text for text in prompts if "ARCHIVAL PHOTOGRAPH" in text]
    shot_prompts = [text for text in prompts if "PLANNED SHOT" in text]
    object_prompts = [text for text in prompts if "shape_mismatch" in text]
    assert event_prompts
    assert shot_prompts
    assert object_prompts
    for text in event_prompts:
        assert "shape_mismatch" not in text
        assert "artefact shape" in text.lower() or "do not score artefact shape" in text.lower()
    for text in shot_prompts:
        assert "context only" in text.lower()
        assert "contradicts the spoken beat" not in text.lower()
        assert "the object this still must show" not in text.lower()
    event_indexes = [
        index for index, shot in enumerate(plan.shots)
        if is_event(shot.narration, camera=shot.camera, setting=shot.setting)
    ]
    assert event_indexes
    published_events = [
        index for index in event_indexes
        if results[index].publishable
        and (results[index].verdict or {}).get("judge_mode") == "event"
    ]
    assert published_events

    # 3. A generated real-place prompt names the Aegean coast.
    # 5. The event frame keeps the boat, and the author is not doubled.
    from production.formats.economic_reel.artefact.source import source_meta

    event = results[published_events[0]]
    assert event.path is not None
    side = source_meta(event.path)
    assert side.get("author") == "Unknown author"
    credit = str(side.get("credit_line") or "")
    assert "Unknown author Unknown author" not in credit
    assert credit.count("Unknown author") == 1
    with Image.open(event.path) as opened:
        dark = min(opened.convert("L").getdata())
        assert dark < 50


def _sea_model(prompt: str, system: str | None = None) -> str:
    del prompt, system
    sentence = "The precision involved remains a profound archaeological enigma."
    return json.dumps({
        "channel": "ancient_knowledge",
        "topic": _QUIMBAYA,
        "narration": sentence,
        "shots": [{
            "id": "s01",
            "narration": sentence,
            "entity_id": "quimbaya_ornaments",
            "subject": "calm sea",
            "canonical_visual": "Calm sea or underwater ambience, light in the water, uncluttered.",
            "shot_type": "wide",
            "camera": "wide view of the sea, no faces",
            "setting": "open sea",
            "era": "pre-Columbian Colombia",
            "mood": "enigmatic",
            "lighting": "natural daylight, documentary",
            "negatives": [],
        }],
    })


class _EventClient:
    def get(self, url, params=None, headers=None):
        from production.providers.image.free.http import HttpResponse

        del headers
        params = params or {}
        if str(url).endswith(".jpg") or "dress" in str(url):
            return HttpResponse(200, _boat_jpeg())
        if params.get("list") == "categorymembers":
            body = {"query": {"categorymembers": [
                {"title": "File:Anticythera shipwreck. Standard diving dress.jpg"},
            ]}}
            return HttpResponse(200, json.dumps(body).encode())
        page = {
            "title": "File:Anticythera shipwreck. Standard diving dress.jpg",
            "imageinfo": [{
                "url": "https://example.test/dress.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Dress.jpg",
                "width": 1600,
                "height": 900,
                "mime": "image/jpeg",
                "extmetadata": {
                    "ObjectName": {"value": "Anticythera shipwreck standard diving dress 1901"},
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                    "Artist": {"value": "Unknown author Unknown author"},
                },
            }],
        }
        body = {"query": {"pages": {"1": page}}}
        return HttpResponse(200, json.dumps(body).encode())


def _install_judge(shot_calls: dict[str, int], flux_count: int, prompts: list[str] | None = None) -> None:
    lock = threading.Lock()

    class _Provider:
        provider = "gemini"
        model_id = "models/gemini-2.5-flash"

        def evaluate(self, prompt, paths, labels=None):
            del paths, labels
            text = str(prompt)
            if prompts is not None:
                with lock:
                    prompts.append(text)
            fail = False
            if "PLANNED SHOT" in text:
                with lock:
                    shot_calls["n"] += 1
                    fail = shot_calls["n"] <= flux_count
            if "ARCHIVAL PHOTOGRAPH" in text:
                payload = {
                    "image_description": "a period boat with divers in standard diving dress",
                    "shows_expected_subject": True,
                    "anachronism": False,
                    "non_photoreal": False,
                    "anatomy_defect": False,
                    "setting_contradicts_beat": False,
                    "shape_mismatch": True,
                    "is_relevant": True,
                    "relevance_score": 0.92,
                    "detected_subjects": ["boat", "divers"],
                    "rejection_reason": None,
                }
            elif fail:
                payload = {
                    "image_description": "a wide landscape",
                    "shows_expected_subject": False,
                    "anachronism": False,
                    "non_photoreal": False,
                    "anatomy_defect": False,
                    "setting_contradicts_beat": False,
                    "contains_forbidden_object": False,
                    "is_relevant": False,
                    "relevance_score": 0.2,
                    "detected_subjects": ["landscape"],
                    "rejection_reason": "below",
                }
            else:
                payload = {
                    "image_description": "a photoreal documentary place",
                    "shows_expected_subject": True,
                    "anachronism": False,
                    "non_photoreal": False,
                    "anatomy_defect": False,
                    "setting_contradicts_beat": False,
                    "contains_forbidden_object": False,
                    "shape_mismatch": False,
                    "is_relevant": True,
                    "relevance_score": 0.93,
                    "detected_subjects": ["place"],
                    "rejection_reason": None,
                }
            return SimpleNamespace(
                text=json.dumps(payload),
                provider="gemini",
                model_id="models/gemini-2.5-flash",
            )

    import production.qa.relevance as relevance

    relevance._provider = lambda name: _Provider()  # type: ignore[assignment]


def _set_flux_cap(monkeypatch, limit: int) -> None:
    # monkeypatch restores the cap, so later tests see the default again.
    monkeypatch.setenv("REEL_FLUX_MAX_PER_REEL", str(limit))


def _reset() -> None:
    from production.formats.economic_reel.artefact.budget import reset_nano_budget, reset_spend, reset_uses
    from production.formats.economic_reel.artefact.diversity import reset_diversity
    from production.formats.economic_reel.artefact.reuse import reset_approved

    reset_spend()
    reset_nano_budget()
    reset_uses()
    reset_diversity()
    reset_approved()


def _beat_from_stem(stem: str) -> int:
    parts = stem.split("_")
    for part in parts:
        if part.isdigit():
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


def _boat_jpeg() -> bytes:
    """Wide sea, with the boat in the lower right. A centre crop misses it.

    Tones change in blocks larger than the defect sampler, so the frame is
    a photograph and not a flat field that averages down to one colour.
    """
    rng = random.Random(11)
    image = Image.new("RGB", (1600, 900))
    pixels = []
    for y in range(900):
        band = (y // 48) * 17
        for x in range(1600):
            cloud = ((x // 70) * 11 + (y // 90) * 7) % 40
            if 1280 <= x <= 1560 and 520 <= y <= 860:
                level = 8 + ((x // 40 + y // 36) % 5) * 4
                pixels.append((level, level + 2, level + 6))
            elif y < 260:
                level = 150 + cloud + (x % 5)
                pixels.append((level, level + 8, 210))
            else:
                level = 90 + band + (x // 80) % 25 + rng.randrange(6)
                pixels.append((level, level + 12, level + 28))
    image.putdata(pixels)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
