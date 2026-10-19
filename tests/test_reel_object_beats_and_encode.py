# -*- coding: utf-8 -*-
"""Object lines, one Flux in pass 1, the final frame, and the encode."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

from modules.reel_artefact.flux_policy import object_line_strength
from modules.reel_artefact.ladder import RungContext, rungs_for, run_ladder
from modules.reel_artefact.script_date import ensure_topic_date, states_date
from modules.shot_planner.build import build_shot_list

_QUIMBAYA = "The Quimbaya golden ornaments, 600-1000 AD"
_ANTIKYTHERA = "The Antikythera mechanism, 2nd century BC"
_QUIMBAYA_SCRIPT = (
    "Deep within Bogota's revered Gold Museum. "
    "Lie relics from the ancient Quimbaya civilization. "
    "Among them, golden objects shaped uncannily like modern aircraft. "
    "Crafted between 600 and 1000 AD in ancient Colombia. "
    "Historians debate if these represent stylized birds or insects. "
    "But what if that conventional understanding is incomplete? "
    "Some researchers believe their precise geometry is too advanced. "
    "Featuring stable wings, tailfins, and vertical stabilizers. "
    "These shapes possess aerodynamic qualities far ahead of their era. "
    "One theory proposes ancient artisans held forgotten aeronautical knowledge. "
    "According to legend, sky-dwellers influenced early craft designs. "
    "Some engineers have replicated their forms for flight tests. "
    "Ancient records suggest a stability defying simple explanation. "
    "The precision involved remains a profound archaeological enigma. "
    "Historians debate whether this indicates a lost technology. "
    "Could ancient cultures have achieved impossible feats? "
    "What hidden truths do these golden mysteries still hold?"
)
_ANTIKYTHERA_SCRIPT = (
    "Off Antikythera, Greece, a 1901 discovery challenged history. "
    "From a sunken wreck, bronze fragments emerged, defying its era. "
    "This mechanism, 2nd century BC, showed impossible precision. "
    "Its gears predicted celestial movements with stunning accuracy. "
    "Ancient records suggest such complexity was centuries ahead. "
    "Some believe its design rivals technology a millennium later. "
    "Historians debate if it was unique or lost technological tradition. "
    "One theory proposes this represents a suppressed narrative. "
    "Its ingenuity redefines mechanical arts. "
    "Its purpose baffled experts, pushing archaeological limits. "
    "It forced re-evaluation of Greek scientific capabilities. "
    "This find suggested engineering depth previously impossible. "
    "Imagine computing power existing two millennia ago. "
    "The mechanism stands testament to forgotten brilliance. "
    "Its existence provokes questions about lost ancient knowledge. "
    "How crafted without modern theoretical frameworks? "
    "Scholars suggest it points to an unknown advanced legacy. "
    "What wonders lie hidden beneath ocean depths? "
    "What extraordinary achievements remain unexplored?"
)


def _by_narration(plan, snippet: str):
    found = [shot for shot in plan.shots if snippet.lower() in shot.narration.lower()]
    assert found, snippet
    return found[0]


def test_feature_lines_are_object_beats() -> None:
    """Lines that name the object are photographs. Atmosphere stays a place.

    The share lands near 30–40%. A wreck discovery with no feature named
    stays the event, so the 1901 photograph still has a beat.
    """
    quimbaya = build_shot_list(_QUIMBAYA, _QUIMBAYA_SCRIPT)
    wings = _by_narration(quimbaya, "stable wings, tailfins")
    golden = _by_narration(quimbaya, "golden objects")
    debate = _by_narration(quimbaya, "stylized birds")
    mysteries = _by_narration(quimbaya, "golden mysteries")
    assert object_line_strength(wings.narration) == 2
    assert object_line_strength("delta wings, stabilizers, even rudders") == 2
    assert object_line_strength("tracked celestial bodies and predicted eclipses") == 2
    assert wings.image_role == "photo"
    assert golden.image_role == "photo"
    assert debate.image_role == "flux"
    assert mysteries.image_role == "flux"
    assert debate.scene_type not in {"museum_hall", "silhouette", "manuscript"}
    photos = [shot for shot in quimbaya.shots if shot.image_role == "photo"]
    share = len(photos) / len(quimbaya.shots)
    assert 0.30 <= share <= 0.45
    assert len(photos) <= 8
    for shot in quimbaya.shots:
        if shot.image_role != "flux":
            continue
        blob = f"{shot.canonical_visual} {shot.setting}".lower()
        assert "cauca" in blob or "andes" in blob

    anti = build_shot_list(_ANTIKYTHERA, _ANTIKYTHERA_SCRIPT)
    gears = _by_narration(anti, "predicted celestial")
    mechanism = _by_narration(anti, "2nd century BC")
    discovery = _by_narration(anti, "1901 discovery")
    wonders = _by_narration(anti, "ocean depths")
    assert gears.image_role == "photo"
    assert mechanism.image_role == "photo"
    assert discovery.image_role == "flux"
    assert discovery.scene_type == "sea"
    assert wonders.image_role == "flux"
    anti_photos = [shot for shot in anti.shots if shot.image_role == "photo"]
    anti_share = len(anti_photos) / len(anti.shots)
    assert 0.30 <= anti_share <= 0.45
    assert len(anti_photos) <= (6 + 4) * 2


def test_a_moved_approved_frame_skips_the_spoken_line(tmp_path: Path) -> None:
    """Ken Burns of the approved still passes. A different picture is judged."""
    from modules.reel_qa.mp4_gate import SubtitleCue, gate_final_mp4

    still = _picture(tmp_path / "still.jpg")
    moved = _moved(still, tmp_path / "frame.jpg")

    def _frame(video: Path, at_s: float, dest: Path) -> Path:
        del video, at_s
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(moved.read_bytes())
        return dest

    def _judge(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError("the approved still was judged against the spoken line")

    decision = gate_final_mp4(
        tmp_path / "reel.mp4",
        [SubtitleCue(text="hidden truths", start_s=40.0, end_s=44.0)],
        probe_fn=lambda _path: {
            "error": "",
            "duration_s": 70.0,
            "has_audio": True,
            "mean_volume_db": -16.0,
        },
        evaluate_fn=_judge,
        frame_fn=_frame,
        beats=[{
            "index": 12,
            "text": "What hidden truths do these golden mysteries still hold?",
            "passed": True,
            "image_path": str(still),
            "score": 0.91,
            "prompt": "a wide landscape",
        }],
    )
    assert decision["passed"] is True
    assert decision["frames"][0]["reason"] == "beat-approved"
    assert "frame_subtitle_mismatch" not in decision["reasons"]

    other = _picture(tmp_path / "other.jpg", seed=99)

    def _other(video: Path, at_s: float, dest: Path) -> Path:
        del video, at_s
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(other.read_bytes())
        return dest

    mismatch = gate_final_mp4(
        tmp_path / "reel.mp4",
        [SubtitleCue(text="does not show a shipwreck", start_s=4.0, end_s=8.0)],
        probe_fn=lambda _path: {
            "error": "",
            "duration_s": 70.0,
            "has_audio": True,
            "mean_volume_db": -16.0,
        },
        evaluate_fn=lambda *_args, **_kwargs: {
            "is_relevant": False,
            "relevance_score": 0.2,
            "qa_status": "scored",
            "rejection_reason": "does not show a shipwreck",
        },
        frame_fn=_other,
        beats=[{
            "index": 0,
            "text": "Off Antikythera, a 1901 discovery challenged history.",
            "passed": True,
            "image_path": str(still),
            "score": 0.95,
            "prompt": "the 1901 diving dress",
        }],
    )
    assert mismatch["passed"] is False
    assert "frame_subtitle_mismatch" in mismatch["reasons"]


def test_pass_one_retry_does_not_call_flux_again(tmp_path: Path) -> None:
    """A failed Flux beat, retried inside the first pass, does not generate again."""
    from modules.reel_artefact.climb import set_share

    assert rungs_for("scene") == ["flux", "reuse"]
    calls: list[int] = []

    def _flux(ctx: RungContext) -> Path:
        calls.append(int(ctx.attempt))
        return _flat(ctx.dest, (20, 40, len(calls) + 5))

    def _reuse(ctx: RungContext) -> Path:
        return _flat(ctx.dest, (200, 160, 40))

    def _judge(path: Path, ctx: RungContext) -> dict:
        del ctx
        if "flux" in path.name:
            return {
                "is_relevant": False,
                "relevance_score": 0.2,
                "qa_status": "scored",
                "rejection_reason": "setting_contradicts_beat",
            }
        return {
            "is_relevant": True,
            "relevance_score": 0.9,
            "qa_status": "scored",
            "rejection_reason": "",
        }

    set_share("first")
    first = run_ladder(
        beat_index=5,
        narration="Fog over the valley.",
        prompt="landscape",
        dest_dir=tmp_path,
        kind="scene",
        producers={"flux": _flux, "reuse": _reuse},
        judge=_judge,
        once=True,
    )
    assert calls == [0]
    assert first.passed is False
    set_share("retry")
    retry = run_ladder(
        beat_index=5,
        narration="Fog over the valley.",
        prompt="landscape",
        dest_dir=tmp_path,
        kind="scene",
        producers={"flux": _flux, "reuse": _reuse},
        judge=_judge,
        once=True,
        start_rung=first.next_rung,
    )
    assert calls == [0]
    assert retry.passed is True
    assert retry.rung == "reuse"


def test_encode_stays_under_60mb_at_reel_size() -> None:
    """1080×1920, CRF 22, veryfast or faster, and an 80s reel under 60 MB."""
    from core.reel_sequence_engine import (
        _REEL_HEIGHT,
        _REEL_WIDTH,
        ken_burns_window,
        sequence_encode_params,
    )

    slow = sequence_encode_params(80.0, "medium")
    assert slow["preset"] == "veryfast"
    assert slow["crf"] == "22"
    assert slow["width"] == "1080"
    assert slow["height"] == "1920"
    kbps = int(slow["maxrate"][:-1])
    assert 2500 <= kbps <= 8000
    assert kbps * 80 / 8 / 1024 <= 60

    faster = sequence_encode_params(60.0, "ultrafast")
    assert faster["preset"] == "ultrafast"
    assert int(faster["crf"]) in range(20, 24)

    base = np.zeros((_REEL_HEIGHT, _REEL_WIDTH, 3), dtype=np.uint8)
    base[100:400, 80:300] = (180, 90, 40)
    close = ken_burns_window(base, scale=1.12, max_scale=1.12, pan_x=12, pan_y=-8)
    wide = ken_burns_window(base, scale=1.0, max_scale=1.12, pan_x=0, pan_y=0)
    assert close.shape == (_REEL_HEIGHT, _REEL_WIDTH, 3)
    assert wide.shape == close.shape


def test_the_topic_date_is_in_the_opening() -> None:
    """A date buried later in the script moves into the first two sentences."""
    from core.reel_sequence_engine import build_sequence_script_prompt

    quimbaya = ensure_topic_date(_QUIMBAYA_SCRIPT, _QUIMBAYA)
    anti = ensure_topic_date(_ANTIKYTHERA_SCRIPT, _ANTIKYTHERA)
    assert states_date(" ".join(_opening(quimbaya)), "600-1000 AD")
    assert states_date(" ".join(_opening(anti)), "2nd century BC")
    already = ensure_topic_date(
        "In the 2nd century BC, divers raised a mechanism. The gears survived.",
        _ANTIKYTHERA,
    )
    assert already.startswith("In the 2nd century BC")
    prompt = build_sequence_script_prompt(
        topic=_ANTIKYTHERA,
        niche="ancient mysteries",
        persona_voice="investigative",
        n_acts=8,
        duration_s=70,
    )
    assert "2nd century BC" in prompt
    assert "first two sentences" in prompt.lower()


def _opening(narration: str) -> list[str]:
    parts = [part.strip() for part in narration.replace("?", ".").replace("!", ".").split(".") if part.strip()]
    return parts[:2]


def _picture(path: Path, *, seed: int = 3) -> Path:
    """Four blocks and a mark, so a crop is still this picture."""
    image = Image.new("RGB", (120, 200), (20, 20, 20))
    pixels = image.load()
    colors = [(180, 40, 40), (40, 80, 180), (40, 140, 70), (200, 170, 40)]
    if seed != 3:
        colors = [(20, 20, 20), (230, 230, 230), (80, 20, 120), (20, 160, 160)]
    for y in range(200):
        for x in range(120):
            index = (0 if y < 100 else 2) + (0 if x < 60 else 1)
            pixels[x, y] = colors[index]
    for y in range(30, 80):
        for x in range(15, 45):
            pixels[x, y] = (250, 250, 250) if seed == 3 else (0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="JPEG", quality=90)
    return path


def _moved(source: Path, dest: Path) -> Path:
    """A tighter crop, darkened the way the reel grades a still."""
    image = Image.open(source).convert("RGB")
    width, height = image.size
    left = int(width * 0.12)
    top = int(height * 0.08)
    crop = image.crop((left, top, width - left, height - int(height * 0.05)))
    crop = crop.resize((width, height), Image.Resampling.BILINEAR)
    crop = ImageEnhance.Brightness(crop).enhance(0.70)
    dest.parent.mkdir(parents=True, exist_ok=True)
    crop.save(dest, format="JPEG", quality=85)
    return dest


def _flat(path: Path, color: tuple[int, int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 48), color).save(path, format="JPEG")
    return path
