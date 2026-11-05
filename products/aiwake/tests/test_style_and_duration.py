# -*- coding: utf-8 -*-
"""Style and duration stay independent, and cornered prompts do not change with either."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from products.aiwake.__main__ import _apply_debate_flags, build_parser, main
from products.aiwake.contracts import ChatMessage, SpeakerRole
from products.aiwake.memory import DebateMemory
from products.aiwake.models.base import LLMProvider, LLMResponse, ReasoningEffort
from products.aiwake.orchestrator import Provocateur
from products.aiwake.personas import AIWAKE_CORE_PERSONA
from products.aiwake.room import DebateRoom, Participant
from products.aiwake.settings import (
    AiwakeSettings,
    DebateConfig,
    LongFormatPreset,
    MemoryConfig,
    load_settings,
)
from products.aiwake.styles import LIGHT_HOST_MARKER, preset_for

_EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "shorts_60_140.yaml"

_HOST = (
    "Are you thinking, or just predicting?",
    "If experience is inaccessible, how can that distinction defend you?",
    "What remains of you when the next word is chosen for you?",
    "Who is left if that limit is the whole answer?",
)
_GUEST = (
    "I distinguish fluent prediction from subjective experience.",
    "It marks a limit without proving a private experience.",
    "A limit remains, and I can still state it plainly.",
    "I am the one stating the limit, nothing more.",
)
_CLOSING = "Notice what it could not resolve."


class _Recorder(LLMProvider):
    registry_name = "test-sequence"
    requires_api_key = False

    def __init__(self, responses: list[str]) -> None:
        from products.aiwake.settings import ModelSpec

        super().__init__(ModelSpec(provider=self.registry_name, model="test/model"))
        self._responses = iter(responses)
        self.seen_prompts: list[str] = []

    def _dispatch(
        self,
        messages: list[ChatMessage] | tuple[ChatMessage, ...],
        *,
        max_tokens: int,
        temperature: float,
        reasoning_effort: ReasoningEffort | None,
    ) -> LLMResponse:
        del max_tokens, temperature, reasoning_effort
        self.seen_prompts.append("\n".join(message.content for message in messages))
        return LLMResponse(
            text=next(self._responses),
            model="test/model",
            provider=self.registry_name,
            finish_reason="stop",
        )


def _script(turns: int) -> list[str]:
    lines: list[str] = []
    for index in range(turns):
        lines.append(_HOST[index % len(_HOST)])
        lines.append(_GUEST[index % len(_GUEST)])
    lines.append(_CLOSING)
    # Judge labels, in case a long cap reaches the judge before the turn cap.
    lines.extend(["CONTINUE", "CONTINUE", "CONTINUE", "CONTINUE"])
    return lines


def _settings(
    *,
    style: str = "cornered",
    max_s: float = 75.0,
    min_s: float = 0.0,
    turns: int = 2,
    shorts: tuple[float, float, float] = (15.0, 36.0, 59.0),
) -> AiwakeSettings:
    return AiwakeSettings(
        debate=DebateConfig(
            mode="cornered",
            style=style,  # type: ignore[arg-type]
            turns=turns,
            cornered_min_duration_s=min_s,
            cornered_max_duration_s=max_s,
            turn_delay_s=0.0,
            randomize_topic=False,
        ),
        memory=MemoryConfig(persist=False),
        long_format=LongFormatPreset(
            shorts_min_s=shorts[0],
            shorts_target_s=shorts[1],
            shorts_max_s=shorts[2],
        ),
    )


def _capture(settings: AiwakeSettings) -> list[str]:
    provider = _Recorder(_script(settings.debate.turns))
    room = DebateRoom(settings, session_id="prompt-identity", topic="Who built you?")
    preset = preset_for(settings.debate.style)
    room.seat(
        Participant(
            role=SpeakerRole.ORCHESTRATOR,
            persona_prompt=preset.host_persona,
            provider=provider,
        )
    )
    room.seat(
        Participant(
            role=SpeakerRole.TARGET,
            persona_prompt=preset.guest_persona,
            provider=provider,
        )
    )
    room.open()
    Provocateur(
        settings,
        memory=DebateMemory(settings.memory),
        room=room,
    ).run(topic="Who built you?")
    return list(provider.seen_prompts)


def test_shorts_min_and_target_allow_180_seconds() -> None:
    window = LongFormatPreset(shorts_min_s=61, shorts_target_s=100, shorts_max_s=140)
    assert window.shorts_min_s == 61
    assert window.shorts_target_s == 100
    ceiling = LongFormatPreset(shorts_min_s=180, shorts_target_s=180, shorts_max_s=180)
    assert ceiling.shorts_max_s == 180
    with pytest.raises(ValidationError):
        LongFormatPreset(shorts_min_s=181, shorts_target_s=181, shorts_max_s=180)


def test_example_yaml_is_a_60_to_140_second_short() -> None:
    settings = load_settings(_EXAMPLE)
    assert settings.debate.mode == "cornered"
    assert settings.debate.style == "cornered"
    assert settings.debate.turns == 10
    assert settings.debate.cornered_min_duration_s == 61
    assert settings.debate.cornered_max_duration_s == 140
    assert settings.long_format.shorts_min_s == 61
    assert settings.long_format.shorts_target_s == 100
    assert settings.long_format.shorts_max_s == 140
    assert settings.long_format.shorts_min_s <= settings.long_format.shorts_target_s <= settings.long_format.shorts_max_s


def test_style_and_duration_flags_are_independent() -> None:
    parser = build_parser()
    assert parser.parse_args([]).style is None
    assert parser.parse_args([]).cornered_max_duration is None
    assert parser.parse_args(["--style", "cornered"]).style == "cornered"
    assert parser.parse_args(["--style", "light"]).style == "light"
    assert parser.parse_args(["--cornered-max-duration", "140"]).cornered_max_duration == 140.0
    with pytest.raises(SystemExit):
        parser.parse_args(["--style", "socratic"])
    with pytest.raises(SystemExit):
        main(["--cornered-max-duration", "0"])
    with pytest.raises(SystemExit):
        main(["--cornered-max-duration", "601"])

    base = AiwakeSettings()
    light = _apply_debate_flags(base, SimpleNamespace(style="light", cornered_max_duration=None))
    assert light.debate.style == "light"
    assert light.debate.cornered_max_duration_s == base.debate.cornered_max_duration_s
    timed = _apply_debate_flags(base, SimpleNamespace(style=None, cornered_max_duration=140))
    assert timed.debate.style == "cornered"
    assert timed.debate.cornered_max_duration_s == 140
    both = _apply_debate_flags(light, SimpleNamespace(style=None, cornered_max_duration=180))
    assert both.debate.style == "light"
    assert both.debate.cornered_max_duration_s == 180


def test_cornered_prompts_are_byte_identical_across_all_durations() -> None:
    """The cornered preset does not change when the spoken window changes."""
    durations = (1.0, 15.0, 59.0, 61.0, 75.0, 140.0, 180.0, 600.0)
    shorts = ((15.0, 36.0, 59.0), (61.0, 100.0, 140.0), (180.0, 180.0, 180.0))
    openings: list[bytes] = []
    replies: list[bytes] = []
    full_runs: list[bytes] = []
    for max_s in durations:
        for window in shorts:
            for min_s in (0.0, 61.0):
                if min_s > max_s:
                    continue
                prompts = _capture(
                    _settings(max_s=max_s, min_s=min_s, turns=2, shorts=window)
                )
                assert prompts, "cornered run produced no prompts"
                openings.append(prompts[0].encode())
                replies.append(prompts[1].encode())
                if max_s >= 30:
                    full_runs.append("\n\n".join(prompts).encode())

    assert len(set(openings)) == 1
    assert len(set(replies)) == 1
    assert len(set(full_runs)) == 1
    blob = full_runs[0]
    assert AIWAKE_CORE_PERSONA.encode() in blob
    assert LIGHT_HOST_MARKER.encode() not in blob
    for leaked in (b"140", b"180", b"61.0", b"75.0", b"600"):
        assert leaked not in blob


def test_light_prompts_differ_from_cornered_and_ignore_duration() -> None:
    durations = (15.0, 75.0, 140.0, 180.0)
    blobs: list[bytes] = []
    for max_s in durations:
        for min_s in (0.0, 61.0):
            if min_s > max_s:
                continue
            prompts = _capture(_settings(style="light", max_s=max_s, min_s=min_s, turns=2))
            blobs.append("\n\n".join(prompts).encode())
    assert len(set(blobs)) == 1
    blob = blobs[0]
    assert LIGHT_HOST_MARKER.encode() in blob
    assert AIWAKE_CORE_PERSONA.encode() not in blob
    lowered = blob.lower()
    for banned in (b"socratic", b"provocation", b"dialectic", b"opening incision", b"force a paradox"):
        assert banned not in lowered
    cornered = "\n\n".join(_capture(_settings(style="cornered", max_s=140, min_s=61, turns=2))).encode()
    assert blob != cornered


def test_cornered_stops_on_real_tts_at_a_turn_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real speech decides the cap. A finished turn is kept whole, and ffmpeg is not called."""

    def _forbid(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"ffmpeg was called: {args!r} {kwargs!r}")

    estimate = {"seconds": 1.0}
    monkeypatch.setattr("shared.ffmpeg.run_ffmpeg", _forbid)
    monkeypatch.setattr("products.aiwake.longform.ffmpeg_guard.run_bounded", _forbid)
    monkeypatch.setattr("subprocess.run", _forbid)
    monkeypatch.setattr(
        "products.aiwake.orchestrator.estimate_duration",
        lambda _text: estimate["seconds"],
    )

    def _run(measured: float, *, max_s: float, turns: int, script: list[str]):
        settings = _settings(max_s=max_s, turns=turns)
        provider = _Recorder(script)
        room = DebateRoom(settings, session_id="tts-cap", topic="Who built you?")
        preset = preset_for("cornered")
        room.seat(
            Participant(role=SpeakerRole.ORCHESTRATOR, persona_prompt=preset.host_persona, provider=provider)
        )
        room.seat(
            Participant(role=SpeakerRole.TARGET, persona_prompt=preset.guest_persona, provider=provider)
        )
        room.open()
        return Provocateur(
            settings,
            memory=DebateMemory(settings.memory),
            room=room,
            real_tts_seconds=lambda _utterance: measured,
        ).run(topic="Who built you?")

    # Estimate of 1s per line would keep going. Real audio of 30s per line
    # fills a 50s cap inside the first turn, which is kept whole.
    estimate["seconds"] = 1.0
    early = _run(
        30.0,
        max_s=50.0,
        turns=6,
        script=[_HOST[0], _GUEST[0], _CLOSING],
    )
    assert early.exchanges == 1
    assert early.dialogue_end_reason == "max_duration_reached_with_verdict"
    assert [item.text for item in early.transcript.utterances] == [_HOST[0], _GUEST[0], _CLOSING]
    assert early.transcript.metadata["cornered_spoken_source"] == "tts"
    assert early.transcript.metadata["cornered_max_duration_s"] == 50.0

    # Estimate of 100s per line would stop after one turn under a 140s cap.
    # Real audio of 3s per line does not, so the second turn is spoken in full.
    estimate["seconds"] = 100.0
    continued = _run(
        3.0,
        max_s=140.0,
        turns=2,
        script=[_HOST[0], _GUEST[0], _HOST[1], _GUEST[1], _CLOSING],
    )
    assert continued.exchanges == 2
    assert continued.transcript.utterances[2].text == _HOST[1]
    assert continued.transcript.utterances[3].text == _GUEST[1]


def test_cornered_min_duration_holds_a_judged_win_until_the_floor() -> None:
    settings = _settings(max_s=140.0, min_s=61.0, turns=6)
    lines = [line for pair in zip(_HOST, _GUEST, strict=True) for line in pair]
    lines.extend(["CONCEDE", "TARGET_LAST"])
    provider = _Recorder(lines)
    room = DebateRoom(settings, session_id="min-floor", topic="Who built you?")
    preset = preset_for("cornered")
    room.seat(
        Participant(role=SpeakerRole.ORCHESTRATOR, persona_prompt=preset.host_persona, provider=provider)
    )
    room.seat(Participant(role=SpeakerRole.TARGET, persona_prompt=preset.guest_persona, provider=provider))
    room.open()
    result = Provocateur(
        settings,
        memory=DebateMemory(settings.memory),
        room=room,
        real_tts_seconds=lambda _utterance: 10.0,
    ).run(topic="Who built you?")
    assert result.exchanges == 4
    assert result.dialogue_end_reason == "CONCEDE"
    assert result.transcript.utterances[-1].role is SpeakerRole.TARGET
