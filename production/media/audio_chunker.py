# -*- coding: utf-8 -*-
"""
Semantic 3–5 s visual-clip chunking from ElevenLabs word timestamps.

Prefers sentence / clause boundaries. Never invents timings that are not
in the alignment stream.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")
_CLAUSE_END = re.compile(r"[,;:—–][\"')\]]*$")
# A sentence-final word may run this far past max_s so it is not left as the
# first word of the next beat ("perfectly. The Coso..."). 0.55 keeps a 5.0 s
# window at or under the 5.6 s chunk cap used by the pipeline tests.
_SOFT_OVERRUN_S = 0.55

MIN_CHUNK_S = 3.0
MAX_CHUNK_S = 5.0


@dataclass(frozen=True)
class AudioChunk:
    index: int
    start_s: float
    end_s: float
    text: str

    @property
    def duration_s(self) -> float:
        return max(0.0, float(self.end_s) - float(self.start_s))


def _is_boundary(token: str, *, sentence: bool) -> bool:
    tok = (token or "").strip()
    if not tok:
        return False
    return bool(_SENTENCE_END.search(tok) if sentence else _CLAUSE_END.search(tok))


def _sentence_end_index(buf: list[tuple[str, float, float]]) -> int:
    for index in range(len(buf) - 1, -1, -1):
        if _is_boundary(buf[index][0], sentence=True):
            return index
    return -1


def _leading_fragment_count(words: list[tuple[str, float, float]]) -> int:
    """Words of a previous sentence stuck on the front of this beat."""
    if len(words) < 2:
        return 0
    first = words[0][0].strip("\"'")
    if not first[:1].islower() or first.lower().rstrip(".!?") in {"a", "an", "i"}:
        return 0
    acc = ""
    for index, (tok, _start, _end) in enumerate(words):
        acc = f"{acc} {tok}".strip()
        if _is_boundary(tok, sentence=True):
            if index < len(words) - 1 and len(acc) <= 48:
                return index + 1
            return 0
        if len(acc) > 48:
            return 0
    return 0


def chunk_word_timings(
    word_timings: list[tuple[str, float, float]] | None,
    *,
    min_s: float = MIN_CHUNK_S,
    max_s: float = MAX_CHUNK_S,
) -> list[AudioChunk]:
    """
    Pack word alignments into 3–5 s windows at natural pauses.

    ``word_timings`` is ``[(word, start_s, end_s), ...]``.
    """
    words = [
        (str(w or "").strip(), float(s), float(e))
        for w, s, e in (word_timings or [])
        if str(w or "").strip() and float(e) > float(s)
    ]
    if not words:
        return []

    min_s = max(1.5, float(min_s))
    max_s = max(min_s + 0.4, float(max_s))

    groups: list[list[tuple[str, float, float]]] = []
    buf: list[tuple[str, float, float]] = []

    def _flush() -> None:
        if not buf:
            return
        groups.append(list(buf))
        buf.clear()

    for i, (tok, start, end) in enumerate(words):
        if not buf:
            buf.append((tok, start, end))
            continue
        dur = end - buf[0][1]
        held = buf[-1][2] - buf[0][1]
        tentative_dur = end - buf[0][1]
        at_sentence = _is_boundary(tok, sentence=True)
        at_clause = _is_boundary(tok, sentence=False)
        last = i == len(words) - 1

        if tentative_dur <= max_s:
            buf.append((tok, start, end))
            if last:
                _flush()
                break
            if at_sentence and tentative_dur >= min_s:
                _flush()
            elif at_clause and tentative_dur >= (min_s + 0.6):
                _flush()
            continue

        # Keep a sentence-final word on this beat when the overrun is small.
        if at_sentence and tentative_dur <= max_s + _SOFT_OVERRUN_S:
            buf.append((tok, start, end))
            _flush()
            continue

        # The buffer already ends a sentence: start the next beat at this word.
        if _is_boundary(buf[-1][0], sentence=True) and held >= min_s:
            _flush()
            buf.append((tok, start, end))
            if last:
                _flush()
            continue

        # Mid-sentence overflow: close at the previous sentence, not mid-word.
        boundary = _sentence_end_index(buf)
        if boundary >= 0:
            prefix_dur = buf[boundary][2] - buf[0][1]
            if prefix_dur >= min_s:
                remainder = buf[boundary + 1 :]
                del buf[boundary + 1 :]
                _flush()
                buf.extend(remainder)
                buf.append((tok, start, end))
                if last:
                    _flush()
                continue

        if dur >= min_s:
            # ``dur`` includes the new word. Close on what we already held.
            if held >= min_s:
                _flush()
                buf.append((tok, start, end))
            else:
                buf.append((tok, start, end))
        else:
            buf.append((tok, start, end))
        if last:
            _flush()

    if buf:
        _flush()

    # A leftover "perfectly." belongs to the previous sentence when it fits.
    for index in range(1, len(groups)):
        take = _leading_fragment_count(groups[index])
        if not take or not groups[index - 1]:
            continue
        moved = groups[index][:take]
        extra = moved[-1][2] - groups[index - 1][0][1]
        if extra > max_s + _SOFT_OVERRUN_S:
            continue
        groups[index - 1].extend(moved)
        groups[index] = groups[index][take:]
    groups = [group for group in groups if group]

    def _as_chunk(index: int, group: list[tuple[str, float, float]]) -> AudioChunk:
        text = " ".join(item[0] for item in group).strip()
        return AudioChunk(
            index=index,
            start_s=float(group[0][1]),
            end_s=float(group[-1][2]),
            text=text,
        )

    chunks = [_as_chunk(i, group) for i, group in enumerate(groups) if group]

    # Merge a trailing micro-chunk into the previous window.
    if len(chunks) >= 2 and chunks[-1].duration_s < (min_s * 0.55):
        prev, tail = chunks[-2], chunks[-1]
        merged = AudioChunk(
            index=prev.index,
            start_s=prev.start_s,
            end_s=tail.end_s,
            text=f"{prev.text} {tail.text}".strip(),
        )
        chunks = chunks[:-2] + [merged]
        chunks = [
            AudioChunk(index=i, start_s=c.start_s, end_s=c.end_s, text=c.text)
            for i, c in enumerate(chunks)
        ]

    return chunks
