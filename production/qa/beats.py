# -*- coding: utf-8 -*-
"""One still per narration beat, capped at a few seconds of speech."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from production.qa.config import (
    WORDS_PER_SECOND,
    max_seconds_per_image,
    min_seconds_per_image,
)
from production.qa.prompts import extract_site, named_entities

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE = re.compile(r"(?<=[,;:])\s+")
# "perfectly. The Coso..." — the tail of the previous sentence.
_LEAD_FRAGMENT = re.compile(r"^([a-z][^.]{0,40}\.)\s+(\S.+)$")


@dataclass
class BeatPlan:
    snippets: list[str]
    durations: list[float]
    scenes: list[dict[str, Any]]
    changed: bool = False
    beats: list[dict[str, Any]] = field(default_factory=list)


def split_sentences(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in _SENTENCE.split(raw) if p.strip()]
    return parts or [raw]


def _split_long_sentence(sentence: str, duration: float, max_s: float) -> list[tuple[str, float]]:
    if duration <= max_s + 0.05:
        return [(sentence, duration)]
    clauses = [c.strip() for c in _CLAUSE.split(sentence) if c.strip()]
    pieces = clauses if len(clauses) > 1 else sentence.split()
    if len(pieces) <= 1:
        return [(sentence, duration)]
    weights = [max(1, len(p.split())) for p in pieces]
    total = float(sum(weights))
    raw = [(p, duration * w / total) for p, w in zip(pieces, weights)]
    if all(d <= max_s + 0.05 for _, d in raw):
        return raw
    # Still over the cap: pack words into windows of about max_s.
    words = sentence.split()
    slots = max(2, int(round(duration / max_s)))
    size = max(1, int(len(words) / slots))
    chunks: list[str] = []
    for i in range(0, len(words), size):
        chunks.append(" ".join(words[i : i + size]))
    span = duration / max(1, len(chunks))
    return [(c, span) for c in chunks if c]


def _attach_leading_fragments(
    groups: list[tuple[str, float]],
    *,
    max_s: float,
) -> list[tuple[str, float]]:
    """Move a lowercase sentence tail back onto the previous beat."""
    if len(groups) < 2:
        return groups
    out: list[tuple[str, float]] = [groups[0]]
    for text, dur in groups[1:]:
        match = _LEAD_FRAGMENT.match((text or "").strip())
        if not match:
            out.append((text, dur))
            continue
        lead, rest = match.group(1), match.group(2)
        prev_text, prev_dur = out[-1]
        words = max(1, len(text.split()))
        lead_dur = dur * max(1, len(lead.split())) / words
        if prev_dur + lead_dur > max_s + 0.55:
            out.append((text, dur))
            continue
        out[-1] = (f"{prev_text} {lead}".strip(), prev_dur + lead_dur)
        out.append((rest, max(0.1, dur - lead_dur)))
    return out


class TimelineError(ValueError):
    """The picture cuts do not match the voice."""


def beats_from_timestamps(
    script: str,
    word_timings: list[tuple] | None,
    *,
    narration_s: float = 0.0,
    audio_end: float | None = None,
) -> list[tuple[str, float, float]]:
    """One beat per sentence. The still stays up through the pause that follows.

    Beat 0 starts at 0, so a leading hook silence stays on the first picture.
    Each later cut is that sentence's first word. The last beat runs through
    ``audio_end`` (narration plus the silence before the CTA, or the whole
    voice when there is no CTA). A long sentence stays one beat: an extra
    clause cut would no longer land on a sentence start.
    """
    sentences = split_sentences(script)
    if not sentences:
        return []
    words = [
        (str(word or "").strip(), float(start), float(end))
        for word, start, end in (word_timings or [])
        if str(word or "").strip() and float(end) > float(start)
    ]
    end_at = float(audio_end if audio_end is not None else 0.0)
    if end_at <= 0.0:
        end_at = float(narration_s or 0.0)
    if not words:
        duration = end_at or max(1.0, len(sentences) * 4.0)
        weights = [max(1, len(sentence.split())) for sentence in sentences]
        total = float(sum(weights)) or 1.0
        cursor = 0.0
        rows: list[tuple[str, float, float]] = []
        for sentence, weight in zip(sentences, weights):
            span = duration * weight / total
            rows.append((sentence, cursor, cursor + span))
            cursor += span
        if rows and end_at > rows[-1][2]:
            text, start, _end = rows[-1]
            rows[-1] = (text, start, end_at)
        return rows
    cursor_i = 0
    aligned: list[tuple[str, float]] = []
    for sentence in sentences:
        if cursor_i >= len(words):
            break
        need = max(1, len(sentence.split()))
        start = words[cursor_i][1]
        taken = 0
        while cursor_i < len(words) and taken < need:
            token, _start, _token_end = words[cursor_i]
            cursor_i += 1
            taken += 1
            stripped = token.rstrip("\"'")
            if stripped.endswith((".", "!", "?")):
                break
        aligned.append((sentence, float(start)))
    if not aligned:
        return []
    if end_at <= 0.0:
        end_at = float(words[-1][2])
    cuts = [0.0]
    for _text, start in aligned[1:]:
        nxt = float(start)
        if nxt < cuts[-1] + 0.05:
            nxt = cuts[-1] + 0.05
        cuts.append(nxt)
    if end_at < cuts[-1] + 0.05:
        end_at = cuts[-1] + 0.2
    points = cuts + [end_at]
    return [
        (text, points[index], points[index + 1])
        for index, (text, _start) in enumerate(aligned)
    ]


def check_timeline(
    durations: list[float],
    sentence_starts: list[float],
    audio_len: float,
    *,
    tolerance: float = 0.3,
) -> None:
    """Fail unless the video matches the voice and cuts land on the sentences.

    The first cut is 0 (the hook silence belongs to the first still). Each
    later narration cut is that sentence's first word. A CTA beat may follow
    the narration cuts; it is part of ``audio_len`` and is not a sentence.
    """
    durs = [float(item) for item in durations]
    starts = [float(item) for item in sentence_starts]
    if not durs or not starts:
        raise TimelineError("empty timeline")
    total = float(sum(durs))
    limit = float(tolerance)
    if abs(total - float(audio_len)) >= limit:
        raise TimelineError(
            f"video {total:.3f}s vs audio {float(audio_len):.3f}s"
        )
    cursor = 0.0
    for index, start in enumerate(starts):
        if index >= len(durs):
            raise TimelineError(f"missing beat for sentence {index}")
        if abs(cursor - start) >= limit:
            raise TimelineError(
                f"beat {index} cut {cursor:.3f}s vs sentence {start:.3f}s"
            )
        cursor += durs[index]


def seal_sentence_timeline(
    snippets: list[str],
    durations: list[float],
    sentence_starts: list[float],
    audio_len: float,
    cta_text: str = "",
) -> tuple[list[str], list[float]]:
    """Append the CTA through the end of the voice, then require a match.

    Narration beats already tile ``[0, narration + silence]``. The CTA beat
    is whatever is left until ``audio_len``, so the closing line is not
    peeled off the last narration still.
    """
    snips = [str(item or "") for item in snippets]
    durs = [float(item) for item in durations]
    text = " ".join((cta_text or "").split())
    already = bool(text and snips) and " ".join(snips[-1].split()) == text
    remain = float(audio_len) - sum(durs)
    if text and not already and remain > 0.05:
        snips.append(text)
        durs.append(remain)
    elif remain > 0.05 and durs:
        # The spoken line already is the CTA, or the voice is simply longer
        # than the slots. Stretch the last hold so the mux does not crash.
        durs[-1] = durs[-1] + remain
    check_timeline(durs, sentence_starts, audio_len)
    return snips, durs


def pack_sentences(
    text: str,
    duration_s: float,
    *,
    min_s: float | None = None,
    max_s: float | None = None,
) -> list[tuple[str, float]]:
    """Group sentences so each group stays within ``max_s``."""
    max_s = float(max_s if max_s is not None else max_seconds_per_image())
    min_s = float(min_s if min_s is not None else min_seconds_per_image())
    min_s = min(min_s, max_s)
    sentences = split_sentences(text)
    if not sentences:
        return []
    duration_s = max(0.1, float(duration_s or 0.0))
    weights = [max(1, len(s.split())) for s in sentences]
    total_w = float(sum(weights))
    raw = [duration_s * w / total_w for w in weights]
    pieces: list[tuple[str, float]] = []
    for sentence, dur in zip(sentences, raw):
        pieces.extend(_split_long_sentence(sentence, dur, max_s))

    groups: list[tuple[str, float]] = []
    buf: list[str] = []
    buf_d = 0.0
    for sentence, dur in pieces:
        if buf and buf_d + dur > max_s + 0.05:
            groups.append((" ".join(buf), buf_d))
            buf, buf_d = [sentence], dur
        else:
            buf.append(sentence)
            buf_d += dur
    if buf:
        groups.append((" ".join(buf), buf_d))
    groups = _attach_leading_fragments(groups, max_s=max_s)
    if len(groups) >= 2 and groups[-1][1] < min_s * 0.55:
        prev_t, prev_d = groups[-2]
        tail_t, tail_d = groups[-1]
        if prev_d + tail_d <= max_s + 0.05:
            groups[-2] = (f"{prev_t} {tail_t}".strip(), prev_d + tail_d)
            groups.pop()
    return groups or [(text.strip(), duration_s)]


def _scene_for(
    text: str,
    start_s: float,
    end_s: float,
    *,
    index: int,
    inherited: dict[str, Any] | None,
    previous_spoken: str = "",
    next_spoken: str = "",
) -> dict[str, Any]:
    base = inherited or {}
    anchors = str(base.get("domain_anchors") or "")
    entities = named_entities(text, anchors)
    site = extract_site(text, anchors)
    entity_bit = (", ".join(entities[:4])) if entities else ""
    try:
        from modules.reel_grounding.prompt import ground_image_prompt

        prompt = ground_image_prompt(
            topic=anchors or text,
            script=anchors,
            spoken=text,
            domain_anchors=anchors,
            previous_spoken=previous_spoken,
            next_spoken=next_spoken,
        )
    except Exception:  # noqa: BLE001
        site_bit = f" The place is context only: {site}." if site else ""
        name_bit = f" Named entity: {entity_bit}." if entity_bit else ""
        prompt = (
            f"PRIMARY SUBJECT: {text}. "
            f"Narration sentence: {text}.{name_bit}{site_bit} No extra landmarks. No text."
        ).strip()
    return {
        "scene_index": index,
        "spoken_text": text,
        "image_generation_prompt": prompt,
        "negative_prompt": str(base.get("negative_prompt") or ""),
        "banned_subjects": list(base.get("banned_subjects") or []),
        "domain_anchors": entity_bit or str(base.get("domain_anchors") or ""),
        "start_time": round(start_s, 3),
        "end_time": round(end_s, 3),
    }


def _within_cap(durations: list[float], max_s: float) -> bool:
    return bool(durations) and max(durations) <= max_s + 1.0


def refine_reel_beats(
    *,
    snippets: list[str] | None,
    durations: list[float] | None = None,
    word_timings: list[tuple] | None = None,
    narration_s: float = 0.0,
    scenes: list[dict[str, Any]] | None = None,
    topic: str = "",
) -> BeatPlan:
    """Split acts that hold more speech than ``REEL_QA_MAX_SECONDS_PER_IMAGE``.

    Word timestamps win when a hold is too long. Otherwise sentences are
    packed by estimated speaking time. Plans already inside the cap are
    returned unchanged.
    """
    texts = [str(s or "").strip() for s in (snippets or [])]
    texts = [s for s in texts if s] or ([topic.strip()] if topic.strip() else [])
    durs = [float(d) for d in (durations or []) if float(d) > 0]
    max_s = max_seconds_per_image()
    min_s = min_seconds_per_image()
    inherited = scenes[0] if scenes else {}
    if topic and isinstance(inherited, dict):
        inherited = dict(inherited)
        inherited.setdefault("domain_anchors", topic)

    # One sentence per beat is already the shot list. Do not pack those
    # sentences back into a 3–5 s window.
    if (
        texts
        and durs
        and len(durs) == len(texts)
        and all(len(split_sentences(item)) <= 1 for item in texts)
    ):
        return BeatPlan(
            snippets=list(texts),
            durations=list(durs),
            scenes=list(scenes or []),
            changed=False,
        )

    if texts and durs and len(durs) == len(texts) and _within_cap(durs, max_s):
        return BeatPlan(
            snippets=list(texts),
            durations=list(durs),
            scenes=list(scenes or []),
            changed=False,
        )

    if word_timings and (not durs or not _within_cap(durs or [max_s + 2], max_s)):
        try:
            from production.media.audio_chunker import chunk_word_timings

            chunks = chunk_word_timings(word_timings, min_s=min(min_s, 3.0), max_s=max_s)
        except Exception:  # noqa: BLE001
            chunks = []
        if chunks:
            raw = [float(c.duration_s) for c in chunks]
            scale_to = float(narration_s or 0.0) or sum(raw) or 1.0
            factor = scale_to / (sum(raw) or 1.0)
            scaled = [d * factor for d in raw]
            tail = 0.0
            if durs and sum(durs) > scale_to + 0.4:
                tail = sum(durs) - scale_to
            if scaled and tail > 0:
                scaled[-1] += tail
            built = []
            cursor = 0.0
            for i, chunk in enumerate(chunks):
                end = cursor + scaled[i]
                parent = inherited if isinstance(inherited, dict) else {}
                previous = chunks[i - 1].text if i else ""
                following = chunks[i + 1].text if i + 1 < len(chunks) else ""
                built.append(
                    _scene_for(
                        chunk.text,
                        cursor,
                        end,
                        index=i,
                        inherited=parent,
                        previous_spoken=previous,
                        next_spoken=following,
                    )
                )
                cursor = end
            return BeatPlan(
                snippets=[c.text for c in chunks],
                durations=scaled,
                scenes=built,
                changed=True,
                beats=built,
            )

    if not texts:
        return BeatPlan(snippets=[], durations=[], scenes=[], changed=False)

    if not durs or len(durs) != len(texts):
        durs = [max(min_s, len(s.split()) / WORDS_PER_SECOND) for s in texts]

    packed_text: list[str] = []
    packed_dur: list[float] = []
    for text, dur in zip(texts, durs):
        if dur <= max_s + 1.0 and len(split_sentences(text)) <= 1:
            packed_text.append(text)
            packed_dur.append(dur)
            continue
        for piece, piece_d in pack_sentences(text, dur, min_s=min_s, max_s=max_s):
            packed_text.append(piece)
            packed_dur.append(piece_d)

    changed = packed_text != texts or len(packed_dur) != len(durs)
    built_scenes: list[dict[str, Any]] = []
    cursor = 0.0
    for i, (text, dur) in enumerate(zip(packed_text, packed_dur)):
        parent = scenes[i] if scenes and i < len(scenes) else inherited
        previous = packed_text[i - 1] if i else ""
        following = packed_text[i + 1] if i + 1 < len(packed_text) else ""
        built_scenes.append(
            _scene_for(
                text,
                cursor,
                cursor + dur,
                index=i,
                inherited=parent if isinstance(parent, dict) else {},
                previous_spoken=previous,
                next_spoken=following,
            )
        )
        cursor += dur
    if not changed:
        return BeatPlan(snippets=texts, durations=durs, scenes=list(scenes or []), changed=False)
    return BeatPlan(
        snippets=packed_text,
        durations=packed_dur,
        scenes=built_scenes,
        changed=True,
        beats=built_scenes,
    )
