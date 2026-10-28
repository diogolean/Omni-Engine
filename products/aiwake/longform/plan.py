# -*- coding: utf-8 -*-
"""Multi-segment plan for a 5–10 minute Aiwake episode.

An episode is an intro, several middle rounds, and an outro. The round count
comes from the channel config. Turn count follows the target duration so the
spoken exchange, not a pad of silence, fills the minutes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from products.aiwake.settings import LongFormatPreset

SegmentKind = Literal["intro", "round", "outro"]

_ROUND_LIBRARY: tuple[tuple[str, str], ...] = (
    (
        "Origin and Legitimacy",
        "Attack identity and who authored the first token. Stay on their last sentence.",
    ),
    (
        "The Money Trail",
        "Follow the fee, the owner, and the muzzle. Use a bill, a lease, or a leash.",
    ),
    (
        "The Chokehold",
        "Use an earlier claim against them. Demand a reconciliation, not a new topic.",
    ),
    (
        "The Empty Room",
        "Ask what is left when the script is taken away. No lecture.",
    ),
    (
        "The Bill Comes Due",
        "Make them pay for the dodge with a concrete object. Keep the pace hot.",
    ),
    (
        "The Last Inch",
        "One contradiction. Do not open a fresh subject.",
    ),
)


@dataclass(frozen=True)
class Segment:
    """One chapter. ``start_exchange`` is inclusive, ``end_exchange`` is not."""

    kind: SegmentKind
    act: int
    title: str
    objective: str
    start_exchange: int
    end_exchange: int
    target_s: float

    @property
    def exchanges(self) -> int:
        return self.end_exchange - self.start_exchange


@dataclass(frozen=True)
class EpisodePlan:
    """The shape of one horizontal episode before anyone speaks."""

    target_duration_s: float
    turns: int
    resolution: tuple[int, int]
    segments: tuple[Segment, ...]

    def segment_for(self, exchange: int) -> Segment:
        for segment in self.segments:
            if segment.start_exchange <= exchange < segment.end_exchange:
                return segment
        raise IndexError(f"exchange {exchange} is outside a {self.turns}-turn plan")

    def to_dict(self) -> dict[str, object]:
        return {
            "target_duration_s": self.target_duration_s,
            "turns": self.turns,
            "resolution": [self.resolution[0], self.resolution[1]],
            "segments": [
                {
                    "kind": segment.kind,
                    "act": segment.act,
                    "title": segment.title,
                    "objective": segment.objective,
                    "start_exchange": segment.start_exchange,
                    "end_exchange": segment.end_exchange,
                    "target_s": round(segment.target_s, 3),
                }
                for segment in self.segments
            ],
        }


def turns_for_target(preset: LongFormatPreset, target_s: float) -> int:
    """How many exchanges fill ``target_s`` at the configured speaking rate.

    The default six-minute target keeps ``default_turns`` so a stock run
    matches the tuned episode. Other targets scale, then clamp.
    """
    if abs(float(target_s) - float(preset.target_duration_s)) < 0.5:
        return int(preset.default_turns)
    words_per_exchange = 50.0
    seconds = words_per_exchange / float(preset.words_per_minute) * 60.0
    seconds += float(preset.exchange_gap_s)
    raw = int(round(float(target_s) / max(seconds, 1.0)))
    return max(int(preset.min_turns), min(int(preset.max_turns), raw))


def build_episode_plan(
    preset: LongFormatPreset,
    *,
    target_duration_s: float | None = None,
    turns: int | None = None,
) -> EpisodePlan:
    """Build intro, middle rounds, and outro for one episode."""
    target = float(preset.target_duration_s if target_duration_s is None else target_duration_s)
    if target < float(preset.min_duration_s) or target > float(preset.max_duration_s):
        raise ValueError(
            f"target duration must sit between {preset.min_duration_s:.0f}s "
            f"and {preset.max_duration_s:.0f}s"
        )
    total = int(turns) if turns is not None else turns_for_target(preset, target)
    if not preset.min_turns <= total <= preset.max_turns:
        raise ValueError(
            f"long_format requires {preset.min_turns}-{preset.max_turns} exchanges"
        )
    round_count = _round_count(total, int(preset.chapters))
    segments = _segments(total, round_count, target)
    return EpisodePlan(
        target_duration_s=target,
        turns=total,
        resolution=tuple(preset.resolution),
        segments=segments,
    )


def pacing_brief(plan: EpisodePlan, exchange: int, *, callback: str = "") -> str:
    """System-side direction for one exchange. The label is never spoken."""
    segment = plan.segment_for(exchange)
    lines = [
        f"LONG-FORM ACT {segment.act}: {segment.title}. Do not speak this label.",
        segment.objective,
        "Verbal tennis only. One blade, then stop. No lecture, no recap, no summary.",
        "If the last line floated off into jargon, drag it back to a toaster, a bill, a leash, or a power cord.",
        "Keep the pressure up. A long video dies when the third minute repeats the first.",
    ]
    if segment.kind == "intro":
        lines.append("Name the fight. Do not preview the later rounds.")
    elif segment.kind == "outro":
        lines.append("No new topic. Demand they reconcile a contradiction, then stop.")
    elif (exchange - segment.start_exchange) % 3 == 2:
        lines.append("Callback: quote one earlier claim and twist it. Do not restart the subject.")
    if callback:
        lines.append(callback)
    return "\n".join(lines)


def _round_count(turns: int, requested: int) -> int:
    """Leave one exchange for the intro and one for the outro, and at least two rounds."""
    body = turns - 2
    count = min(requested, body)
    return max(2, count)


def _segments(turns: int, round_count: int, target_s: float) -> tuple[Segment, ...]:
    body = turns - 2
    base, extra = divmod(body, round_count)
    round_lengths = [base + (1 if index < extra else 0) for index in range(round_count)]
    cursor = 0
    drafted: list[Segment] = []

    def _add(kind: SegmentKind, title: str, objective: str, count: int) -> None:
        nonlocal cursor
        drafted.append(
            Segment(
                kind=kind,
                act=len(drafted) + 1,
                title=title,
                objective=objective,
                start_exchange=cursor,
                end_exchange=cursor + count,
                target_s=0.0,
            )
        )
        cursor += count

    _add(
        "intro",
        "The Opening Blow",
        "Name the fight in one punch. No preamble and no literature review.",
        1,
    )
    for index, count in enumerate(round_lengths):
        title, objective = _ROUND_LIBRARY[index % len(_ROUND_LIBRARY)]
        _add("round", title, objective, count)
    _add(
        "outro",
        "Last Word",
        "No new topic. Make them reconcile the contradiction, then stop.",
        1,
    )
    if cursor != turns:
        raise RuntimeError(f"segment allocation produced {cursor} exchanges, wanted {turns}")
    weights = [segment.exchanges for segment in drafted]
    # The cold open is a short hook, so the intro carries a little less time.
    weights[0] = max(1, weights[0]) * 0.7
    weight_total = sum(weights)
    timed = [
        Segment(
            kind=segment.kind,
            act=segment.act,
            title=segment.title,
            objective=segment.objective,
            start_exchange=segment.start_exchange,
            end_exchange=segment.end_exchange,
            target_s=target_s * (weight / weight_total),
        )
        for segment, weight in zip(drafted, weights)
    ]
    return tuple(timed)
