# -*- coding: utf-8 -*-
"""Ancient Knowledge one-video gate.

The Dogon QA file on the operator machine said verdict=pass while 7 of 12
beats were reuse=fallback, score=0, with a rejection reason. The MP4 check
looked at 0.911s and 78.34s and trusted the first as beat-approved. These
tests lock that shape as a failure, plus the neighbouring holes that let a
flat card or a missing approval through.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from production.media.text_utils import reel_output_name
from production.qa.acceptance import (
    approval_state,
    distribution_ready,
    write_approval,
)
from production.qa.local_gate import check_still
from production.qa.mp4_gate import SubtitleCue, coverage_cues, frame_fault, gate_final_mp4
from production.qa.pipeline import seal_compiled_reel


def _texture(path: Path, seed: int = 3) -> Path:
    image = Image.new("RGB", (96, 160), (20, 24, 28))
    pixels = image.load()
    for y in range(160):
        for x in range(96):
            if (x + seed) % 9 < 4:
                pixels[x, y] = ((40 + seed * 20) % 255, (80 + y) % 255, (30 + x * 3) % 255)
    image.save(path, format="JPEG", quality=90)
    return path


def _probe(duration: float = 80.0) -> dict:
    return {
        "error": "",
        "duration_s": duration,
        "has_audio": True,
        "mean_volume_db": -16.0,
        "max_volume_db": -3.0,
    }


def _pass_judge(*_args: object, **_kwargs: object) -> dict:
    return {
        "is_relevant": True,
        "relevance_score": 0.91,
        "qa_status": "scored",
        "rejection_reason": "",
    }


def test_static_fallback_opening_is_flat_and_not_a_black_frame(tmp_path: Path) -> None:
    from production.formats.economic_reel.artefact.reuse import guarantee_still, reset_approved

    reset_approved()
    path = guarantee_still(tmp_path / "act01_fallback.jpg", 0)
    with Image.open(path) as image:
        red, green, blue = image.getpixel((0, 0))
    # JPEG rounds the card. The source colour is (86, 108, 72).
    assert abs(red - 86) <= 4 and abs(green - 108) <= 4 and abs(blue - 72) <= 4
    assert frame_fault(path) == "flat"
    gate = check_still(path, narration="Dogon knowledge of Sirius", remember=False)
    # The pale marker bar keeps Laplacian sharpness above the still-gate
    # floor, and a short edge is only a note. The still gate does not
    # reject this card. The seal has to.
    assert gate.ok is True
    assert gate.sharpness > 8.0


def test_dogon_fallback_rows_cannot_seal_as_pass(tmp_path: Path) -> None:
    """The shape of the_sirius_mystery_dogon_4ed43c_v01_v01.qa.json."""
    from production.formats.economic_reel.artefact.reuse import guarantee_still, reset_approved

    reset_approved()
    fallback = guarantee_still(tmp_path / "act01_fallback.jpg", 0)
    good = _texture(tmp_path / "later.jpg", 5)
    beats: list[dict] = []
    for index in range(7):
        beats.append(
            {
                "index": index,
                "text": "Dogon knowledge of Sirius and the star",
                "image_path": str(fallback),
                "score": 0,
                "passed": True,
                "reason": "documentary_fail:non_photoreal,anatomy",
                "reuse": "fallback",
                "qa_status": "scored",
                "attempts": 3,
            }
        )
    for index in range(7, 12):
        beats.append(
            {
                "index": index,
                "text": f"later line {index} about the temple",
                "image_path": str(good),
                "score": 0.91,
                "passed": True,
                "reason": "",
                "qa_status": "scored",
            }
        )

    def _frame(_video: Path, _at: float, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(good.read_bytes())
        return dest

    video = tmp_path / "reel.mp4"
    video.write_bytes(b"not-a-real-reel")
    report = seal_compiled_reel(
        video_path=video,
        post_type="ECONOMIC_REEL",
        beats=beats,
        word_timings=[("Dogon", 0.2, 0.8), ("knowledge", 0.8, 1.4)],
        output_dir=tmp_path / "qa",
        stem="the_sirius_mystery_dogon_4ed43c_v01_v01",
        channel="ancient_knowledge",
        evaluate_fn=_pass_judge,
        probe_fn=lambda _path: _probe(),
        frame_fn=_frame,
    )
    assert report["verdict"] == "fail"
    assert report["publishable"] is False
    assert "static_fallback" in report["blockers"]
    assert "rejection_marked_passed" in report["blockers"]
    assert report["semantic_status"] == "needs_human_review"
    assert report["certifies_truth"] is False
    assert report.get("review_path")


def test_flat_opening_fails_even_when_the_beat_says_approved(tmp_path: Path) -> None:
    from production.formats.economic_reel.artefact.reuse import guarantee_still, reset_approved

    reset_approved()
    card = guarantee_still(tmp_path / "card.jpg", 0)

    def _frame(_video: Path, _at: float, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(card.read_bytes())
        return dest

    decision = gate_final_mp4(
        tmp_path / "reel.mp4",
        [SubtitleCue(text="on Dogon knowledge of", start_s=0.4, end_s=1.4)],
        probe_fn=lambda _path: _probe(),
        evaluate_fn=_pass_judge,
        frame_fn=_frame,
        beats=[{
            "index": 0,
            "text": "on Dogon knowledge of Sirius",
            "passed": True,
            "score": 0.91,
            "image_path": str(card),
            "reason": "",
        }],
    )
    assert decision["passed"] is False
    assert "frame_flat" in decision["reasons"]


def test_corrupt_and_missing_sources_block(tmp_path: Path) -> None:
    bad = tmp_path / "act.jpg"
    bad.write_text("not an image", encoding="utf-8")
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"mp4")
    picture = _texture(tmp_path / "ok.jpg")

    def _write_texture(_video: Path, _at: float, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(picture.read_bytes())
        return dest

    corrupt = seal_compiled_reel(
        video_path=video,
        post_type="ECONOMIC_REEL",
        beats=[{
            "index": 0,
            "text": "a temple",
            "image_path": str(bad),
            "passed": True,
            "score": 0.91,
            "reason": "",
            "qa_status": "scored",
        }],
        word_timings=[("a", 0.2, 0.6), ("temple", 0.6, 1.2)],
        output_dir=tmp_path / "qa",
        stem="corrupt",
        evaluate_fn=_pass_judge,
        probe_fn=lambda _path: _probe(),
        frame_fn=_write_texture,
    )
    assert "unreadable_source" in corrupt["blockers"]
    assert corrupt["verdict"] == "fail"

    missing = seal_compiled_reel(
        video_path=None,
        post_type="ECONOMIC_REEL",
        beats=[],
        output_dir=tmp_path / "qa2",
        stem="missing",
        probe_fn=lambda _path: _probe(),
    )
    assert missing["verdict"] == "fail"
    assert missing["publishable"] is False


def test_repeated_stills_block(tmp_path: Path) -> None:
    picture = _texture(tmp_path / "same.jpg", 2)
    beats = []
    for index in range(2):
        beats.append(
            {
                "index": index,
                "text": f"line {index} about stone",
                "image_path": str(picture),
                "passed": True,
                "score": 0.91,
                "reason": "",
                "qa_status": "scored",
            }
        )
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"mp4")

    def _frame(_video: Path, _at: float, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(picture.read_bytes())
        return dest

    report = seal_compiled_reel(
        video_path=video,
        post_type="ECONOMIC_REEL",
        beats=beats,
        word_timings=[("line", 0.2, 1.0), ("stone", 1.0, 1.8)],
        output_dir=tmp_path / "qa",
        stem="repeat",
        evaluate_fn=_pass_judge,
        probe_fn=lambda _path: _probe(),
        frame_fn=_frame,
    )
    assert report["verdict"] == "fail"
    assert "repeated_still" in report["blockers"]


def test_timeline_coverage_is_not_only_the_first_and_last_cue() -> None:
    cues = [
        SubtitleCue(text=f"line {index} alpha beta", start_s=index * 6.0, end_s=index * 6.0 + 2.0)
        for index in range(12)
    ]
    chosen = coverage_cues(cues)
    times = [cue.midpoint_s for cue in chosen]
    assert times[0] < 2.0
    assert times[-1] > 60.0
    assert any(10.0 < mark < 60.0 for mark in times)
    assert len(chosen) == 12


def test_middle_of_a_long_cut_is_sampled(tmp_path: Path) -> None:
    seen: list[float] = []
    picture = _texture(tmp_path / "frame.jpg", 6)

    def _frame(_video: Path, at: float, dest: Path) -> Path:
        seen.append(at)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(picture.read_bytes())
        return dest

    cues = [
        SubtitleCue(text=f"spoken {index} words here", start_s=index * 6.0, end_s=index * 6.0 + 2.0)
        for index in range(8)
    ]
    gate_final_mp4(
        tmp_path / "reel.mp4",
        cues,
        probe_fn=lambda _path: _probe(),
        evaluate_fn=_pass_judge,
        frame_fn=_frame,
        beats=[],
    )
    assert any(10.0 < mark < 40.0 for mark in seen)
    assert any(mark < 1.0 for mark in seen)


def test_valid_media_still_needs_a_current_human_approval(tmp_path: Path) -> None:
    picture = _texture(tmp_path / "scene.jpg", 8)
    video = tmp_path / "reel_candidate.mp4"
    video.write_bytes(b"candidate-bytes-v1")

    def _frame(_video: Path, _at: float, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(picture.read_bytes())
        return dest

    report = seal_compiled_reel(
        video_path=video,
        post_type="ECONOMIC_REEL",
        beats=[{
            "index": 0,
            "text": "The stone course is still in place",
            "image_path": str(picture),
            "passed": True,
            "score": 0.91,
            "reason": "",
            "qa_status": "scored",
        }],
        word_timings=[
            ("The", 0.2, 0.5),
            ("stone", 0.5, 0.9),
            ("course", 0.9, 1.3),
            ("remains", 1.3, 1.8),
        ],
        output_dir=tmp_path / "qa",
        stem="candidate",
        channel="ancient_knowledge",
        evaluate_fn=_pass_judge,
        probe_fn=lambda _path: _probe(40.0),
        frame_fn=_frame,
    )
    assert report["verdict"] == "pass"
    assert report["certifies_truth"] is False
    assert report["semantic_status"] == "needs_human_review"
    row = {
        "post_type": "ECONOMIC_REEL",
        "qa_verdict": report["verdict"],
        "qa_report_path": report["report_path"],
        "video_path": str(video),
        "qa_blockers": report["blockers"],
    }
    assert distribution_ready(row) is False
    assert approval_state(video, qa_report=report["report_path"]) == "missing"
    write_approval(video, approver="diogo", qa_report=report["report_path"])
    assert approval_state(video, qa_report=report["report_path"]) == "current"
    assert distribution_ready(row) is True
    video.write_bytes(b"candidate-bytes-v1-changed")
    assert approval_state(video, qa_report=report["report_path"]) == "stale"
    assert distribution_ready(row) is False


def test_master_mei_quantity_one_hooks_can_share_one_v01_path() -> None:
    """Recorded collision. Not the Dogon publish bug.

    Separate --quantity 1 executions both use variant 0, so both files are
    _v01. One batch would have numbered them _v02 and _v06.
    """
    ghost = reel_output_name("Your craving is a ghost. Starve it", 0, stem="unused")
    ghost_b = reel_output_name("Your craving is a ghost. Starve the feed", 0, stem="unused")
    wall = reel_output_name("Your screen is the cave wall", 0, stem="unused")
    assert ghost == ghost_b == "reel_your_craving_is_a_ghost__starve__v01.mp4"
    assert wall == "reel_your_screen_is_the_cave_wall_v01.mp4"
    assert reel_output_name("Your screen is the cave wall", 1, stem="unused").endswith("_v02.mp4")
    assert reel_output_name("Your screen is the cave wall", 5, stem="unused").endswith("_v06.mp4")
