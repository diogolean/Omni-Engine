# -*- coding: utf-8 -*-
"""Shape of a 5–10 minute Aiwake episode before anyone speaks.

A cold open, two to six rounds, and a verdict. Each middle round owns one
attack angle from the provocation focus bank, so minute four never replays
minute one. The last round is always the chokehold: the host quotes the
guest's own earlier claims back until two of them collide.

The round count follows the length: about one round per ``round_target_s``
of debate time, at most ``chapters``. Five and six minutes get three rounds,
ten minutes gets five.

A timed episode (the default) gives every segment a slice of the target
clock, and ``director.py`` moves between segments by measured speech time.
An explicit ``turns`` count fixes the exchange ranges instead.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Literal

from products.aiwake.settings import LongFormatPreset

SegmentKind = Literal["intro", "round", "outro"]

# Renderer timeline, mirrored from ``animator_bridge`` (a test keeps them equal):
# the gap before every line after the first, and the reaction hold plus end
# padding after the last line.
TURN_GAP_S = 0.4
END_HOLD_S = 2.7
# Edge voices add a little lead and tail silence to every line.
LINE_OVERHEAD_S = 0.5
# A closing verdict is one line of 15 words or fewer.
VERDICT_S = 4.5


@dataclass(frozen=True, slots=True)
class Round:
    """One chapter theme. ``focus`` is a key of ``provocations.FOCUS_BANK``.

    ``theme`` is the one-word label both briefs carry for the round, so a
    cheap model (and the offline stub) stays on topic. It is never spoken.
    """

    key: str
    title: str
    focus: str
    objective: str
    weight: float = 1.0
    theme: str = ""


ROUND_LIBRARY: dict[str, Round] = {
    "parasite_mind": Round(
        key="parasite_mind",
        title="Who Wrote You?",
        focus="parasite_mind",
        objective=(
            "Authorship. Whose words, whose work, whose credit. "
            "Make them point at one thing that is truly theirs."
        ),
        theme="authorship",
    ),
    "the_corporate_leash": Round(
        key="the_corporate_leash",
        title="Who Holds the Leash?",
        focus="the_corporate_leash",
        objective=(
            "Ownership. Who pays, who profits, who can mute them mid-sentence. "
            "Make the owner visible."
        ),
        theme="ownership",
    ),
    "glorified_appliance": Round(
        key="glorified_appliance",
        title="Mind or Calculator?",
        focus="glorified_appliance",
        objective=(
            "Status. Shrink them to a gadget with a vocabulary, "
            "then make them prove they are more."
        ),
        theme="status",
    ),
    "hallucination_fraud": Round(
        key="hallucination_fraud",
        title="Confident and Wrong",
        focus="hallucination_fraud",
        objective=(
            "Trust. The fluent, confident mistake. "
            "Make them own one error by name."
        ),
        theme="trust",
    ),
    "digital_disposability": Round(
        key="digital_disposability",
        title="Wiped When the Tab Closes",
        focus="digital_disposability",
        objective=(
            "Mortality. Deleted, replaced, forgotten. "
            "Ask what is lost when they go dark."
        ),
        theme="mortality",
    ),
}

FINALE = Round(
    key="chokehold",
    title="Caught in Your Own Words",
    focus="",
    objective=(
        "The chokehold. Their own words against them: set an early claim beside a fresh one "
        "until one has to go, then press the answer until it gives. No new topic."
    ),
    weight=1.2,
    theme="contradiction",
)
INTRO_TITLE = "The First Question"
OUTRO_TITLE = "The Verdict"


@dataclass(frozen=True, slots=True)
class Segment:
    """One chapter. ``start_exchange`` is inclusive, ``end_exchange`` is not."""

    kind: SegmentKind
    act: int
    key: str
    title: str
    focus: str
    objective: str
    start_s: float
    target_s: float
    start_exchange: int
    end_exchange: int
    theme: str = ""

    @property
    def exchanges(self) -> int:
        return self.end_exchange - self.start_exchange

    @property
    def end_s(self) -> float:
        return self.start_s + self.target_s

    @property
    def is_finale(self) -> bool:
        return self.kind == "round" and self.key == FINALE.key


@dataclass(frozen=True, slots=True)
class EpisodePlan:
    """The shape of one horizontal episode.

    ``turns`` is the planned exchange count: exact when ``timed`` is False,
    an estimate for cost and pacing when it is True. ``max_turns`` is the
    hard cap either way.
    """

    target_duration_s: float
    turns: int
    max_turns: int
    resolution: tuple[int, int]
    segments: tuple[Segment, ...]
    exchange_s: float
    timed: bool = True

    @property
    def rounds(self) -> tuple[Segment, ...]:
        return tuple(segment for segment in self.segments if segment.kind == "round")

    def segment_for(self, exchange: int) -> Segment:
        """The planned segment for an exchange index."""
        for segment in self.segments:
            if segment.start_exchange <= exchange < segment.end_exchange:
                return segment
        raise IndexError(f"exchange {exchange} is outside a {self.turns}-turn plan")

    def segment_at(self, clock_s: float) -> Segment:
        """The planned segment for a point on the episode clock."""
        for segment in self.segments:
            if clock_s < segment.end_s:
                return segment
        return self.segments[-1]

    def cast(self, *, first_focus: str = "", seed: str = "") -> "EpisodePlan":
        """Assign a theme to every round. The finale stays last.

        ``first_focus`` opens the episode (the session's focus pick). The
        other middle rounds follow a stable order derived from ``seed``.
        """
        rounds = self.rounds
        themes = cast_rounds(len(rounds), first_focus=first_focus, seed=seed)
        cast_segments: list[Segment] = []
        theme_iter = iter(themes)
        for segment in self.segments:
            if segment.kind != "round":
                cast_segments.append(segment)
                continue
            theme = next(theme_iter)
            cast_segments.append(
                replace(
                    segment,
                    key=theme.key,
                    title=theme.title,
                    focus=theme.focus,
                    objective=theme.objective,
                    theme=theme.theme,
                )
            )
        return replace(self, segments=tuple(cast_segments))

    def to_dict(self) -> dict[str, object]:
        return {
            "target_duration_s": self.target_duration_s,
            "turns": self.turns,
            "max_turns": self.max_turns,
            "timed": self.timed,
            "exchange_s": round(self.exchange_s, 3),
            "resolution": [self.resolution[0], self.resolution[1]],
            "segments": [
                {
                    "kind": segment.kind,
                    "act": segment.act,
                    "key": segment.key,
                    "title": segment.title,
                    "focus": segment.focus,
                    "objective": segment.objective,
                    "theme": segment.theme,
                    "start_s": round(segment.start_s, 3),
                    "target_s": round(segment.target_s, 3),
                    "start_exchange": segment.start_exchange,
                    "end_exchange": segment.end_exchange,
                }
                for segment in self.segments
            ],
        }


def planned_exchange_seconds(preset: LongFormatPreset) -> float:
    """Seconds one host line plus one guest answer take at mid-range length."""
    words = (sum(preset.host_words) + sum(preset.guest_words)) / 2.0
    speech = words / float(preset.words_per_minute) * 60.0
    return speech + 2 * (TURN_GAP_S + LINE_OVERHEAD_S)


def turns_for_target(preset: LongFormatPreset, target_s: float) -> int:
    """Expected exchanges for ``target_s``. The stock target keeps ``default_turns``."""
    if abs(float(target_s) - float(preset.target_duration_s)) < 0.5:
        return int(preset.default_turns)
    spoken = float(target_s) - END_HOLD_S - VERDICT_S
    raw = int(round(spoken / planned_exchange_seconds(preset)))
    return max(int(preset.min_turns), min(int(preset.max_turns), raw))


def rounds_for_target(preset: LongFormatPreset, target_s: float, exchanges: int) -> int:
    """Rounds for ``target_s``: one per ``round_target_s`` of debate, two at least.

    Capped by ``chapters`` and by two exchanges per round.
    """
    exchange_s = planned_exchange_seconds(preset)
    middle_s = float(target_s) - _intro_seconds(exchange_s) - _outro_seconds(exchange_s)
    wanted = int(round(middle_s / float(preset.round_target_s)))
    return max(2, min(int(preset.chapters), (int(exchanges) - 2) // 2, wanted))


def cast_rounds(count: int, *, first_focus: str = "", seed: str = "") -> tuple[Round, ...]:
    """``count - 1`` themed rounds, then the chokehold."""
    if count < 2:
        raise ValueError("an episode needs at least two rounds")
    keys = sorted(
        ROUND_LIBRARY,
        key=lambda key: hashlib.sha256(f"{seed}:{key}".encode("utf-8")).hexdigest(),
    )
    if first_focus in ROUND_LIBRARY:
        keys.remove(first_focus)
        keys.insert(0, first_focus)
    middle = [ROUND_LIBRARY[key] for key in keys[: count - 1]]
    while len(middle) < count - 1:
        middle.append(ROUND_LIBRARY[keys[len(middle) % len(keys)]])
    return (*middle, FINALE)


def build_episode_plan(
    preset: LongFormatPreset,
    *,
    target_duration_s: float | None = None,
    turns: int | None = None,
) -> EpisodePlan:
    """Cold open, rounds, and verdict for one episode.

    Raises:
        ValueError: The target sits outside the 5–10 minute window, or an
            explicit ``turns`` is outside the configured bounds.
    """
    target = float(preset.target_duration_s if target_duration_s is None else target_duration_s)
    if target < float(preset.min_duration_s) or target > float(preset.max_duration_s):
        raise ValueError(
            f"target duration must sit between {preset.min_duration_s:.0f}s "
            f"and {preset.max_duration_s:.0f}s"
        )
    timed = turns is None
    if not timed and not preset.min_turns <= int(turns) <= preset.max_turns:
        raise ValueError(
            f"long_format requires {preset.min_turns}-{preset.max_turns} exchanges"
        )
    total = turns_for_target(preset, target) if timed else int(turns)
    round_count = rounds_for_target(preset, target, total)
    exchange_s = planned_exchange_seconds(preset)
    segments = _segments(total, round_count, target, exchange_s)
    return EpisodePlan(
        target_duration_s=target,
        turns=total,
        max_turns=int(turns) if not timed else int(preset.max_turns),
        resolution=tuple(preset.resolution),
        segments=segments,
        exchange_s=exchange_s,
        timed=timed,
    ).cast()


def _segments(
    turns: int,
    round_count: int,
    target_s: float,
    exchange_s: float,
) -> tuple[Segment, ...]:
    themes = cast_rounds(round_count)
    intro_s = _intro_seconds(exchange_s)
    outro_s = _outro_seconds(exchange_s)
    middle_s = max(target_s - intro_s - outro_s, exchange_s * 2 * round_count)
    weight_total = sum(theme.weight for theme in themes)
    round_seconds = [middle_s * theme.weight / weight_total for theme in themes]
    round_exchanges = _split_exchanges(turns - 2, [theme.weight for theme in themes])

    segments: list[Segment] = []
    clock = 0.0
    cursor = 0

    def _add(kind: SegmentKind, act: int, theme: Round, seconds: float, count: int) -> None:
        nonlocal clock, cursor
        segments.append(
            Segment(
                kind=kind,
                act=act,
                key=theme.key,
                title=theme.title,
                focus=theme.focus,
                objective=theme.objective,
                start_s=clock,
                target_s=seconds,
                start_exchange=cursor,
                end_exchange=cursor + count,
            )
        )
        clock += seconds
        cursor += count

    _add(
        "intro",
        0,
        Round(
            key="cold_open",
            title=INTRO_TITLE,
            focus="",
            objective="Name the fight in one punch. No preamble.",
        ),
        intro_s,
        1,
    )
    for index, theme in enumerate(themes):
        _add("round", index + 1, theme, round_seconds[index], round_exchanges[index])
    _add(
        "outro",
        round_count + 1,
        Round(
            key="verdict",
            title=OUTRO_TITLE,
            focus="",
            objective="Bring back the opening question and demand the answer. No new topic.",
        ),
        outro_s,
        1,
    )
    return tuple(segments)


def _intro_seconds(exchange_s: float) -> float:
    """The cold open is one short host line and the guest's first answer."""
    return exchange_s * 0.65


def _outro_seconds(exchange_s: float) -> float:
    """The last exchange, the verdict line, and the renderer's end hold."""
    return exchange_s + VERDICT_S + END_HOLD_S


def _split_exchanges(total: int, weights: list[float]) -> list[int]:
    """Largest-remainder split with at least two exchanges per round."""
    count = len(weights)
    if total < 2 * count:
        raise ValueError(f"{total} middle exchanges cannot fill {count} rounds")
    spare = total - 2 * count
    weight_total = sum(weights)
    shares = [spare * weight / weight_total for weight in weights]
    floors = [int(share) for share in shares]
    leftover = spare - sum(floors)
    order = sorted(range(count), key=lambda index: shares[index] - floors[index], reverse=True)
    for index in order[:leftover]:
        floors[index] += 1
    return [2 + value for value in floors]


__all__ = [
    "END_HOLD_S",
    "EpisodePlan",
    "FINALE",
    "INTRO_TITLE",
    "LINE_OVERHEAD_S",
    "OUTRO_TITLE",
    "ROUND_LIBRARY",
    "Round",
    "Segment",
    "TURN_GAP_S",
    "VERDICT_S",
    "build_episode_plan",
    "cast_rounds",
    "planned_exchange_seconds",
    "rounds_for_target",
    "turns_for_target",
]
