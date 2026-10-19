# -*- coding: utf-8 -*-
"""The closing CTA overlay is our own text, not a subtitle mismatch.

s23b blocked both reels: the last sampled frame sat under the cue
"for more hidden mysteries." and the judge scored the CTA card as
contains_text and non_photoreal. A real mismatch must still fail.
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image

from modules.reel_qa.mp4_gate import (
    SubtitleCue,
    cta_tail_cues,
    cues_from_word_timings,
    gate_final_mp4,
)

CTA = "Follow Ancient Knowledge for more hidden mysteries."
NARRATION = (
    "Göbekli Tepe's carved stone pillars date to 9600 BC. "
    "This site predates Egypt by thousands of years."
)


def _timings(text: str, start: float = 0.0) -> list[tuple[str, float, float]]:
    rows = []
    at = start
    for word in text.split():
        rows.append((word, at, at + 0.4))
        at += 0.45
    return rows


def _words() -> list[tuple[str, float, float]]:
    narration = _timings(NARRATION)
    return narration + _timings(CTA, start=narration[-1][2] + 1.0)


def _probe(_path: Path) -> dict:
    return {"error": "", "duration_s": 60.0, "has_audio": True, "mean_volume_db": -16.0}


def _textured(path: Path) -> Path:
    rng = random.Random(5)
    image = Image.new("RGB", (108, 192))
    image.putdata([
        tuple(rng.randrange(40, 220) for _ in range(3)) for _ in range(108 * 192)
    ])
    image.save(path)
    return path


def _run(tmp_path: Path, cues: list[SubtitleCue], judge, cta_text: str) -> dict:
    frame = _textured(tmp_path / "source.jpg")

    def _grab(_video: Path, _at: float, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(frame.read_bytes())
        return dest

    return gate_final_mp4(
        tmp_path / "reel.mp4",
        cues,
        probe_fn=_probe,
        evaluate_fn=judge,
        frame_fn=_grab,
        work_dir=tmp_path / "frames",
        beats=[],
        cta_text=cta_text,
    )


def _cta_card_judge(seen: list[str]):
    """Fails anything that looks like the CTA card, passes the rest."""

    def judge(_path, text, *_args, **_kwargs):
        seen.append(str(text))
        if "mysteries" in str(text) or "Follow" in str(text):
            return {
                "is_relevant": False,
                "relevance_score": 0.2,
                "qa_status": "scored",
                "rejection_reason": "documentary_fail:non_photoreal,contains_text",
            }
        return {"is_relevant": True, "relevance_score": 0.9, "qa_status": "scored"}

    return judge


def test_the_s23b_cta_tail_frame_passes_when_the_cta_is_known(tmp_path: Path) -> None:
    cues = cues_from_word_timings(_words())
    assert cues[-1].text == "for more hidden mysteries."
    seen: list[str] = []
    result = _run(tmp_path, cues, _cta_card_judge(seen), CTA)
    assert result["passed"] is True, result["reasons"]
    tail = [row for row in result["frames"] if row["text"] == "for more hidden mysteries."]
    assert tail and tail[0]["reason"] == "cta-overlay"
    assert not any("mysteries" in text for text in seen)


def test_without_the_cta_string_the_same_frame_still_fails(tmp_path: Path) -> None:
    result = _run(tmp_path, cues_from_word_timings(_words()), _cta_card_judge([]), "")
    assert result["passed"] is False
    assert "frame_subtitle_mismatch" in result["reasons"]


def test_a_real_mismatch_at_the_end_still_fails(tmp_path: Path) -> None:
    """The last cue is narration, not the CTA: it is judged and it fails."""
    cues = cues_from_word_timings(_timings(NARRATION))

    def judge(_path, _text, *_args, **_kwargs):
        return {
            "is_relevant": False,
            "relevance_score": 0.1,
            "qa_status": "scored",
            "rejection_reason": "setting_contradicts_beat",
        }

    result = _run(tmp_path, cues, judge, CTA)
    assert result["passed"] is False
    assert "frame_subtitle_mismatch" in result["reasons"]


def test_only_the_closing_cta_run_is_whitelisted() -> None:
    cues = [
        SubtitleCue("for more hidden mysteries.", 1.0, 2.0),
        SubtitleCue("the pillars stand tall", 3.0, 4.0),
        SubtitleCue("of years. Follow Ancient", 5.0, 6.0),
        SubtitleCue("Knowledge for more hidden", 6.0, 7.0),
        SubtitleCue("mysteries.", 7.0, 7.5),
    ]
    # A CTA phrase spoken mid-script and a cue that mixes narration stay judged.
    assert cta_tail_cues(cues, CTA) == {3, 4}
    assert cta_tail_cues(cues, "") == set()
