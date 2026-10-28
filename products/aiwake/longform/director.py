# -*- coding: utf-8 -*-
"""Runs a long episode on the clock.

The director decides, before each exchange, which chapter it belongs to,
how hard the host pushes, whether this line must land a retention hook,
and what stance the guest takes. It keeps the episode clock from the
measured length of every spoken line, so rounds turn over and the verdict
lands near the target length instead of after a fixed turn count.

Rules it holds:

* every round gets at least two exchanges, rounds never skip, and the
  chokehold finale is always reached before the verdict, even when short
  lines would hit ``max_turns`` before the clock runs out;
* a hook lands about every ``hook_interval_s`` and never later than
  ``hook_max_gap_s`` after the previous one: before an exchange starts,
  the director assumes it may run 30% longer than usual, and, while only a
  few exchanges are measured, no shorter than planned;
* escalation follows the share of the clock already spent, so minute five
  is meaner than minute one;
* the finale alternates: a chokehold sets one early claim against the
  latest answer and the guest tries to reconcile them, then the host
  presses and the guest hits back; no claim is pitted twice;
* a hook that quotes the guest hands the host one sentence, not a whole
  answer: the first-person sentence that stands alone (``self_claim``),
  so the quote makes sense to a viewer who missed the minute it came from;
* the guest plants a claim, defends it, counter-punches every third answer
  of a round, holds out through the finale and cracks on its last exchange,
  then gets the last word before the verdict. Whether a finale exchange is
  the last one is decided before it starts, so the crack always lands.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from products.aiwake.memory import content_tokens
from products.aiwake.settings import LongFormatPreset

from .plan import END_HOLD_S, TURN_GAP_S, VERDICT_S, EpisodePlan, Segment
from .text import addresses_you, points_back, sentences, speaks_for_self

HOOK_ROTATION: tuple[str, ...] = ("callback", "stakes", "image", "reversal")
# Spoken exchanges vary by about a quarter around their average (a
# counter-punch runs long), so the hook-gap check leaves this much room.
_NEXT_EXCHANGE_MARGIN = 1.3
# Until this many exchanges are measured, the planned length is a floor for
# that check: two short answers say little about the third.
_SETTLED_EXCHANGES = 4
# The guest brief asks for one quotable sentence about itself, ten words or fewer.
CLAIM_MAX_WORDS = 10
_CLAIM_MIN_WORDS = 4
_LEADING_CONNECTOR = re.compile(r"^(?:and|but|so|then|well|yet|or)\b[\s,]*", re.IGNORECASE)
_OPENS_WITH_SELF = re.compile(r"^(?:i|my)\b", re.IGNORECASE)
_QUOTE_MARKS = ('"', "\u201c", "\u201d")


def self_claim(answer: str) -> str:
    """The sentence of ``answer`` that stands alone as a claim about the guest, or ``""``.

    It is in the first person, not aimed at "you", four to ten words with
    two content words, and has no word that leans on an earlier line
    ("it", "that", "they", ...). A leading "But" or "And" is dropped. A
    sentence opening on "I" or "My" wins, then more content words, then
    the earlier sentence.
    """
    best, best_score = "", (False, 0, 0)
    for order, sentence in enumerate(sentences(answer)):
        line = _LEADING_CONNECTOR.sub("", sentence).strip()
        if not line or "?" in line or any(mark in line for mark in _QUOTE_MARKS):
            continue
        line = line[0].upper() + line[1:]
        if not _CLAIM_MIN_WORDS <= len(line.split()) <= CLAIM_MAX_WORDS:
            continue
        if not speaks_for_self(line) or addresses_you(line) or points_back(line):
            continue
        content = len(content_tokens(line))
        score = (bool(_OPENS_WITH_SELF.match(line)), min(content, 4), -order)
        if content >= 2 and (not best or score > best_score):
            best, best_score = line, score
    return best


@dataclass(frozen=True, slots=True)
class Beat:
    """Direction for one exchange.

    ``callback`` and ``claims`` hold the guest's own words for a quote hook;
    ``opening`` is the cold-open question the bookend brings back.
    """

    exchange: int
    segment: Segment
    segment_index: int
    round_exchange: int
    start_s: float
    progress: float
    tier: int
    hook: str
    stance: str
    callback: str = ""
    claims: tuple[str, ...] = ()
    opening: str = ""

    @property
    def is_cold_open(self) -> bool:
        return self.segment.kind == "intro"

    @property
    def is_verdict(self) -> bool:
        return self.segment.kind == "outro"

    @property
    def opens_chapter(self) -> bool:
        return self.round_exchange == 0


@dataclass(frozen=True, slots=True)
class _Answer:
    exchange: int
    text: str
    claim: str
    standalone: bool

    @property
    def quote(self) -> str:
        """What a hook quotes back: the sharp claim, else the whole answer."""
        return self.claim or self.text


@dataclass
class _Record:
    exchange: int
    segment_index: int
    kind: str
    key: str
    title: str
    start_s: float
    hook: str
    stance: str
    tier: int
    hook_verified: bool | None = None
    end_s: float = 0.0
    host_turn: int | None = None
    guest_turn: int | None = None
    relaxed: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class EpisodeDirector:
    """Clock, chapters, hooks, and guest stance for one long episode."""

    plan: EpisodePlan
    preset: LongFormatPreset
    clock_s: float = 0.0
    _segment_index: int = 0
    _segment_exchanges: int = 0
    _exchange_seconds: list[float] = field(default_factory=list)
    _exchange_start_s: float = 0.0
    _last_hook_s: float = 0.0
    _hook_turn: int = 0
    _answers: list[_Answer] = field(default_factory=list)
    _called_back: set[str] = field(default_factory=set)
    _pitted: set[str] = field(default_factory=set)
    _finale_closing: bool = False
    _opening: str = ""
    _records: list[_Record] = field(default_factory=list)
    _closed: bool = False
    _lines: int = 0

    # -- Clock ----------------------------------------------------------------
    def add_line(self, seconds: float) -> None:
        """Advance the clock by one spoken line, including the gap before it."""
        if self._lines:
            self.clock_s += TURN_GAP_S
        self.clock_s += max(0.0, float(seconds))
        self._lines += 1

    @property
    def average_exchange_s(self) -> float:
        measured = self._exchange_seconds[1:] or self._exchange_seconds
        if not measured:
            return self.plan.exchange_s
        return sum(measured) / len(measured)

    def projected_end_s(self, *, extra_exchanges: int = 0) -> float:
        """Video length if the verdict exchange started after ``extra_exchanges`` more."""
        average = self.average_exchange_s
        return self.clock_s + average * (extra_exchanges + 1) + VERDICT_S + END_HOLD_S

    # -- Exchanges ------------------------------------------------------------
    def next_beat(self, exchange: int) -> Beat | None:
        """Direction for ``exchange``, or None once the verdict exchange has run."""
        if self._closed:
            return None
        self._exchange_start_s = self.clock_s
        if exchange == 0:
            index = 0
        elif self.plan.timed:
            index = self._timed_segment(exchange)
        else:
            index = self.plan.segments.index(self.plan.segment_for(exchange))
        if index != self._segment_index:
            self._segment_index = index
            self._segment_exchanges = 0
        segment = self.plan.segments[index]
        if segment.is_finale:
            self._finale_closing = self._closes_finale(exchange)
        beat = Beat(
            exchange=exchange,
            segment=segment,
            segment_index=index,
            round_exchange=self._segment_exchanges,
            start_s=self.clock_s,
            progress=min(1.0, self.clock_s / max(1.0, self.plan.target_duration_s)),
            tier=self._tier(segment),
            hook=self._hook(segment, exchange),
            stance=self._stance(segment),
            opening=self._opening,
        )
        if beat.hook == "callback":
            quote = self._callback_quote(exchange)
            beat = replace(beat, callback=quote) if quote else replace(beat, hook="image")
        if beat.hook == "chokehold":
            beat = replace(beat, claims=self._claims_on_record())
        if beat.hook:
            self._last_hook_s = beat.start_s
        self._records.append(
            _Record(
                exchange=exchange,
                segment_index=index,
                kind=segment.kind,
                key=segment.key,
                title=segment.title,
                start_s=round(beat.start_s, 3),
                hook=beat.hook,
                stance=beat.stance,
                tier=beat.tier,
            )
        )
        return beat

    def host_spoke(
        self,
        beat: Beat,
        text: str,
        *,
        hook_verified: bool | None,
        relaxed: list[str] | tuple[str, ...] = (),
        turn_index: int | None = None,
    ) -> None:
        """Record the host line. The cold open becomes the bookend question.

        ``relaxed`` lists the soft checks the shipped line still fails.
        ``turn_index`` is the line's place in the transcript.
        """
        if beat.is_cold_open and not self._opening:
            self._opening = text.strip()
        if beat.callback:
            self._called_back.add(beat.callback)
        self._pitted.update(beat.claims)
        record = self._record_for(beat)
        if record is not None:
            record.host_turn = turn_index
            if beat.hook:
                record.hook_verified = hook_verified
            if relaxed:
                record.relaxed["host"] = list(relaxed)

    def guest_spoke(
        self,
        beat: Beat,
        text: str,
        *,
        relaxed: list[str] | tuple[str, ...] = (),
        turn_index: int | None = None,
    ) -> None:
        """Close the exchange and keep the answer for later quote hooks.

        Only a claim or a defence says something about the guest worth quoting
        back. A counter-punch is aimed at the host, and an answer to a callback
        or in the finale is about earlier lines, so a chokehold falls back on
        those only when the rounds left too few claims.
        """
        standalone = (
            beat.stance in {"claim", "defend"} and beat.hook != "callback" and not beat.segment.is_finale
        )
        answer = text.strip()
        self._answers.append(_Answer(beat.exchange, answer, self_claim(answer), standalone))
        self._exchange_seconds.append(self.clock_s - self._exchange_start_s)
        self._segment_exchanges += 1
        record = self._record_for(beat)
        if record is not None:
            record.end_s = round(self.clock_s, 3)
            record.guest_turn = turn_index
            if relaxed:
                record.relaxed["guest"] = list(relaxed)
        if beat.is_verdict:
            self._closed = True

    def _record_for(self, beat: Beat) -> _Record | None:
        if self._records and self._records[-1].exchange == beat.exchange:
            return self._records[-1]
        return None

    @property
    def opening(self) -> str:
        return self._opening

    @property
    def finished(self) -> bool:
        return self._closed

    def summary(self) -> dict[str, object]:
        """JSON-safe record for transcript metadata and the publish step."""
        hooks = [record for record in self._records if record.hook]
        gaps = [later.start_s - earlier.start_s for earlier, later in zip(hooks, hooks[1:])]
        return {
            "estimated_duration_s": round(self.clock_s + END_HOLD_S, 3),
            "hook_interval_s": self.preset.hook_interval_s,
            "hook_max_gap_s": self.preset.hook_max_gap_s,
            "max_hook_gap_s": round(max(gaps), 3) if gaps else 0.0,
            "hooks_missed": sum(1 for record in hooks if record.hook_verified is False),
            "relaxed_lines": sum(len(record.relaxed) for record in self._records),
            "beats": [
                {
                    "exchange": record.exchange,
                    "segment_index": record.segment_index,
                    "kind": record.kind,
                    "key": record.key,
                    "title": record.title,
                    "start_s": record.start_s,
                    "end_s": record.end_s,
                    "host_turn": record.host_turn,
                    "guest_turn": record.guest_turn,
                    "hook": record.hook,
                    "hook_verified": record.hook_verified,
                    "stance": record.stance,
                    "tier": record.tier,
                    "relaxed": {role: list(problems) for role, problems in record.relaxed.items()},
                }
                for record in self._records
            ],
        }

    # -- Decisions ------------------------------------------------------------
    def _timed_segment(self, exchange: int) -> int:
        segments = self.plan.segments
        index = self._segment_index
        current = segments[index]
        if exchange >= self.plan.max_turns - 1:
            return len(segments) - 1
        if current.kind == "intro":
            return index + 1
        if current.is_finale:
            return index + 1 if self._finale_closing else index
        if self._segment_exchanges < 2:
            return index
        if self._segment_exchanges >= self._round_budget(current):
            return index + 1
        rounds_left = sum(1 for item in segments[index + 1 :] if item.kind == "round")
        room = self.plan.target_duration_s - self.projected_end_s()
        out_of_time = rounds_left * 2 * self.average_exchange_s >= room
        if self.clock_s >= current.end_s or out_of_time:
            return index + 1
        return index

    def _round_budget(self, segment: Segment) -> int:
        """Most exchanges a middle round may take: its share of ``max_turns``.

        Lines far shorter than planned would otherwise keep the clock behind
        and let the first rounds spend the turn cap before the finale.
        """
        total_s = sum(item.target_s for item in self.plan.rounds) or 1.0
        return max(2, int((self.plan.max_turns - 2) * segment.target_s / total_s))

    def _closes_finale(self, exchange: int) -> bool:
        """True when this finale exchange should be the last before the verdict.

        The verdict exchange starts next when that lands closest to the
        target. The finale keeps two exchanges at least.
        """
        if self._segment_exchanges < 1:
            return False
        if not self.plan.timed:
            return self.plan.segment_for(exchange + 1).kind == "outro"
        if exchange + 1 >= self.plan.max_turns - 1:
            return True
        half = self.average_exchange_s / 2.0
        return self.projected_end_s(extra_exchanges=1) >= self.plan.target_duration_s - half

    def _tier(self, segment: Segment) -> int:
        if segment.kind == "intro":
            return 0
        if segment.kind == "outro":
            return 4
        progress = self.clock_s / max(1.0, self.plan.target_duration_s)
        return 1 + min(3, int(progress * 4))

    def _hook(self, segment: Segment, exchange: int) -> str:
        if exchange == 0:
            return "open"
        if segment.kind == "outro":
            return "bookend"
        if segment.is_finale and self._segment_exchanges % 2 == 0 and len(self._answers) >= 2:
            return "chokehold"
        if self._segment_exchanges == 0 and segment.kind == "round":
            return "chapter"
        gap = self.clock_s - self._last_hook_s
        if gap < self.preset.hook_interval_s and gap + self._next_exchange_s() <= self.preset.hook_max_gap_s:
            return ""
        kind = self._rotate()
        if kind == "callback" and segment.is_finale:
            # The finale already quotes them on every other line.
            kind = self._rotate()
        return kind

    def _next_exchange_s(self) -> float:
        """A generous guess at the next exchange's length, for the hook-gap check."""
        guesses = [self.average_exchange_s, *self._exchange_seconds[-1:]]
        if len(self._exchange_seconds) < _SETTLED_EXCHANGES:
            guesses.append(self.plan.exchange_s)
        return _NEXT_EXCHANGE_MARGIN * max(guesses)

    def _rotate(self) -> str:
        kind = HOOK_ROTATION[self._hook_turn % len(HOOK_ROTATION)]
        self._hook_turn += 1
        return kind

    def _stance(self, segment: Segment) -> str:
        if segment.kind == "intro":
            return "claim"
        if segment.kind == "outro":
            return "last_word"
        position = self._segment_exchanges
        if segment.is_finale:
            if self._finale_closing:
                return "crack"
            return "counter" if position % 2 else "reconcile"
        # The first round defends the cold-open claim twice before hitting back.
        if position % 3 == (1 if segment.act > 1 else 2):
            return "counter"
        return "defend"

    def _callback_quote(self, exchange: int) -> str:
        """An earlier answer to quote back: two or more exchanges old and unused.

        Answers with a sharp claim go first, then the oldest one.
        """
        eligible = [
            answer
            for answer in self._answers
            if answer.standalone
            and answer.exchange <= exchange - 2
            and answer.quote not in self._called_back
            and len(content_tokens(answer.quote)) >= 2
        ]
        if not eligible:
            return ""
        return max(eligible, key=lambda answer: (bool(answer.claim), -answer.exchange)).quote

    def _claims_on_record(self) -> tuple[str, str]:
        """Two of the guest's own lines, in the order spoken, for a chokehold.

        One is from the three latest standalone answers not yet pitted; the
        other is the oldest one not yet quoted back, so the opening boast
        goes up against what they said a minute ago. Answers with a sharp
        claim go first on both sides.
        """
        standalone = [answer for answer in self._answers if answer.standalone]
        pool = standalone if len(standalone) >= 2 else list(self._answers)
        unpitted = [answer for answer in pool if answer.quote not in self._pitted]
        unused = [answer for answer in unpitted if answer.quote not in self._called_back]
        latest = max((unused or unpitted or pool)[-3:], key=lambda answer: (bool(answer.claim), answer.exchange))
        earlier = [answer for answer in pool if answer.quote != latest.quote] or [pool[0]]
        fresh = [
            answer
            for answer in earlier
            if answer.quote not in self._pitted
            and answer.quote not in self._called_back
            and len(content_tokens(answer.quote)) >= 2
        ]
        choices = fresh or [answer for answer in earlier if answer.quote not in self._pitted] or earlier
        first = max(choices, key=lambda answer: (bool(answer.claim), -answer.exchange))
        older, newer = sorted((first, latest), key=lambda answer: answer.exchange)
        return (older.quote, newer.quote)


__all__ = ["CLAIM_MAX_WORDS", "Beat", "EpisodeDirector", "HOOK_ROTATION", "self_claim"]
