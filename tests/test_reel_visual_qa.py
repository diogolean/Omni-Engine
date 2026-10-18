# -*- coding: utf-8 -*-
"""Reel visual QA: fail-closed judge, retries, beats, MP4 gate, publish."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from modules.reel_qa.beats import pack_sentences, refine_reel_beats
from modules.reel_qa.mp4_gate import decide_mp4_gate
from modules.reel_qa.pipeline import seal_compiled_reel
from modules.reel_qa.publish import planner_row_allowed, row_publishable
from modules.reel_qa.relevance import evaluate_chunk_relevance
from modules.reel_visual_qa import generate_and_gate


def _png(path: Path) -> Path:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
    return path


def _ok_text(score: float = 0.91) -> str:
    return (
        '{"is_relevant": true, "relevance_score": %s, '
        '"detected_subjects": ["gear"], "rejection_reason": null}'
        % score
    )


class _Provider:
    def __init__(self, text: str, provider: str = "openrouter", model_id: str = "qwen") -> None:
        self.text = text
        self.provider = provider
        self.model_id = model_id

    def evaluate(self, prompt, paths, labels=None):
        del prompt, paths, labels
        return SimpleNamespace(text=self.text, provider=self.provider, model_id=self.model_id)


def test_unparseable_vision_is_not_a_pass(tmp_path: Path, monkeypatch) -> None:
    img = _png(tmp_path / "still.png")

    def _provider(name: str):
        del name
        return _Provider("the picture looks fine to me")

    monkeypatch.setattr("modules.reel_qa.relevance._provider", _provider)
    verdict = evaluate_chunk_relevance(img, "Bronze gears off Antikythera.", "gears")
    assert verdict["is_relevant"] is False
    assert verdict["qa_status"] == "unavailable"
    assert verdict["publishable"] is False
    assert str(verdict["rejection_reason"]).startswith("qa_unavailable")


def test_a_planned_scene_is_scored_against_the_shot_not_the_line(
    tmp_path: Path, monkeypatch,
) -> None:
    img = _png(tmp_path / "hall.png")
    seen: dict[str, str] = {}

    class _ShotProvider:
        provider = "gemini"
        model_id = "models/gemini-2.5-flash"

        def evaluate(self, prompt, paths, labels=None):
            del paths, labels
            seen["prompt"] = prompt
            return SimpleNamespace(
                text=(
                    '{"image_description": "a museum hall seen from across the room",'
                    '"shows_expected_subject": true, "anachronism": false,'
                    '"non_photoreal": false, "anatomy_defect": false,'
                    '"setting_contradicts_beat": false, "contains_forbidden_object": false,'
                    '"is_relevant": true, "relevance_score": 0.91,'
                    '"detected_subjects": ["museum hall"], "rejection_reason": null}'
                ),
                provider="gemini",
                model_id="models/gemini-2.5-flash",
            )

    monkeypatch.setattr(
        "modules.reel_qa.relevance._provider",
        lambda name: _ShotProvider(),
    )
    verdict = evaluate_chunk_relevance(
        img,
        "Corroded fragments sat in a museum drawer.",
        "EXPECTED SUBJECT: a museum archive seen from afar.",
        score_against="shot",
        shot_subject="a museum archive seen from afar",
        shot_setting="a museum archive seen from across the hall",
        shot_era="Hellenistic Greece",
    )
    assert "PLANNED SHOT" in seen["prompt"]
    assert "context only" in seen["prompt"].lower()
    assert verdict["is_relevant"] is True
    assert verdict["publishable"] is True


def test_a_flux_scene_that_shows_a_forbidden_object_fails(
    tmp_path: Path, monkeypatch,
) -> None:
    img = _png(tmp_path / "book.png")

    class _ObjectProvider:
        def evaluate(self, prompt, paths, labels=None):
            del prompt, paths, labels
            return SimpleNamespace(
                text=(
                    '{"image_description": "a gold ornament lying on an open book",'
                    '"shows_expected_subject": true, "anachronism": false,'
                    '"non_photoreal": false, "anatomy_defect": false,'
                    '"setting_contradicts_beat": false, "contains_forbidden_object": false,'
                    '"is_relevant": true, "relevance_score": 0.9,'
                    '"detected_subjects": ["book"], "rejection_reason": null}'
                ),
                provider="gemini",
                model_id="models/gemini-2.5-flash",
            )

    monkeypatch.setattr(
        "modules.reel_qa.relevance._provider",
        lambda name: _ObjectProvider(),
    )
    verdict = evaluate_chunk_relevance(
        img,
        "Some researchers believe the record is incomplete.",
        "EXPECTED SUBJECT: an old manuscript on a desk.",
        score_against="shot",
        shot_subject="an old manuscript on a desk",
        shot_setting="a wooden desk",
        shot_era="Hellenistic",
    )
    assert verdict["is_relevant"] is False
    assert "contains_forbidden_object" in str(verdict["rejection_reason"])


def test_model_chain_uses_gemini_when_qwen_missing(tmp_path: Path, monkeypatch) -> None:
    img = _png(tmp_path / "still.png")

    def _provider(name: str):
        if name == "qwen":
            return None
        return _Provider(_ok_text(), provider="gemini", model_id="models/gemini-2.5-flash")

    monkeypatch.setattr("modules.reel_qa.relevance._provider", _provider)
    verdict = evaluate_chunk_relevance(img, "Bronze gears off Antikythera.", "gears")
    assert verdict["is_relevant"] is True
    assert verdict["provider"] == "gemini"
    assert verdict["qa_status"] == "scored"


def test_both_providers_down_fail_closed(tmp_path: Path, monkeypatch) -> None:
    img = _png(tmp_path / "still.png")
    monkeypatch.setattr("modules.reel_qa.relevance._provider", lambda name: None)
    verdict = evaluate_chunk_relevance(img, "Divers found the dial.", "dial")
    assert verdict["is_relevant"] is False
    reason = str(verdict["rejection_reason"])
    assert "gemini_unavailable" in reason
    assert "qwen_unavailable" in reason
    assert reason.index("gemini") < reason.index("qwen")


def test_retry_refines_sentence_and_entity_and_skips_placeholder(
    tmp_path: Path, monkeypatch,
) -> None:
    prompts: list[str] = []

    def _generate(prompt: str, **kwargs) -> Path:
        prompts.append(prompt)
        return _png(tmp_path / f"{kwargs['output_stem']}.png")

    state = {"n": 0}

    def _judge(path, text, prompt, banned_subjects=None):
        del path, text, prompt, banned_subjects
        state["n"] += 1
        if state["n"] == 1:
            return {
                "is_relevant": False,
                "relevance_score": 0.2,
                "detected_subjects": ["pyramid"],
                "rejection_reason": "HARD_REJECT: Detected out-of-scope element: pyramid",
                "qa_status": "scored",
                "publishable": False,
                "hard_reject": True,
            }
        return {
            "is_relevant": True,
            "relevance_score": 0.93,
            "detected_subjects": ["Antikythera"],
            "rejection_reason": None,
            "qa_status": "scored",
            "publishable": True,
        }

    def _boom(*args, **kwargs):
        raise AssertionError("cached placeholder must not be used")

    monkeypatch.setattr(
        "modules.reel_visual_qa.resolve_cached_channel_background",
        _boom,
    )
    result = generate_and_gate(
        _generate,
        prompt="a monument",
        chunk_text="Off Antikythera, divers found bronze gears.",
        output_stem="act01",
        output_directory=tmp_path,
        domain_anchors="Antikythera",
        max_attempts_n=3,
        evaluate_fn=_judge,
    )
    path, extra = result
    assert result.publishable is True
    assert path is not None and path.is_file()
    assert extra == 1
    assert len(prompts) == 2
    assert "Narration sentence: Off Antikythera, divers found bronze gears." in prompts[1]
    assert "Antikythera" in prompts[1]
    assert "pyramid" in prompts[1].lower()
    assert not list(tmp_path.glob("*fallback*"))


def test_unavailable_judge_does_not_keep_retrying(tmp_path: Path) -> None:
    calls = {"n": 0}

    def _generate(prompt: str, **kwargs) -> Path:
        del prompt
        calls["n"] += 1
        return _png(tmp_path / f"{kwargs['output_stem']}.png")

    def _judge(path, text, prompt, banned_subjects=None):
        del path, text, prompt, banned_subjects
        return {
            "is_relevant": False,
            "relevance_score": 0.0,
            "qa_status": "unavailable",
            "rejection_reason": "qa_unavailable:qwen_unavailable",
            "publishable": False,
        }

    result = generate_and_gate(
        _generate,
        prompt="gears",
        chunk_text="Bronze gears.",
        output_stem="act",
        output_directory=tmp_path,
        max_attempts_n=3,
        evaluate_fn=_judge,
    )
    assert calls["n"] == 1
    assert result.publishable is False
    assert result.path is not None


def test_exhausted_retries_block_without_placeholder(tmp_path: Path) -> None:
    def _generate(prompt: str, **kwargs) -> Path:
        del prompt
        return _png(tmp_path / f"{kwargs['output_stem']}.png")

    def _judge(path, text, prompt, banned_subjects=None):
        del path, text, prompt, banned_subjects
        return {
            "is_relevant": False,
            "relevance_score": 0.1,
            "rejection_reason": "below_relevance_threshold",
            "qa_status": "scored",
            "publishable": False,
            "detected_subjects": [],
        }

    result = generate_and_gate(
        _generate,
        prompt="desert",
        chunk_text="Colossal drawings scar the Nazca plain.",
        output_stem="nazca",
        output_directory=tmp_path,
        max_attempts_n=2,
        evaluate_fn=_judge,
    )
    assert result.publishable is False
    assert result.extra_generations == 1
    assert not list(tmp_path.glob("*fallback*"))
    row = result.beat_row(index=0, text="Colossal drawings scar the Nazca plain.")
    assert row["passed"] is False
    assert row["attempts"] == 2


def test_sentence_groups_stay_within_max_seconds() -> None:
    text = (
        "The bronze gears turned. "
        "The ship sank off Antikythera. "
        "Divers found the dial."
    )
    groups = pack_sentences(text, 12.0, min_s=4.0, max_s=5.0)
    assert len(groups) == 3
    assert all(dur <= 5.05 for _, dur in groups)
    assert any("Antikythera" in text for text, _dur in groups)


def test_long_act_splits_into_beats() -> None:
    text = (
        "The bronze gears turned. "
        "The ship sank off Antikythera. "
        "Divers found the dial."
    )
    plan = refine_reel_beats(
        snippets=[text],
        durations=[12.0],
        topic="The Antikythera Mechanism",
    )
    assert plan.changed is True
    assert len(plan.snippets) == 3
    assert all(d <= 5.05 for d in plan.durations)
    joined = " ".join(plan.scenes[i]["image_generation_prompt"] for i in range(3))
    assert "Antikythera" in joined


def test_short_beats_are_left_alone() -> None:
    plan = refine_reel_beats(
        snippets=["Divers found the dial.", "Scholars rebuilt it."],
        durations=[4.2, 4.8],
        scenes=[{"spoken_text": "Divers found the dial."}],
    )
    assert plan.changed is False
    assert plan.snippets[0].startswith("Divers")


def test_mp4_gate_rejects_short_silent_and_mismatch() -> None:
    short = decide_mp4_gate(
        {"error": "", "duration_s": 8.0, "has_audio": True, "mean_volume_db": -16.0},
        [{"passed": True, "text": "hello"}],
    )
    assert short["passed"] is False
    assert "duration_below_minimum" in short["reasons"]

    silent = decide_mp4_gate(
        {"error": "", "duration_s": 20.0, "has_audio": False, "mean_volume_db": None},
        [{"passed": True}],
    )
    assert "audio_missing" in silent["reasons"]

    quiet = decide_mp4_gate(
        {"error": "", "duration_s": 20.0, "has_audio": True, "mean_volume_db": -70.0},
        [{"passed": True}],
    )
    assert "audio_too_quiet" in quiet["reasons"]

    mismatch = decide_mp4_gate(
        {"error": "", "duration_s": 20.0, "has_audio": True, "mean_volume_db": -16.0},
        [{"passed": False, "text": "gears", "reason": "below_relevance_threshold"}],
    )
    assert "frame_subtitle_mismatch" in mismatch["reasons"]

    ok = decide_mp4_gate(
        {"error": "", "duration_s": 20.0, "has_audio": True, "mean_volume_db": -16.0},
        [{"passed": True, "text": "gears"}],
    )
    assert ok["passed"] is True


def test_seal_writes_report_and_blocks_quiet_reel(tmp_path: Path) -> None:
    video = tmp_path / "clips" / "reel_v01.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"mp4")
    still = _png(tmp_path / "still.png")

    def _judge(path, text, prompt, banned_subjects=None):
        del path, text, prompt, banned_subjects
        return {
            "is_relevant": True,
            "relevance_score": 0.95,
            "qa_status": "scored",
            "rejection_reason": None,
            "detected_subjects": ["gear"],
            "publishable": True,
        }

    def _probe(path: Path) -> dict:
        del path
        return {
            "error": "",
            "duration_s": 22.0,
            "has_audio": True,
            "mean_volume_db": -80.0,
            "max_volume_db": -70.0,
        }

    def _frame(video_path: Path, at_s: float, dest: Path) -> Path:
        del video_path, at_s
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"jpg")
        return dest

    report = seal_compiled_reel(
        video_path=video,
        post_type="WAN_REEL",
        beats=[{
            "index": 0,
            "text": "Divers found bronze gears.",
            "image_path": str(still),
            "passed": True,
            "score": 0.95,
            "start_s": 0.0,
            "end_s": 4.0,
        }],
        word_timings=[
            ("Divers", 0.0, 0.5),
            ("found", 0.5, 1.0),
            ("bronze", 1.0, 1.5),
            ("gears", 1.5, 2.0),
        ],
        output_dir=tmp_path / "qa",
        stem="reel_v01",
        evaluate_fn=_judge,
        probe_fn=_probe,
        frame_fn=_frame,
    )
    assert report["verdict"] == "fail"
    assert report["publishable"] is False
    assert "audio_too_quiet" in report["blocked_reason"]
    sibling = video.with_name("reel_v01.qa.json")
    assert sibling.is_file()
    assert (tmp_path / "qa" / "reel_v01.qa.json").is_file()
    assert report["beats"][0]["score"] == 0.95
    assert report["mp4"]["frames"]


def test_publish_requires_pass_for_every_reel_type() -> None:
    assert row_publishable({"post_type": "WAN_REEL", "qa_verdict": "pass", "video_path": "a.mp4"})
    assert row_publishable({"post_type": "ECONOMIC_REEL_LOFI", "qa_verdict": "fail"}) is False
    assert row_publishable({"post_type": "REFERENCE_BASED_REELS", "video_path": "a.mp4"}) is False
    assert row_publishable({"post_type": "LONG_CAPTION_IMAGE"}) is True
    assert planner_row_allowed(post_type="ECONOMIC_REEL", report={"verdict": "pass"})
    assert planner_row_allowed(post_type="ECONOMIC_REEL", report=None) is False
    assert planner_row_allowed(post_type="SMART_BAIT", report=None) is True
