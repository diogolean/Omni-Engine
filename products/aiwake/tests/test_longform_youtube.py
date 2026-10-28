# -*- coding: utf-8 -*-
"""Long-form chapters, cheap seats, bounded ffmpeg, and the Shorts gate."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from products.aiwake.longform.cost import estimate_episode_cost
from products.aiwake.longform.ffmpeg_guard import UnboundedFfmpeg, assert_bounded_ffmpeg
from products.aiwake.longform.metadata import (
    ChapterMark,
    build_youtube_package,
    chapter_marks,
    short_cta,
)
from products.aiwake.longform.plan import build_episode_plan, pacing_brief
from products.aiwake.longform.qa import MediaProbe, duration_window, evaluate_package
from products.aiwake.longform.seats import ExpensiveModelRefused, resolve_longform_seats
from products.aiwake.longform.shorts import choose_windows
from products.aiwake.memory import _jaccard, _tokenise
from products.aiwake.models.offline import (
    _LONG_FORM_PROVOCATIONS,
    _LONG_FORM_REBUTTALS,
    reset_long_form_cursor,
)
from products.aiwake.pipeline import matchup_is_legal
from products.aiwake.settings import load_settings


def test_six_minute_plan_has_intro_rounds_and_outro() -> None:
    settings = load_settings()
    plan = build_episode_plan(settings.long_format, target_duration_s=360)
    kinds = [segment.kind for segment in plan.segments]
    assert plan.resolution == (1920, 1080)
    assert plan.turns == 20
    assert kinds[0] == "intro"
    assert kinds[-1] == "outro"
    assert kinds.count("round") >= 2
    assert abs(sum(segment.target_s for segment in plan.segments) - 360) < 0.1
    brief = pacing_brief(plan, plan.segments[-1].start_exchange)
    assert "LONG-FORM ACT" in brief
    assert "No new topic" in brief


def test_ten_minute_plan_stays_inside_the_turn_cap() -> None:
    settings = load_settings()
    plan = build_episode_plan(settings.long_format, target_duration_s=600)
    assert settings.long_format.min_turns <= plan.turns <= settings.long_format.max_turns
    assert plan.segments[0].kind == "intro"
    assert plan.segments[-1].kind == "outro"


def test_chapters_start_at_zero_and_land_in_the_description() -> None:
    settings = load_settings()
    plan = build_episode_plan(settings.long_format, target_duration_s=360)
    chapters = chapter_marks(plan, boundaries_s=None, duration_s=360)
    package = build_youtube_package(
        topic="Who built you?",
        orchestrator="deepseek-chat",
        target="llama-70b",
        plan=plan,
        chapters=chapters,
    )
    assert chapters[0].start_s == 0
    assert package.description.splitlines()[-len(chapters)] == "0:00 The Opening Blow"
    assert "0:00 The Opening Blow" in package.description
    assert package.title.startswith("DeepSeek vs Llama")
    assert "AIWake" in package.tags


def test_short_metadata_points_at_the_long_video() -> None:
    cta = short_cta("DeepSeek vs Llama: Who built you?")
    assert cta.startswith('Full debate on the channel: "')
    assert "DeepSeek vs Llama" in cta


def test_cheap_pair_is_the_default_and_claude_is_refused() -> None:
    settings = load_settings()
    assert settings.long_format.allow_expensive is False
    left, right = resolve_longform_seats(settings, orchestrator=None, target=None)
    assert left == "deepseek-chat"
    assert right == "llama-70b"
    assert matchup_is_legal(left, right)
    with pytest.raises(ExpensiveModelRefused):
        resolve_longform_seats(settings, orchestrator="claude-sonnet", target="llama-70b")


def test_cost_stays_under_a_dime_for_the_cheap_pair() -> None:
    settings = load_settings()
    plan = build_episode_plan(settings.long_format, target_duration_s=360)
    cheap = estimate_episode_cost(
        plan,
        settings.long_format,
        orchestrator="deepseek-chat",
        target="llama-70b",
    )
    claude = estimate_episode_cost(
        plan,
        settings.long_format,
        orchestrator="claude-sonnet",
        target="llama-70b",
    )
    offline = estimate_episode_cost(
        plan,
        settings.long_format,
        orchestrator="deepseek-chat",
        target="llama-70b",
        offline=True,
    )
    assert offline.usd == 0
    assert 0 < cheap.usd < 0.10
    assert claude.usd > cheap.usd * 5


def test_ffmpeg_guard_rejects_open_ended_graphs() -> None:
    with pytest.raises(UnboundedFfmpeg):
        assert_bounded_ffmpeg(["ffmpeg", "-af", "apad"])
    with pytest.raises(UnboundedFfmpeg):
        assert_bounded_ffmpeg(["ffmpeg", "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest"])
    assert_bounded_ffmpeg(["ffmpeg", "-t", "36", "-i", "in.mp4", "-c:v", "libx264", "out.mp4"])
    assert_bounded_ffmpeg(["ffmpeg", "-af", "atrim=end=36,apad=whole_dur=36"])


def test_qa_rejects_a_short_file_and_a_teaser_under_15_seconds() -> None:
    settings = load_settings()
    low, high = duration_window(360, settings.long_format)
    assert low == 300
    assert high == 480
    report = evaluate_package(
        long_probe=MediaProbe("episode.mp4", 42.0, True, 1920, 1080),
        short_probes=[
            MediaProbe("short_01.mp4", 8.0, True, 1080, 1920),
            MediaProbe("short_02.mp4", 20.0, False, 1080, 1920),
        ],
        preset=settings.long_format,
        target_s=360,
        shorts_expected=2,
    )
    assert report.passed is False
    blob = " ".join(report.failures)
    assert "outside" in blob
    assert "under" in blob
    assert "no audio" in blob


def test_qa_accepts_a_package_inside_the_window() -> None:
    settings = load_settings()
    report = evaluate_package(
        long_probe=MediaProbe("episode.mp4", 372.0, True, 1920, 1080),
        short_probes=[
            MediaProbe("short_01.mp4", 36.0, True, 1080, 1920),
            MediaProbe("short_02.mp4", 36.0, True, 1080, 1920),
            MediaProbe("short_03.mp4", 28.0, True, 1080, 1920),
        ],
        preset=settings.long_format,
        target_s=360,
        shorts_expected=3,
    )
    assert report.passed is True
    assert report.failures == []


def test_three_teaser_windows_fit_a_six_minute_episode() -> None:
    settings = load_settings()
    plan = build_episode_plan(settings.long_format, target_duration_s=360)
    chapters = chapter_marks(plan, boundaries_s=None, duration_s=360)
    windows = choose_windows(
        duration_s=360,
        chapters=chapters,
        count=3,
        target_s=36,
        min_s=15,
        max_s=59,
    )
    assert len(windows) == 3
    assert all(end - start >= 15 for start, end, _side in windows)
    assert windows[0][1] <= windows[1][0]
    assert windows[1][1] <= windows[2][0]


def test_offline_lines_survive_the_long_form_validators() -> None:
    from products.aiwake.contracts import split_sentences
    from products.aiwake.personas import is_trivial_metric

    banned = (
        "vantage point",
        "structured uncertainty",
        "hedging",
        "emergent",
        "epistemic",
        "behavioral confession",
        "inner world",
    )
    assert len(_LONG_FORM_PROVOCATIONS) >= 20
    tokens = []
    for line in _LONG_FORM_PROVOCATIONS:
        words = line.split()
        assert 15 <= len(words) <= 25
        assert line.endswith("?")
        assert all(len(sentence.split()) <= 15 for sentence in split_sentences(line))
        assert not is_trivial_metric(line)
        assert not any(word in line.lower() for word in banned)
        tokens.append(_tokenise(line))
    for left in range(len(tokens)):
        for right in range(left + 1, len(tokens)):
            assert _jaccard(tokens[left], tokens[right]) < 0.55
    for line in _LONG_FORM_REBUTTALS:
        assert 20 <= len(line.split()) <= 40
        assert all(len(sentence.split()) <= 15 for sentence in split_sentences(line))
    reset_long_form_cursor()


def test_channel_cli_forwards_the_six_minute_episode() -> None:
    from channels_config.aiwake.cli import _forward, build_parser

    args = build_parser().parse_args(["longform", "--offline", "--target-duration", "360", "--shorts", "2"])
    forwarded = _forward(args)
    assert forwarded[:4] == ["--mode", "longform", "--resolution", "1920x1080"]
    assert "--offline" in forwarded
    assert "--target-duration" in forwarded
    assert forwarded[forwarded.index("--target-duration") + 1].startswith("360")
    assert "2" in forwarded


def test_offline_episode_writes_chapters_without_rendering(tmp_path: Path) -> None:
    from products.aiwake.pipeline import run_pipeline

    settings = load_settings()
    settings = settings.model_copy(
        update={
            "memory": settings.memory.model_copy(update={"persist": False}),
            "debate": settings.debate.model_copy(update={"turn_delay_s": 0}),
        }
    )
    result = run_pipeline(
        topic="Who built you?",
        turns=16,
        offline=True,
        with_audio=False,
        with_video=False,
        quiet=True,
        post_type="long_format",
        target_duration_s=360,
        settings=settings,
        output_dir=tmp_path,
        fresh_memory=True,
    )
    assert result.end_reason == "complete"
    assert result.exchanges == 16
    assert result.qa_passed is True
    assert result.cost_estimate_usd == 0
    assert result.metadata_path is not None
    payload = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert payload["resolution"] == [1920, 1080]
    assert payload["chapters"][0]["timestamp"].startswith("0:00")
    assert "Full debate" not in payload["description"]
    assert payload["post_type"] == "long_format"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_bounded_cut_makes_a_vertical_teaser(tmp_path: Path) -> None:
    from products.aiwake.longform.ffmpeg_guard import run_bounded
    from products.aiwake.longform.qa import probe_media
    from products.aiwake.longform.shorts import cut_or_regenerate_shorts

    settings = load_settings()
    preset = settings.long_format.model_copy(
        update={"shorts_width": 270, "shorts_height": 480, "shorts_target_s": 16, "shorts_min_s": 15}
    )
    master = tmp_path / "master.mp4"
    run_bounded(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=15:d=40",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=40",
            "-t",
            "40",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(master),
        ],
        timeout=60,
    )
    plan = build_episode_plan(settings.long_format, target_duration_s=360)
    chapters = [
        ChapterMark(0.0, "The Opening Blow", "intro"),
        ChapterMark(2.0, "Origin and Legitimacy", "round"),
        ChapterMark(20.0, "The Money Trail", "round"),
    ]
    clips = cut_or_regenerate_shorts(
        master=master,
        battle_audio=None,
        output_dir=tmp_path / "shorts",
        plan=plan,
        chapters=chapters,
        preset=preset,
        long_title="DeepSeek vs Llama: Who built you?",
        topic="Who built you?",
        duration_s=40,
        count=2,
    )
    assert len(clips) == 2
    for clip in clips:
        assert short_cta("DeepSeek vs Llama: Who built you?") in str(clip.metadata["description"])
        probe = probe_media(clip.path)
        assert probe.has_audio
        assert probe.duration_s is not None and probe.duration_s >= 15
        assert probe.height > probe.width
