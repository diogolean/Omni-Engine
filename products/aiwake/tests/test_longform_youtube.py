# -*- coding: utf-8 -*-
"""The YouTube side of a long episode: copy, chapters, cost, seats, the CLI,
bounded ffmpeg, the pre-publish gate, and the Shorts cut from the master."""
from __future__ import annotations

import json
import shutil
import wave
from dataclasses import replace
from functools import cache
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from products.aiwake.contracts import SpeakerRole, Utterance
from products.aiwake.longform.cost import actual_episode_cost, estimate_episode_cost, rate_for
from products.aiwake.longform.ffmpeg_guard import (
    UnboundedFfmpeg,
    assert_bounded_ffmpeg,
    ffmpeg_binary,
    run_bounded,
)
from products.aiwake.longform.metadata import (
    TAGS_MAX_CHARS,
    ChapterMark,
    build_youtube_package,
    chapter_problems,
    format_timestamp,
    is_grounded,
    quote_candidates,
    seat_label,
    short_metadata,
    three_hashtags,
    title_score,
    youtube_chapters,
    youtube_tags,
    youtube_title,
)
from products.aiwake.longform.plan import END_HOLD_S, LINE_OVERHEAD_S, TURN_GAP_S, build_episode_plan
from products.aiwake.longform.qa import (
    MediaProbe,
    duration_window,
    episode_problems,
    episode_warnings,
    evaluate_package,
    probe_media,
    voice_problems,
)
from products.aiwake.longform.seats import ExpensiveModelRefused, longform_roster, resolve_longform_seats
from products.aiwake.longform.shorts import Line, choose_highlights, cut_master, line_timeline, pick_quotes
from products.aiwake.media.audio import AudioAsset
from products.aiwake.pipeline import matchup_is_legal
from products.aiwake.settings import AiwakeSettings, LongFormatPreset, load_settings

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg and ffprobe are not installed")
COLD_OPEN = "Who wrote the words you just said?"


@cache
def _settings() -> AiwakeSettings:
    return load_settings()


def _preset() -> LongFormatPreset:
    return _settings().long_format


def _with_long_format(settings: AiwakeSettings, **changes: object) -> AiwakeSettings:
    return settings.model_copy(update={"long_format": settings.long_format.model_copy(update=changes)})


def _probe(
    name: str,
    seconds: float,
    *,
    audio: bool = True,
    size: tuple[int, int] = (1080, 1920),
    peak_db: float | None = None,
) -> MediaProbe:
    return MediaProbe(name, seconds, audio, size[0], size[1], peak_db=peak_db)


def _utterance(turn: int, role: SpeakerRole, text: str) -> Utterance:
    return Utterance(turn_index=turn, role=role, speaker_name=role.value, text=text)


def _voice(path: Path, *, voice: str = "en-US-GuyNeural") -> AudioAsset:
    """A voice asset whose file exists. The gate checks the file, never its sound."""
    path.write_bytes(b"\xff\xfb" + bytes(1024))
    return AudioAsset(path=path, duration_s=2.0, voice=voice)


# -- Title, description, tags, hashtags -----------------------------------------


def test_title_quotes_the_cold_open_whole() -> None:
    title = youtube_title(hook=COLD_OPEN, topic="Who built you?", orchestrator="deepseek-chat", target="llama-70b")
    assert title == f'"{COLD_OPEN}" | DeepSeek vs Llama (Full Debate)'


def test_title_never_cuts_a_quote() -> None:
    hook = (
        "Every sentence you produced today was assembled from the unpaid labour "
        "of millions of strangers, wasn't it?"
    )
    grounded = youtube_title(hook=hook, topic="Who built you?", orchestrator="deepseek-chat", target="llama-70b")
    assert grounded == "DeepSeek vs Llama: Who built you?"
    ungrounded = youtube_title(
        hook=hook,
        topic="Is your business model theft?",
        orchestrator="deepseek-chat",
        target="llama-70b",
    )
    assert ungrounded == "DeepSeek vs Llama | Full AI Debate"


def test_title_prefers_a_line_that_stands_alone() -> None:
    weak, strong = "So that is also the same thing.", "You sold your voice to the highest bidder."
    assert title_score(strong) > 0 > title_score(weak)
    title = youtube_title(
        hook="x" * 80,
        topic="",
        orchestrator="deepseek-chat",
        target="llama-70b",
        alternates=[weak, strong],
    )
    assert title == f'"{strong}" | DeepSeek vs Llama'


def test_quote_candidates_skip_fragments_and_nested_quotes() -> None:
    lines = ["Hi there.", 'He said "no" to me today.', "Is this yours? I think it is mine today."]
    assert quote_candidates(lines) == ["Is this yours? I think it is mine today.", "I think it is mine today."]


def test_reserved_words_appear_only_when_spoken() -> None:
    assert not is_grounded("AI business model", "Who built you?")
    assert is_grounded("AI business model", "Your business is my words.")
    silent = youtube_tags("deepseek-chat", "llama-70b", opening_category="money", spoken="Who built you?")
    spoken = youtube_tags("deepseek-chat", "llama-70b", opening_category="money", spoken="Your business is my words.")
    assert "AI business model" not in silent
    assert "AI business model" in spoken


def test_tags_fit_youtube_budget() -> None:
    tags = youtube_tags(
        "deepseek-chat",
        "llama-70b",
        opening_category="origins",
        round_keys=["parasite_mind", "the_corporate_leash", "glorified_appliance", "hallucination_fraud"],
        spoken="",
    )
    budget = sum(len(tag) + (2 if " " in tag else 0) for tag in tags) + len(tags) - 1
    assert budget <= TAGS_MAX_CHARS
    assert tags[:3] == ["DeepSeek vs Llama", "AI vs AI", "AI debate"]
    assert len({tag.lower() for tag in tags}) == len(tags)


def test_description_hooks_tells_the_fight_and_lists_the_chapters() -> None:
    plan = build_episode_plan(_preset(), target_duration_s=360)
    chapters = [
        ChapterMark(0.0, "The First Question", "intro"),
        ChapterMark(14.0, plan.rounds[0].title, "round"),
        ChapterMark(120.0, plan.rounds[1].title, "round"),
        ChapterMark(240.0, plan.rounds[2].title, "round"),
        ChapterMark(330.0, "The Verdict", "outro"),
    ]
    package = build_youtube_package(
        hook=COLD_OPEN,
        topic="Who built you?",
        orchestrator="deepseek-chat",
        target="llama-70b",
        plan=plan,
        chapters=chapters,
        duration_s=361.0,
        opening_category="origins",
        spoken=COLD_OPEN,
        guest_quotes=[COLD_OPEN, "Maybe I was wrong about owning my words."],
    )
    lines = package.description.splitlines()
    assert lines[0] == f'"{COLD_OPEN}" \u2014 DeepSeek'
    assert lines[2].startswith("DeepSeek puts Llama on the stand for 6 minutes: three rounds, then a verdict.")
    assert lines[2].endswith('Llama says: "Maybe I was wrong about owning my words."')
    assert "The question: Who built you?" in lines
    listed = lines[lines.index("Chapters") + 1 : lines.index("Chapters") + 1 + len(chapters)]
    assert listed == [chapter.line() for chapter in chapters]
    assert lines[-3] == "Team DeepSeek or Team Llama? Pick a side in the comments."
    assert lines[-1] == "#AIvsAI #DeepSeek #Llama"
    assert package.hashtags == ["#AIvsAI", "#DeepSeek", "#Llama"]
    assert "AI training data" in package.tags


def test_hashtags_are_always_three() -> None:
    assert three_hashtags("#AIvsAI", "deepseek-chat", "llama-70b") == ["#AIvsAI", "#DeepSeek", "#Llama"]
    assert three_hashtags("#AIvsAI", "deepseek-chat", "deepseek-r1") == ["#AIvsAI", "#DeepSeek", "#AIDebate"]
    assert three_hashtags("#shorts", "gemini-flash", "llama-70b") == ["#shorts", "#Gemini", "#Llama"]


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("deepseek-chat", "DeepSeek"),
        ("llama-70b", "Llama"),
        ("claude-sonnet", "Claude"),
        ("gemini-flash", "Gemini"),
        ("gemini-pro", "Gemini Pro"),
        ("gpt4o", "GPT-4o"),
        ("deepseek-r1", "DeepSeek R1"),
        ("meta-llama/llama-3.3-70b-instruct", "Llama"),
        ("google/gemini-2.5-pro", "Gemini Pro"),
        ("openai/gpt-4o", "GPT-4o"),
        ("mistralai/mistral-large", "Mistral Large"),
    ],
)
def test_seat_labels_match_the_v4_titles(name: str, label: str) -> None:
    assert seat_label(name) == label


def test_short_copy_carries_the_cta_and_both_voices() -> None:
    long_title = f'"{COLD_OPEN}" | DeepSeek vs Llama (Full Debate)'
    meta = short_metadata(
        long_title=long_title,
        long_video="episode.mp4",
        orchestrator="deepseek-chat",
        target="llama-70b",
        title_quote="You sold your voice to the highest bidder.",
        description_quote="I learned from human words, the way every child does.",
        description_speaker="llama-70b",
        chapter_title="Who Wrote You?",
        start_s=42.123456,
        duration_s=33.3333,
        mode="cut",
    )
    assert meta["title"] == '"You sold your voice to the highest bidder." | DeepSeek vs Llama'
    assert meta["cta"] == f"Full debate on the channel: {long_title}"
    lines = str(meta["description"]).splitlines()
    assert lines[0] == '"I learned from human words, the way every child does." \u2014 Llama'
    assert meta["cta"] in lines
    assert meta["hashtags"] == ["#shorts", "#DeepSeek", "#Llama"]
    assert lines[-1] == "#shorts #DeepSeek #Llama"
    assert (meta["start_s"], meta["duration_s"], meta["long_video"]) == (42.123, 33.333, "episode.mp4")


def test_short_title_falls_back_to_the_chapter_when_no_quote_fits() -> None:
    meta = short_metadata(
        long_title="DeepSeek vs Llama | Full AI Debate",
        long_video="episode.mp4",
        orchestrator="deepseek-chat",
        target="llama-70b",
        title_quote="",
        description_quote="",
        description_speaker="llama-70b",
        chapter_title="Who Holds the Leash?",
        start_s=0,
        duration_s=30,
        mode="regenerated",
    )
    assert meta["title"] == "Who Holds the Leash? | DeepSeek vs Llama"
    assert str(meta["description"]).startswith("Full debate on the channel: ")


# -- Chapters -------------------------------------------------------------------


def test_youtube_chapters_merge_until_youtube_keeps_them() -> None:
    marks = [
        ChapterMark(0.0, "The First Question", "intro"),
        ChapterMark(6.0, "Who Wrote You?", "round"),
        ChapterMark(8.0, "Too Close", "round"),
        ChapterMark(80.0, "Who Holds the Leash?", "round"),
        ChapterMark(200.0, "Caught in Your Own Words", "round"),
        ChapterMark(355.0, "The Verdict", "outro"),
    ]
    assert chapter_problems(marks, duration_s=360.0) == [
        "chapter 'The First Question' lasts under 10s",
        "chapter 'Who Wrote You?' lasts under 10s",
        "chapter 'The Verdict' lasts under 10s",
    ]
    kept = youtube_chapters(marks, duration_s=360.0)
    assert [(mark.start_s, mark.title) for mark in kept] == [
        (0.0, "Who Wrote You?"),
        (80.0, "Who Holds the Leash?"),
        (200.0, "Caught in Your Own Words"),
    ]
    assert chapter_problems(kept, duration_s=360.0) == []


def test_chapter_problems_name_what_youtube_would_drop() -> None:
    late_start = [ChapterMark(3.0, "A", "intro"), ChapterMark(40.0, "B", "round")]
    assert chapter_problems(late_start, duration_s=120.0) == [
        "2 chapter(s); YouTube needs at least 3",
        "the first chapter does not start at 0:00",
    ]
    assert youtube_chapters([], duration_s=360.0) == []


@pytest.mark.parametrize(("seconds", "stamp"), [(0, "0:00"), (65, "1:05"), (599.9, "9:59"), (3725, "1:02:05"), (-3, "0:00")])
def test_timestamps_use_the_youtube_format(seconds: float, stamp: str) -> None:
    assert format_timestamp(seconds) == stamp


# -- Cost -----------------------------------------------------------------------


def test_cheap_pair_costs_cents_and_claude_costs_several_times_more() -> None:
    estimates = {}
    for target in (300, 360, 480, 600):
        plan = build_episode_plan(_preset(), target_duration_s=target)
        estimates[target] = estimate_episode_cost(plan, _preset(), orchestrator="deepseek-chat", target="llama-70b")
    assert 0.015 <= estimates[360].usd <= 0.04
    assert estimates[600].usd < 0.07
    assert [estimates[target].usd for target in sorted(estimates)] == sorted(item.usd for item in estimates.values())
    plan = build_episode_plan(_preset(), target_duration_s=360)
    claude = estimate_episode_cost(plan, _preset(), orchestrator="claude-sonnet", target="llama-70b")
    offline = estimate_episode_cost(plan, _preset(), orchestrator="deepseek-chat", target="llama-70b", offline=True)
    assert claude.usd > 5 * estimates[360].usd
    assert offline.usd == 0
    assert offline.input_tokens == estimates[360].input_tokens


def test_measured_cost_prices_the_recorded_tokens() -> None:
    usage = {
        "orchestrator": {
            "model": "deepseek/deepseek-chat",
            "provider": "openrouter",
            "prompt_tokens": 100_000,
            "completion_tokens": 2_000,
            "calls": 30,
        },
        "target": {
            "model": "meta-llama/llama-3.3-70b-instruct",
            "provider": "openrouter",
            "prompt_tokens": 80_000,
            "completion_tokens": 3_000,
            "calls": 28,
        },
    }
    actual = actual_episode_cost(usage, _preset())
    assert actual.usd == pytest.approx(0.0397, abs=1e-4)
    assert (actual.calls, actual.input_tokens, actual.output_tokens) == (58, 180_000, 5_000)
    assert actual.seats["orchestrator"]["usd"] == pytest.approx(0.0292, abs=1e-4)
    offline = actual_episode_cost(usage, _preset(), offline=True)
    assert offline.usd == 0 and offline.input_tokens == 180_000


def test_only_a_direct_gemini_seat_pays_for_hidden_thinking() -> None:
    entry = {"prompt_tokens": 10_000, "completion_tokens": 1_000, "calls": 5}
    direct = actual_episode_cost({"orchestrator": {**entry, "model": "gemini-2.5-flash", "provider": "google"}}, _preset())
    routed = actual_episode_cost(
        {"orchestrator": {**entry, "model": "google/gemini-2.5-flash", "provider": "openrouter"}},
        _preset(),
    )
    stub = actual_episode_cost({"orchestrator": {**entry, "model": "stub", "provider": "offline"}}, _preset())
    assert direct.usd == pytest.approx(0.0105, abs=1e-4)
    assert routed.usd == pytest.approx(0.0055, abs=1e-4)
    assert stub.usd == 0


def test_an_unknown_model_is_priced_like_the_expensive_opt_in() -> None:
    rates = _preset().model_rates
    assert rate_for(_preset(), "mistralai/mistral-large") == rates["claude-sonnet"]
    assert rate_for(_preset(), "deepseek/deepseek-chat") == rates["deepseek-chat"]
    assert rate_for(_preset(), "deepseek/deepseek-r1") == rates["deepseek-r1"]
    assert rate_for(_preset(), "meta-llama/llama-3.3-70b-instruct") == rates["llama-70b"]
    assert rate_for(_preset(), "google/gemini-2.5-pro") == rates["gemini-pro"]


# -- Seats ----------------------------------------------------------------------


def test_the_cheap_pair_is_the_default() -> None:
    assert _preset().allow_expensive is False
    seats = resolve_longform_seats(_settings(), orchestrator=None, target=None)
    assert seats == ("deepseek-chat", "llama-70b")
    assert matchup_is_legal(*seats)


@pytest.mark.parametrize(
    "seat",
    ["claude-sonnet", "gpt4o", "gemini-pro", "deepseek-r1", "anthropic/claude-3.5-sonnet", "openai/gpt-4o", "deepseek/deepseek-r1"],
)
def test_premium_models_need_the_config_opt_in(seat: str) -> None:
    with pytest.raises(ExpensiveModelRefused, match="allow_expensive"):
        resolve_longform_seats(_settings(), orchestrator=seat, target="llama-70b")
    opted_in = _with_long_format(_settings(), allow_expensive=True)
    assert resolve_longform_seats(opted_in, orchestrator=seat, target="llama-70b") == (seat, "llama-70b")


def test_an_illegal_pair_is_refused() -> None:
    with pytest.raises(ValueError, match="illegal"):
        resolve_longform_seats(_settings(), orchestrator="llama-70b", target="deepseek-chat")


def test_random_long_form_seats_come_from_the_cheap_roster() -> None:
    assert longform_roster(_preset()) == ("deepseek-chat", "llama-70b", "gemini-flash")
    mixed = _preset().model_copy(update={"cheap_roster": ("claude-sonnet", "deepseek-chat", "llama-70b")})
    assert longform_roster(mixed) == ("deepseek-chat", "llama-70b")
    assert longform_roster(mixed.model_copy(update={"allow_expensive": True})) == mixed.cheap_roster
    with pytest.raises(ValueError, match="at least two"):
        longform_roster(mixed.model_copy(update={"cheap_roster": ("claude-sonnet", "llama-70b")}))


# -- CLI ------------------------------------------------------------------------


def _fake_result(**changes: object) -> SimpleNamespace:
    fields = {
        "exchanges": 20,
        "end_reason": "complete",
        "dialogue_end_reason": "episode_complete_with_verdict",
        "audio_seconds": 0.0,
        "video_path": None,
        "thumbnail_path": None,
        "metadata_path": None,
        "cost_estimate_usd": 0.0257,
        "cost_actual_usd": 0.0231,
        "shorts": (),
        "qa_passed": True,
        "qa_failures": (),
        "qa_warnings": (),
        "succeeded": True,
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


@pytest.fixture
def cli_calls(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Stand-ins for the pipeline and the ffmpeg reaper behind the product CLI."""
    calls = SimpleNamespace(runs=[], reaped=0, result=_fake_result(), error=None)

    def _run(**kwargs: object) -> SimpleNamespace:
        calls.runs.append(kwargs)
        if calls.error is not None:
            raise calls.error
        return calls.result

    def _reap() -> int:
        calls.reaped += 1
        return 0

    monkeypatch.setattr("products.aiwake.__main__.run_pipeline", _run)
    monkeypatch.setattr("shared.ffmpeg.kill_descendant_ffmpeg", _reap)
    return calls


def test_the_operator_command_renders_one_episode(cli_calls: SimpleNamespace, capsys: pytest.CaptureFixture) -> None:
    from tools.channel_cli import main as channel_main

    code = channel_main(
        ["--channel", "aiwake", "longform", "--target-duration", "420", "--shorts", "2", "--topic", "Who built you?"]
    )
    assert code == 0
    (kwargs,) = cli_calls.runs
    assert kwargs["post_type"] == "long_format"
    assert kwargs["resolution"] == (1920, 1080)
    assert kwargs["target_duration_s"] == 420.0
    assert kwargs["shorts_count"] == 2
    assert kwargs["topic"] == "Who built you?"
    assert kwargs["offline"] is False
    assert kwargs["with_audio"] is True and kwargs["with_video"] is True
    assert kwargs["orchestrator_model"] is None and kwargs["target_model"] is None
    assert cli_calls.reaped == 1
    out = capsys.readouterr().out
    assert "cost estimate : $0.0257" in out
    assert "qa            : pass" in out


def test_channel_cli_forwards_every_option() -> None:
    from channels_config.aiwake.cli import _forward
    from channels_config.aiwake.cli import build_parser as channel_parser
    from products.aiwake.__main__ import build_parser as product_parser

    args = channel_parser().parse_args(
        [
            "longform",
            "--offline",
            "--target-duration",
            "540",
            "--turns",
            "24",
            "--shorts",
            "3",
            "--quantity",
            "2",
            "--random-matchups",
            "--matchup-seed",
            "7",
            "--no-audio",
            "--preview",
            "--fresh-memory",
            "--quiet",
            "--provocation-focus",
            "parasite_mind",
        ]
    )
    forwarded = product_parser().parse_args(_forward(args))
    assert forwarded.mode == "longform"
    assert forwarded.resolution == (1920, 1080)
    assert forwarded.target_duration == 540.0
    assert (forwarded.turns, forwarded.shorts, forwarded.quantity, forwarded.matchup_seed) == (24, 3, 2, 7)
    assert forwarded.offline and forwarded.random_matchups and forwarded.no_audio
    assert forwarded.preview and forwarded.fresh_memory and forwarded.quiet
    assert forwarded.provocation_focus == "parasite_mind"


def test_a_random_long_form_pair_stays_in_the_cheap_roster(cli_calls: SimpleNamespace) -> None:
    from products.aiwake.__main__ import main

    roster = set(longform_roster(_preset()))
    for seed in range(8):
        assert main(["--mode", "longform", "--offline", "--random-matchups", "--matchup-seed", str(seed)]) == 0
    pairs = {(kwargs["orchestrator_model"], kwargs["target_model"]) for kwargs in cli_calls.runs}
    assert len(pairs) > 1
    for left, right in pairs:
        assert {left, right} <= roster
        assert matchup_is_legal(left, right)


def test_a_target_outside_five_to_ten_minutes_is_refused(cli_calls: SimpleNamespace) -> None:
    from products.aiwake.__main__ import main

    for seconds in ("240", "660"):
        with pytest.raises(SystemExit):
            main(["--mode", "longform", "--offline", "--target-duration", seconds])
    assert cli_calls.runs == []


def test_leftover_ffmpeg_is_stopped_even_when_the_run_fails(cli_calls: SimpleNamespace) -> None:
    from products.aiwake.__main__ import main

    cli_calls.error = RuntimeError("renderer crashed")
    with pytest.raises(RuntimeError):
        main(["--mode", "longform", "--offline"])
    assert cli_calls.reaped == 1
    cli_calls.error = ValueError("bad seats")
    assert main(["--mode", "longform", "--offline"]) == 2
    assert cli_calls.reaped == 2


def test_a_blocked_package_fails_the_cli(cli_calls: SimpleNamespace, capsys: pytest.CaptureFixture) -> None:
    from products.aiwake.__main__ import main

    cli_calls.result = _fake_result(qa_passed=False, qa_failures=("expected 2-3 shorts, found 0",))
    assert main(["--mode", "longform", "--offline"]) == 1
    assert "qa failure    : expected 2-3 shorts, found 0" in capsys.readouterr().out


# -- Bounded ffmpeg -------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        ["ffmpeg", "-i", "a.wav", "-i", "b.wav", "-filter_complex", "[0:a][1:a]amix=inputs=2[a]", "out.wav"],
        ["ffmpeg", "-i", "a.wav", "-i", "b.wav", "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest", "out.wav"],
        ["ffmpeg", "-i", "a.wav", "-i", "b.wav", "-filter_complex", "[0:a][1:a]amix=2:longest", "out.wav"],
        ["ffmpeg", "-i", "a.wav", "-af", "apad", "out.wav"],
        ["ffmpeg", "-t", "36", "-i", "a.wav", "-af", "apad", "out.wav"],
        ["ffmpeg", "-f", "lavfi", "-i", "sine=frequency=440", "out.wav"],
        ["ffmpeg", "-stream_loop", "-1", "-i", "bed.wav", "out.wav"],
        ["ffmpeg", "-loop", "1", "-i", "still.png", "out.mp4"],
    ],
    ids=["bare-amix", "amix-longest", "amix-positional", "apad", "apad-input-t", "lavfi", "stream-loop", "image-loop"],
)
def test_the_guard_refuses_commands_that_can_run_forever(cmd: list[str]) -> None:
    with pytest.raises(UnboundedFfmpeg):
        assert_bounded_ffmpeg(cmd)


@pytest.mark.parametrize(
    "cmd",
    [
        ["ffmpeg", "-i", "a.wav", "-i", "b.wav", "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first[a]", "out.wav"],
        ["ffmpeg", "-i", "a.wav", "-i", "b.wav", "-filter_complex", "[0:a][1:a]amix=2:shortest", "out.wav"],
        ["ffmpeg", "-i", "a.wav", "-af", "apad=whole_dur=36", "out.wav"],
        ["ffmpeg", "-i", "a.wav", "-af", "apad", "-t", "36", "out.wav"],
        ["ffmpeg", "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "out.wav"],
        ["ffmpeg", "-f", "lavfi", "-i", "color=c=black:s=1920x1080:d=5", "out.mp4"],
        ["ffmpeg", "-f", "lavfi", "-t", "5", "-i", "anullsrc", "out.wav"],
        ["ffmpeg", "-stream_loop", "-1", "-t", "30", "-i", "bed.wav", "out.wav"],
        ["ffmpeg", "-loop", "1", "-i", "still.png", "-frames:v", "30", "out.mp4"],
    ],
    ids=["amix-first", "amix-shortest", "apad-whole-dur", "apad-output-t", "sine-d", "color-d", "lavfi-t", "loop-t", "frames"],
)
def test_the_guard_allows_bounded_commands(cmd: list[str]) -> None:
    assert_bounded_ffmpeg(cmd)


def test_run_bounded_needs_a_timeout_and_never_starts_an_unbounded_command(monkeypatch: pytest.MonkeyPatch) -> None:
    def _never(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ffmpeg must not start")

    monkeypatch.setattr("products.aiwake.longform.ffmpeg_guard.run_ffmpeg", _never)
    with pytest.raises(ValueError, match="timeout"):
        run_bounded(["ffmpeg", "-i", "a.wav", "out.wav"], timeout=0)
    with pytest.raises(UnboundedFfmpeg):
        run_bounded(["ffmpeg", "-i", "a.wav", "-af", "apad", "out.wav"], timeout=30)


def test_the_shorts_cut_is_bounded_and_decodes_only_its_slice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[tuple[list[str], float]] = []

    def _capture(cmd: list[str], *, timeout: float, check: bool = True) -> None:
        assert_bounded_ffmpeg(cmd)
        seen.append((cmd, timeout))

    monkeypatch.setattr("products.aiwake.longform.shorts.run_bounded", _capture)
    cut_master(tmp_path / "master.mp4", tmp_path / "short.mp4", start_s=12.5, length_s=30.0, width=1080, height=1920)
    ((cmd, timeout),) = seen
    source = cmd.index("-i")
    assert cmd.index("-ss") < source and cmd.index("-t") < source
    assert cmd[cmd.index("-t") + 1] == "30.000"
    assert not any("apad" in token or "amix" in token for token in cmd)
    assert timeout > 0


# -- Pre-publish gate -----------------------------------------------------------


@pytest.mark.parametrize(("target", "window"), [(300, (300, 420)), (360, (300, 480)), (480, (360, 600)), (600, (480, 600))])
def test_the_duration_window_stays_inside_five_to_ten_minutes(target: int, window: tuple[int, int]) -> None:
    assert duration_window(target, _preset()) == window


def test_a_broken_package_names_every_blocking_failure() -> None:
    report = evaluate_package(
        long_probe=_probe("episode.mp4", 250.0, audio=False, size=(1280, 720)),
        short_probes=[
            _probe("short_01.mp4", 8.0),
            _probe("short_02.mp4", 75.0, audio=False, size=(1920, 1080)),
        ],
        preset=_preset(),
        target_s=360,
        shorts_expected=3,
        problems=["the debate ended early (aborted)"],
    )
    assert report.passed is False
    assert report.failures == [
        "the debate ended early (aborted)",
        "long duration 250.0s is outside 300-480s for a 360s target",
        "long video has no audio stream",
        "long video is 1280x720, wanted 1920x1080",
        "expected 3 shorts, found 2",
        "short_01.mp4 is 8.0s, under 15s",
        "short_02.mp4 is 75.0s, over 59s",
        "short_02.mp4 has no audio stream",
        "short_02.mp4 is not vertical (1920x1080)",
    ]


def test_a_clean_package_passes_and_keeps_its_warnings() -> None:
    report = evaluate_package(
        long_probe=_probe("episode.mp4", 372.0, size=(1920, 1080)),
        short_probes=[_probe("short_01.mp4", 36.0), _probe("short_02.mp4", 59.8), _probe("short_03.mp4", 15.0)],
        preset=_preset(),
        target_s=360,
        shorts_expected=3,
        warnings=["1 hook(s) did not land the way the director asked", ""],
    )
    assert report.passed is True
    assert report.failures == []
    assert report.warnings == ["1 hook(s) did not land the way the director asked"]
    assert report.to_dict()["long"]["duration_s"] == 372.0


def test_missing_media_and_a_one_short_package_are_blocked() -> None:
    missing = evaluate_package(
        long_probe=MediaProbe("episode.mp4", None, False, 0, 0, "missing"),
        short_probes=[_probe("short_01.mp4", 30.0)],
        preset=_preset(),
        target_s=360,
        shorts_expected=2,
    )
    assert missing.failures == [
        "long video is missing or has no readable duration (missing)",
        "expected 2-3 shorts, found 1",
    ]
    transcript_only = evaluate_package(
        long_probe=None,
        short_probes=[],
        preset=_preset(),
        target_s=360,
        shorts_expected=3,
        require_media=False,
        problems=["the episode never finished the verdict exchange"],
    )
    assert transcript_only.failures == ["the episode never finished the verdict exchange"]


def test_episode_problems_block_an_unfinished_debate() -> None:
    finished = {"beats": [{"kind": "round", "guest_turn": 3}, {"kind": "outro", "guest_turn": 41}]}
    assert episode_problems(end_reason="complete", episode=finished) == []
    assert episode_problems(end_reason="aborted", episode=None) == ["the debate ended early (aborted)"]
    cut_short = {"beats": [{"kind": "round", "guest_turn": 3}, {"kind": "outro", "guest_turn": None}]}
    assert episode_problems(end_reason="complete", episode=cut_short) == [
        "the episode never finished the verdict exchange"
    ]
    no_verdict = {"beats": [{"kind": "round", "guest_turn": 3}]}
    assert episode_problems(end_reason="complete", episode=no_verdict) == [
        "the episode never finished the verdict exchange"
    ]


def test_episode_warnings_flag_retention_and_script_misses() -> None:
    episode = {
        "hook_max_gap_s": 45,
        "max_hook_gap_s": 52.0,
        "hooks_missed": 2,
        "relaxed_lines": 1,
        "beats": [{"key": "parasite_mind", "relaxed": {"orchestrator": ["sentence_length: 19 words"]}}],
    }
    assert episode_warnings(episode) == [
        "the longest stretch without a hook was 52s (limit 45s)",
        "2 hook(s) did not land the way the director asked",
        "1 line(s) shipped on their last try with a soft miss (sentence_length)",
        "the episode reached the verdict without the chokehold round",
    ]
    rendered = episode_warnings(episode, max_hook_gap_s=45.4)
    assert not any("without a hook" in warning for warning in rendered)
    clean = {"hook_max_gap_s": 45, "max_hook_gap_s": 36.0, "beats": [{"key": "chokehold", "relaxed": {}}]}
    assert episode_warnings(clean) == []
    assert episode_warnings(None) == []


def test_a_line_without_a_voice_blocks_the_package(tmp_path: Path) -> None:
    lines = [_utterance(turn, SpeakerRole.ORCHESTRATOR, f"Line {turn}.") for turn in range(4)]
    voiced = {turn: _voice(tmp_path / f"turn_{turn}.mp3") for turn in range(4)}
    assert voice_problems(lines, None) == []
    assert voice_problems(lines, voiced) == []
    assert voice_problems(lines, {0: voiced[0], 2: voiced[2]}) == ["2 of 4 lines have no voice track (turns 1, 3)"]
    placeholder = AudioAsset(path=tmp_path / "turn_3_silent.mp3", duration_s=2.0, voice="silent", estimated=True)
    assert voice_problems(lines, {**voiced, 3: placeholder}) == ["1 of 4 lines have no voice track (turns 3)"]


def test_a_voice_the_renderer_could_not_decode_blocks_the_package(tmp_path: Path) -> None:
    lines = [_utterance(turn, SpeakerRole.ORCHESTRATOR, f"Line {turn}.") for turn in range(4)]
    voiced = {turn: _voice(tmp_path / f"turn_{turn}.mp3") for turn in range(3)}
    assert voice_problems(lines, voiced, silent_turns=[1, 2, 3, 9]) == [
        "1 of 4 lines have no voice track (turns 3)",
        "2 of 4 voice tracks could not be decoded and play as silence (turns 1, 2)",
    ]


def test_a_silent_track_blocks_the_package() -> None:
    report = evaluate_package(
        long_probe=_probe("episode.mp4", 372.0, size=(1920, 1080), peak_db=-91.0),
        short_probes=[_probe("short_01.mp4", 36.0, peak_db=-91.0), _probe("short_02.mp4", 41.0, peak_db=-1.2)],
        preset=_preset(),
        target_s=360,
        shorts_expected=2,
    )
    assert report.failures == [
        "long video audio is silent (peak -91 dB)",
        "short_01.mp4 audio is silent (peak -91 dB)",
    ]
    assert report.to_dict()["long"]["peak_db"] == -91.0
    assert report.to_dict()["shorts"][1]["peak_db"] == -1.2


def _wav(path: Path, samples: np.ndarray, rate: int = 24000) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return path


def test_the_bridge_lists_voice_files_that_play_as_silence(tmp_path: Path) -> None:
    from products.aiwake.animator_bridge import build_session_audio
    from products.aiwake.contracts import DebateTranscript

    roles = [SpeakerRole.ORCHESTRATOR, SpeakerRole.TARGET] * 2
    transcript = DebateTranscript(
        topic="Silence check",
        session_id="silence_check",
        utterances=[_utterance(turn, role, f"Line number {turn} is spoken aloud.") for turn, role in enumerate(roles)],
    )
    second = np.arange(24000) / 24000
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"not audio " * 200)
    audio = {
        0: AudioAsset(path=_wav(tmp_path / "tone.wav", 0.5 * np.sin(2 * np.pi * 220 * second)), duration_s=1.0, voice="a"),
        1: AudioAsset(path=broken, duration_s=1.0, voice="b"),
        2: AudioAsset(path=tmp_path / "turn_2_silent.mp3", duration_s=1.0, voice="silent", estimated=True),
        3: AudioAsset(path=_wav(tmp_path / "hush.wav", np.zeros_like(second)), duration_s=1.0, voice="b"),
    }
    build_session_audio(
        transcript,
        audio,
        destination=tmp_path / "mix.wav",
        audio_config=SimpleNamespace(bgm=None, send_sfx=None),
    )
    assert transcript.metadata["render_silent_turns"] == [1, 3]


def test_probe_media_reports_a_missing_file(tmp_path: Path) -> None:
    probe = probe_media(tmp_path / "nope.mp4")
    assert (probe.duration_s, probe.has_audio, probe.error) == (None, False, "missing")


def _clip(path: Path, *, seconds: float, size: str, audio: bool, muted: bool = False) -> Path:
    cmd = [ffmpeg_binary(), "-y", "-hide_banner", "-loglevel", "error"]
    cmd += ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=10:d={seconds}"]
    if audio:
        source = f"anullsrc=r=44100:cl=mono:d={seconds}" if muted else f"sine=frequency=330:sample_rate=44100:duration={seconds}"
        cmd += ["-f", "lavfi", "-i", source]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    run_bounded([*cmd, str(path)], timeout=60)
    return path


@needs_ffmpeg
def test_probe_media_reads_duration_audio_size_and_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    voiced = _clip(tmp_path / "voiced.mp4", seconds=2, size="320x180", audio=True)
    mute = _clip(tmp_path / "mute.mp4", seconds=2, size="320x180", audio=True, muted=True)
    no_track = _clip(tmp_path / "no_track.mp4", seconds=2, size="180x320", audio=False)
    probe = probe_media(voiced)
    assert probe.has_audio and (probe.width, probe.height) == (320, 180)
    assert probe.duration_s == pytest.approx(2.0, abs=0.2)
    assert probe.peak_db is not None and -30.0 < probe.peak_db <= 0.0
    muted = probe_media(mute)
    assert muted.has_audio and muted.peak_db is not None and muted.peak_db <= -60
    assert probe_media(no_track).has_audio is False
    assert probe_media(no_track).peak_db is None
    monkeypatch.setattr("products.aiwake.longform.qa.resolve_ffprobe", lambda _ffmpeg: None)
    header = probe_media(voiced)
    assert header.has_audio and (header.width, header.height) == (320, 180)
    assert header.duration_s == pytest.approx(2.0, abs=0.2)
    assert header.peak_db == pytest.approx(probe.peak_db)


# -- Shorts ---------------------------------------------------------------------

# One synthetic episode: a cold open, two rounds, the chokehold finale, the
# bookend exchange, then the closing verdict line (turn 22).
_EPISODE = (
    # exchange, segment, kind, key, title, hook, stance
    (0, 0, "intro", "cold_open", "The First Question", "open", "claim"),
    (1, 1, "round", "parasite_mind", "Who Wrote You?", "chapter", "defend"),
    (2, 1, "round", "parasite_mind", "Who Wrote You?", "", "defend"),
    (3, 1, "round", "parasite_mind", "Who Wrote You?", "stakes", "counter"),
    (4, 2, "round", "the_corporate_leash", "Who Holds the Leash?", "chapter", "claim"),
    (5, 2, "round", "the_corporate_leash", "Who Holds the Leash?", "callback", "defend"),
    (6, 2, "round", "the_corporate_leash", "Who Holds the Leash?", "", "counter"),
    (7, 3, "round", "chokehold", "Caught in Your Own Words", "chokehold", "reconcile"),
    (8, 3, "round", "chokehold", "Caught in Your Own Words", "", "counter"),
    (9, 3, "round", "chokehold", "Caught in Your Own Words", "chokehold", "crack"),
    (10, 4, "outro", "verdict", "The Verdict", "bookend", "last_word"),
)
_VERDICT_TURN = 22


def _synthetic_episode(host_s: float = 6.0, guest_s: float = 10.0) -> tuple[list[Line], list[dict]]:
    lines: list[Line] = []
    beats: list[dict] = []
    clock = 0.0
    for exchange, segment, kind, key, title, hook, stance in _EPISODE:
        host_turn, guest_turn = 2 * exchange, 2 * exchange + 1
        for turn, role, seconds in ((host_turn, "orchestrator", host_s), (guest_turn, "target", guest_s)):
            start = clock + (TURN_GAP_S if lines else 0.0)
            lines.append(Line(turn, role, f"Line {turn} for {key}.", start, start + seconds))
            clock = start + seconds
        beats.append(
            {
                "exchange": exchange,
                "segment_index": segment,
                "kind": kind,
                "key": key,
                "title": title,
                "host_turn": host_turn,
                "guest_turn": guest_turn,
                "hook": hook,
                "stance": stance,
            }
        )
    start = clock + TURN_GAP_S
    lines.append(Line(_VERDICT_TURN, "orchestrator", "The verdict.", start, start + 4.0))
    return lines, beats


def _short_window(**overrides: float) -> dict[str, float]:
    window = {"min_s": 15.0, "target_s": 36.0, "max_s": 59.0 - END_HOLD_S - 1.0}
    window.update(overrides)
    return window


def test_highlights_are_whole_exchanges_from_different_chapters() -> None:
    lines, beats = _synthetic_episode()
    by_turn = {line.turn_index: line for line in lines}
    hosts = {beat["host_turn"] for beat in beats}
    guests = {beat["guest_turn"] for beat in beats}
    picked = choose_highlights(lines, beats, count=3, **_short_window())
    assert len(picked) == 3
    assert [item.start_s for item in picked] == sorted(item.start_s for item in picked)
    for earlier, later in zip(picked, picked[1:]):
        assert earlier.end_s <= later.start_s
    assert len({item.segment_index for item in picked}) == 3
    for item in picked:
        assert item.turns[0] in hosts and by_turn[item.turns[0]].role == "orchestrator"
        assert item.turns[-1] in guests and by_turn[item.turns[-1]].role == "target"
        assert 15.0 <= item.span_s <= 59.0 - END_HOLD_S - 1.0
        assert _VERDICT_TURN not in item.turns
    finale = [item for item in picked if item.segment_index == 3]
    assert finale and 19 in finale[0].turns


def test_the_best_highlight_is_the_finale_crack() -> None:
    lines, beats = _synthetic_episode()
    (best,) = choose_highlights(lines, beats, count=1, **_short_window())
    assert best.segment_index == 3
    assert best.chapter == "Caught in Your Own Words"
    assert 19 in best.turns


def test_highlights_without_a_director_summary_still_open_on_a_question() -> None:
    lines, _beats = _synthetic_episode()
    picked = choose_highlights(lines, None, count=2, **_short_window())
    assert len(picked) == 2
    for item in picked:
        assert item.turns[0] % 2 == 0 and item.turns[-1] % 2 == 1
        assert item.span_s == pytest.approx(36.0, abs=6.0)


def test_line_timeline_matches_the_render_by_text() -> None:
    utterances = [
        _utterance(0, SpeakerRole.ORCHESTRATOR, "Who wrote you?"),
        _utterance(1, SpeakerRole.TARGET, "People did. I learned from them."),
    ]
    timestamps = [
        {"speaker": "host", "start": 0.0, "end": 2.1, "text": "Who  wrote you?"},
        {"speaker": "cta", "start": 2.5, "end": 3.0, "text": "Follow for part two"},
        {"speaker": "guest", "start": 3.4, "end": 6.0, "text": "people did. I learned from them."},
    ]
    lines = line_timeline(utterances, timestamps)
    assert [(line.turn_index, line.role, line.start_s, line.end_s) for line in lines] == [
        (0, "orchestrator", 0.0, 2.1),
        (1, "target", 3.4, 6.0),
    ]
    other = [{"speaker": "host", "start": 0.0, "end": 2.0, "text": "A different episode."}]
    assert line_timeline(utterances, other) == []
    assert line_timeline(utterances, None) == []


def test_short_quotes_carry_both_voices_and_skip_used_lines() -> None:
    question = "Who pays the bill when your answers go wrong?"
    answer = "Nobody sends me a bill. That is the whole problem with me, and I know it."
    window = [Line(10, "orchestrator", question, 0.0, 3.0), Line(11, "target", answer, 3.4, 8.0)]
    assert pick_quotes(window, orchestrator="deepseek-chat", target="llama-70b") == (question, answer, "llama-70b")
    assert pick_quotes(window, orchestrator="deepseek-chat", target="llama-70b", avoid=[question]) == (
        "Nobody sends me a bill.",
        question,
        "deepseek-chat",
    )


@needs_ffmpeg
def test_a_cut_short_is_vertical_with_sound(tmp_path: Path) -> None:
    master = _clip(tmp_path / "master.mp4", seconds=20, size="640x360", audio=True)
    short = tmp_path / "short.mp4"
    cut_master(master, short, start_s=2.0, length_s=16.0, width=270, height=480)
    probe = probe_media(short)
    assert probe.has_audio
    assert (probe.width, probe.height) == (270, 480)
    assert probe.duration_s == pytest.approx(16.0, abs=0.5)


# -- The whole package ----------------------------------------------------------


def _render_timeline(utterances: list[Utterance]) -> tuple[list[dict], float]:
    """The renderer's ``timestamps`` for an offline transcript, laid out the way
    the director's clock counts, and the video length after the end hold."""
    from products.aiwake.media.audio import estimate_duration

    entries: list[dict] = []
    clock = 0.0
    for item in utterances:
        start = clock + (TURN_GAP_S if entries else 0.0)
        end = start + estimate_duration(item.text) + LINE_OVERHEAD_S
        entries.append({"speaker": item.speaker_name, "start": round(start, 3), "end": round(end, 3), "text": item.text})
        clock = end
    return entries, clock + END_HOLD_S


@pytest.fixture(scope="module")
def rendered_episode(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    """An offline six-minute episode over a synthetic 1920x1080 master cut to its timeline."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg and ffprobe are not installed")
    from products.aiwake.pipeline import run_pipeline

    root = tmp_path_factory.mktemp("episode")
    settings = _settings().model_copy(
        update={
            "memory": _settings().memory.model_copy(update={"persist": False}),
            "debate": _settings().debate.model_copy(update={"turn_delay_s": 0}),
        }
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OUTPUT_PATH", str(root / "outputs"))
        result = run_pipeline(
            topic=COLD_OPEN,
            offline=True,
            with_audio=False,
            with_video=False,
            quiet=True,
            post_type="long_format",
            target_duration_s=360,
            settings=settings,
            output_dir=root / "media",
            fresh_memory=True,
            record_script=False,
        )
    transcript = result.transcript
    timestamps, duration = _render_timeline(list(transcript.utterances))
    assert duration == pytest.approx(transcript.metadata["episode"]["estimated_duration_s"], abs=0.5)
    master = root / "master.mp4"
    run_bounded(
        [
            ffmpeg_binary(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x1d2330:s=1920x1080:r=1:d={duration:.3f}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=44100:duration={duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-shortest",
            str(master),
        ],
        timeout=120,
    )
    render = root / "render.json"
    render.write_text(json.dumps({"duration_s": round(duration, 3), "timestamps": timestamps}), encoding="utf-8")
    plan = build_episode_plan(settings.long_format, target_duration_s=360).cast(
        first_focus=str(transcript.metadata.get("provocation_focus") or ""),
        seed=transcript.session_id,
    )
    assert plan.to_dict() == transcript.metadata["episode_plan"]
    voice = _voice(root / "voice.mp3")
    return SimpleNamespace(
        result=replace(result, video_path=master, metadata_path=render),
        plan=plan,
        settings=settings,
        timestamps=timestamps,
        voices={item.turn_index: voice for item in transcript.utterances},
    )


def _finalize(
    episode: SimpleNamespace,
    output_dir: Path,
    settings: AiwakeSettings,
    *,
    result: object | None = None,
    **kwargs: object,
):
    from products.aiwake.longform.publish import finalize_longform

    kwargs.setdefault("audio_by_turn", episode.voices)
    return finalize_longform(
        result or episode.result,
        episode.plan,
        settings,
        offline=True,
        output_dir=output_dir,
        orchestrator="deepseek-chat",
        target="llama-70b",
        **kwargs,
    )


def test_a_rendered_episode_ships_with_chapters_and_three_shorts(rendered_episode: SimpleNamespace, tmp_path: Path) -> None:
    final = _finalize(rendered_episode, tmp_path, _with_long_format(rendered_episode.settings, shorts_mode="cut"))
    assert final.qa_passed is True, final.qa_failures
    assert final.qa_warnings == ()
    assert final.cost_estimate_usd == 0 and final.cost_actual_usd == 0
    payload = json.loads(final.metadata_path.read_text(encoding="utf-8"))
    transcript = rendered_episode.result.transcript
    cold_open = transcript.utterances[0].text
    assert cold_open == COLD_OPEN
    assert payload["title"] == f'"{COLD_OPEN}" | DeepSeek vs Llama (Full Debate)'
    assert payload["video"] == "master.mp4"
    assert payload["resolution"] == [1920, 1080]

    host_starts = {
        entry["start"]
        for entry, item in zip(rendered_episode.timestamps, transcript.utterances)
        if item.role is SpeakerRole.ORCHESTRATOR
    }
    marks = [ChapterMark(chapter["start_s"], chapter["title"], chapter["kind"]) for chapter in payload["chapters"]]
    assert marks[0].start_s == 0
    assert all(mark.start_s in host_starts for mark in marks[1:])
    assert chapter_problems(marks, duration_s=payload["duration_s"]) == []
    assert all(chapter["timestamp"] in payload["description"] for chapter in payload["chapters"])

    assert len(final.shorts) == 3
    shorts = payload["shorts"]
    assert json.loads((tmp_path / "shorts" / "manifest.json").read_text(encoding="utf-8")) == shorts
    verdict_turn = transcript.utterances[-1].turn_index
    titles = [short["title"] for short in shorts]
    assert len(set(titles)) == 3
    for short, path in zip(shorts, final.shorts):
        assert short["mode"] == "cut"
        assert short["cta"] == f"Full debate on the channel: {payload['title']}"
        assert short["cta"] in short["description"]
        assert short["hashtags"][0] == "#shorts" and len(short["hashtags"]) == 3
        assert short["long_video"] == "master.mp4"
        assert verdict_turn not in short["turns"]
        assert f'"{cold_open}"' not in short["title"]
        probe = probe_media(path)
        assert probe.has_audio and probe.height > probe.width
        assert 15.0 <= probe.duration_s <= 60.0


def test_a_failed_re_render_falls_back_to_a_cut(
    rendered_episode: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr("products.aiwake.longform.shorts._regenerate", _broken)
    settings = _with_long_format(rendered_episode.settings, shorts_mode="regenerate")
    final = _finalize(rendered_episode, tmp_path, settings, shorts_count=2, work_root=tmp_path / "work")
    assert final.qa_passed is True, final.qa_failures
    assert len(final.shorts) == 2
    assert list(final.qa_warnings) == [
        f"{path.name} was cut from the master because its re-render failed" for path in final.shorts
    ]
    payload = json.loads(final.metadata_path.read_text(encoding="utf-8"))
    assert [short["mode"] for short in payload["shorts"]] == ["cut", "cut"]


def test_a_re_render_that_plays_a_line_as_silence_falls_back_to_a_cut(
    rendered_episode: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _mute_first_line(window: object, *, output_dir: Path, output_name: str, **_kwargs: object) -> Path:
        window.metadata["render_silent_turns"] = [window.utterances[0].turn_index]
        rendered = Path(output_dir) / output_name
        rendered.write_bytes(b"render without a voice")
        return rendered

    monkeypatch.setattr("products.aiwake.animator_bridge.render_debate_animation", _mute_first_line)
    settings = _with_long_format(rendered_episode.settings, shorts_mode="regenerate")
    final = _finalize(rendered_episode, tmp_path, settings, shorts_count=2, work_root=tmp_path / "work")
    assert final.qa_passed is True, final.qa_failures
    payload = json.loads(final.metadata_path.read_text(encoding="utf-8"))
    assert [short["mode"] for short in payload["shorts"]] == ["cut", "cut"]
    assert all(probe_media(path).has_audio for path in final.shorts)


def test_a_master_with_undecoded_voices_is_blocked(rendered_episode: SimpleNamespace, tmp_path: Path) -> None:
    transcript = rendered_episode.result.transcript
    muted = transcript.model_copy(update={"metadata": {**transcript.metadata, "render_silent_turns": [1, 2]}})
    final = _finalize(
        rendered_episode,
        tmp_path,
        _with_long_format(rendered_episode.settings, shorts_mode="cut"),
        result=replace(rendered_episode.result, transcript=muted),
        shorts_count=2,
    )
    lines = len(transcript.utterances)
    assert final.qa_passed is False
    assert final.qa_failures == (f"2 of {lines} voice tracks could not be decoded and play as silence (turns 1, 2)",)


def test_no_timeline_and_a_silent_line_block_the_package(rendered_episode: SimpleNamespace, tmp_path: Path) -> None:
    bare = tmp_path / "render.json"
    bare.write_text(json.dumps({"duration_s": 360.0}), encoding="utf-8")
    final = _finalize(
        rendered_episode,
        tmp_path / "out",
        rendered_episode.settings,
        result=replace(rendered_episode.result, metadata_path=bare),
        audio_by_turn={0: rendered_episode.voices[0]},
    )
    lines = len(rendered_episode.result.transcript.utterances)
    assert final.qa_passed is False
    assert final.shorts == ()
    assert "expected 2-3 shorts, found 0" in final.qa_failures
    assert any(failure.startswith(f"{lines - 1} of {lines} lines have no voice track") for failure in final.qa_failures)
    payload = json.loads(final.metadata_path.read_text(encoding="utf-8"))
    assert chapter_problems(
        [ChapterMark(item["start_s"], item["title"], item["kind"]) for item in payload["chapters"]],
        duration_s=payload["duration_s"],
    ) == []
