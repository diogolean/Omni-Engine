# -*- coding: utf-8 -*-
"""Two or three vertical Shorts from one horizontal episode.

A Short is a run of whole exchanges from the episode. It opens on a host
line, so the first second is a question, and closes on the guest's answer.
Windows are scored by the hook that opens them, the strongest guest stance
inside them, and the finale, then picked from different chapters. The
closing verdict stays out of every Short, so the long video keeps its ending.

``regenerate`` (the default) renders the window again at 1080x1920 in the
channel's shot-reverse-shot framing, with the episode's puppets, arena, skin
and voice files. ``cut``, and the fallback when a re-render fails, takes a
bounded slice of the master and lays it over a blurred copy of itself, so
both fighters and the subtitles stay in frame. Neither path uses ``apad``
or ``amix``.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from products.aiwake.contracts import DebateTranscript
from products.aiwake.settings import AiwakeSettings

from .ffmpeg_guard import ffmpeg_binary, run_bounded
from .metadata import quote_candidates, quote_title, short_metadata
from .text import quotable, sentences

_LOG = logging.getLogger("aiwake.longform.shorts")

_HOOK_SCORE = {
    "chokehold": 2.5,
    "callback": 2.0,
    "bookend": 2.0,
    "open": 1.5,
    "chapter": 1.0,
    "reversal": 1.0,
    "stakes": 0.5,
    "image": 0.5,
}
_STANCE_SCORE = {"crack": 3.0, "counter": 2.0, "last_word": 2.0, "reconcile": 1.5, "claim": 1.0}
_FINALE_BONUS = 2.5
_FINALE_KEY = "chokehold"
# A cut keeps up to this much of the beat after the last word.
_CUT_TAIL_S = 0.8
_DESCRIPTION_QUOTE_MAX = 200


@dataclass(frozen=True)
class Line:
    """One spoken line placed on the episode timeline."""

    turn_index: int
    role: str
    text: str
    start_s: float
    end_s: float


@dataclass(frozen=True)
class Highlight:
    """A window of whole lines worth a Short."""

    turns: tuple[int, ...]
    start_s: float
    end_s: float
    segment_index: int
    chapter: str
    score: float

    @property
    def span_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class ShortClip:
    path: Path
    start_s: float
    duration_s: float
    mode: str
    metadata: dict[str, object]


def line_timeline(utterances: Sequence[Any], timestamps: object) -> list[Line]:
    """Pair each utterance with its slot in the renderer's ``timestamps``.

    The renderer lists every spoken line once, in order. Matching on the
    text, rather than the position alone, catches a timeline from a
    different transcript. Returns ``[]`` when the two do not line up.
    """
    if not isinstance(timestamps, list) or not timestamps:
        return []
    lines: list[Line] = []
    cursor = 0
    for utterance in utterances:
        wanted = _normal(utterance.text)
        while cursor < len(timestamps):
            entry = timestamps[cursor]
            cursor += 1
            if isinstance(entry, dict) and _normal(str(entry.get("text") or "")) == wanted:
                try:
                    start, end = float(entry["start"]), float(entry["end"])
                except (KeyError, TypeError, ValueError):
                    return []
                lines.append(
                    Line(
                        turn_index=int(utterance.turn_index),
                        role=str(getattr(utterance.role, "value", utterance.role)),
                        text=str(utterance.text),
                        start_s=start,
                        end_s=end,
                    )
                )
                break
        else:
            return []
    return lines


def choose_highlights(
    lines: Sequence[Line],
    beats: Sequence[Mapping[str, Any]] | None,
    *,
    count: int,
    min_s: float,
    target_s: float,
    max_s: float,
) -> list[Highlight]:
    """The ``count`` best non-overlapping windows, in episode order.

    A window starts on a host line the director placed and ends on a guest
    answer, spans ``min_s`` to ``max_s``, and never includes the closing
    verdict. Different chapters are taken first. ``beats`` is the director's
    summary; without it every exchange scores on length alone.
    """
    by_host: dict[int, Mapping[str, Any]] = {}
    by_guest: dict[int, Mapping[str, Any]] = {}
    for beat in beats or ():
        if beat.get("host_turn") is not None:
            by_host[int(beat["host_turn"])] = beat
        if beat.get("guest_turn") is not None:
            by_guest[int(beat["guest_turn"])] = beat
    directed = bool(by_host)
    candidates: list[Highlight] = []
    for first, opener in enumerate(lines):
        if opener.role != "orchestrator":
            continue
        start_beat = by_host.get(opener.turn_index)
        if directed and start_beat is None:
            continue
        for last in range(first + 1, len(lines)):
            closer = lines[last]
            if closer.role != "target":
                continue
            if directed and closer.turn_index not in by_guest:
                continue
            span = closer.end_s - opener.start_s
            if span > max_s:
                break
            if span < min_s:
                continue
            window = lines[first : last + 1]
            candidates.append(
                Highlight(
                    turns=tuple(line.turn_index for line in window),
                    start_s=opener.start_s,
                    end_s=closer.end_s,
                    segment_index=int(start_beat.get("segment_index", -1)) if start_beat else -1,
                    chapter=str(start_beat.get("title") or "") if start_beat else "",
                    score=_score(window, start_beat, by_guest, span=span, target_s=target_s),
                )
            )
    ranked = sorted(candidates, key=lambda item: (-item.score, item.start_s))
    picked: list[Highlight] = []
    for distinct in (True, False):
        for candidate in ranked:
            if len(picked) >= count:
                break
            if any(_overlaps(candidate, chosen) for chosen in picked):
                continue
            if distinct and any(chosen.segment_index == candidate.segment_index for chosen in picked):
                continue
            picked.append(candidate)
    return sorted(picked, key=lambda item: item.start_s)


def make_shorts(
    highlights: Sequence[Highlight],
    *,
    transcript: DebateTranscript,
    lines: Sequence[Line],
    output_dir: Path,
    settings: AiwakeSettings,
    master: Path | None,
    master_duration_s: float,
    audio_by_turn: Mapping[int, Any] | None,
    skin: str,
    long_title: str,
    long_video: str,
    orchestrator: str,
    target: str,
    avoid_quotes: Sequence[str] = (),
    work_root: Path | None = None,
) -> list[ShortClip]:
    """Render or cut one Short per highlight and write its ``short_XX.json``.

    Re-renders work under ``work_root``, by default the channel's ``_tmp``
    folder for this session, which is removed afterwards.
    """
    preset = settings.long_format
    output_dir.mkdir(parents=True, exist_ok=True)
    by_turn = {line.turn_index: line for line in lines}
    used = [quote for quote in avoid_quotes if quote]
    regenerate = preset.shorts_mode == "regenerate"
    scratch = None
    if regenerate:
        from shared.paths import temp_path  # noqa: PLC0415

        scratch = Path(work_root) if work_root is not None else temp_path("aiwake", "longform_shorts", transcript.session_id)
    clips: list[ShortClip] = []
    try:
        for index, highlight in enumerate(highlights, start=1):
            clip = _make_short(
                index,
                highlight,
                transcript=transcript,
                by_turn=by_turn,
                output_dir=output_dir,
                settings=settings,
                master=master,
                master_duration_s=master_duration_s,
                audio_by_turn=audio_by_turn,
                skin=skin,
                long_title=long_title,
                long_video=long_video,
                orchestrator=orchestrator,
                target=target,
                used=used,
                scratch=scratch,
            )
            if clip is not None:
                clips.append(clip)
    finally:
        if scratch is not None and work_root is None:
            shutil.rmtree(scratch, ignore_errors=True)
    return clips


def _make_short(
    index: int,
    highlight: Highlight,
    *,
    transcript: DebateTranscript,
    by_turn: Mapping[int, Line],
    output_dir: Path,
    settings: AiwakeSettings,
    master: Path | None,
    master_duration_s: float,
    audio_by_turn: Mapping[int, Any] | None,
    skin: str,
    long_title: str,
    long_video: str,
    orchestrator: str,
    target: str,
    used: list[str],
    scratch: Path | None,
) -> ShortClip | None:
    preset = settings.long_format
    destination = output_dir / f"short_{index:02d}.mp4"
    mode = ""
    if scratch is not None:
        try:
            _regenerate(
                highlight,
                transcript=transcript,
                destination=destination,
                settings=settings,
                audio_by_turn=audio_by_turn,
                skin=skin,
                work_dir=scratch / destination.stem,
            )
            mode = "regenerated"
        except Exception as exc:  # noqa: BLE001 — a failed re-render falls back to a cut
            _LOG.error("short %d re-render failed, cutting the master instead: %s", index, exc)
    if not mode and master is not None and Path(master).is_file():
        tail = max(0.0, min(_CUT_TAIL_S, float(master_duration_s or 0.0) - highlight.end_s))
        try:
            cut_master(
                Path(master),
                destination,
                start_s=highlight.start_s,
                length_s=highlight.span_s + tail,
                width=int(preset.shorts_width),
                height=int(preset.shorts_height),
            )
            mode = "cut"
        except Exception as exc:  # noqa: BLE001 — one failed Short must not drop the others
            _LOG.error("short %d cut failed: %s", index, exc)
    if not mode:
        return None
    window = [by_turn[turn] for turn in highlight.turns if turn in by_turn]
    title_quote, description_quote, speaker = pick_quotes(
        window,
        orchestrator=orchestrator,
        target=target,
        avoid=used,
    )
    used.append(title_quote)
    meta = short_metadata(
        long_title=long_title,
        long_video=long_video,
        orchestrator=orchestrator,
        target=target,
        title_quote=title_quote,
        description_quote=description_quote,
        description_speaker=speaker,
        chapter_title=highlight.chapter or "AI Debate",
        start_s=highlight.start_s,
        duration_s=highlight.span_s,
        mode=mode,
    )
    meta["file"] = destination.name
    meta["turns"] = list(highlight.turns)
    (output_dir / f"short_{index:02d}.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return ShortClip(
        path=destination,
        start_s=highlight.start_s,
        duration_s=highlight.span_s,
        mode=mode,
        metadata=meta,
    )


def pick_quotes(
    window: Sequence[Line],
    *,
    orchestrator: str,
    target: str,
    avoid: Sequence[str] = (),
) -> tuple[str, str, str]:
    """``(title_quote, description_quote, description_speaker)`` for one Short.

    The title quotes a host question that fits the 70-character title, else
    a guest sentence that fits. The description quotes the other side, so
    the copy carries both voices. Lines in ``avoid`` (the long title, earlier
    Shorts) are skipped.
    """
    taken = {quotable(quote).lower() for quote in avoid}
    host = [line.text for line in window if line.role == "orchestrator"]
    guest = [line.text for line in window if line.role == "target"]
    title_quote, title_role = "", ""
    for role, texts in (("orchestrator", host), ("target", guest)):
        for quote in quote_candidates(texts):
            if quotable(quote).lower() in taken:
                continue
            if quote_title(quote, orchestrator, target):
                title_quote, title_role = quote, role
                break
        if title_quote:
            break
    if title_role == "orchestrator" or not title_role:
        other, speaker = guest, target
    else:
        other, speaker = host, orchestrator
    description_quote = _description_quote(other[0] if other else "")
    if not description_quote:
        speaker = orchestrator if speaker == target else target
        fallback = host if speaker == orchestrator else guest
        description_quote = _description_quote(fallback[0] if fallback else "")
    return title_quote, description_quote, speaker


def cut_master(
    master: Path,
    destination: Path,
    *,
    start_s: float,
    length_s: float,
    width: int,
    height: int,
) -> None:
    """Bounded 9:16 cut: the 16:9 slice centred over a blurred, darkened copy.

    ``-ss`` and ``-t`` sit before ``-i``, so ffmpeg decodes only the slice.
    """
    small_w = max(2, width // 8 * 2)
    small_h = max(2, height // 8 * 2)
    graph = (
        "[0:v]split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={small_w}:{small_h}:force_original_aspect_ratio=increase,"
        f"crop={small_w}:{small_h},boxblur=8:1,scale={width}:{height},eq=brightness=-0.06[bg];"
        f"[fgsrc]scale={width}:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
    )
    cmd = [
        ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, start_s):.3f}",
        "-t",
        f"{length_s:.3f}",
        "-i",
        str(master),
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-threads",
        "2",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_bounded(cmd, timeout=max(90.0, length_s * 8.0))


def _regenerate(
    highlight: Highlight,
    *,
    transcript: DebateTranscript,
    destination: Path,
    settings: AiwakeSettings,
    audio_by_turn: Mapping[int, Any] | None,
    skin: str,
    work_dir: Path,
) -> None:
    """Render the window's lines again as a native 9:16 clip."""
    from products.aiwake.animator_bridge import render_debate_animation  # noqa: PLC0415

    preset = settings.long_format
    keep = set(highlight.turns)
    utterances = [item for item in transcript.utterances if item.turn_index in keep]
    roles = {str(getattr(item.role, "value", item.role)) for item in utterances}
    if roles != {"orchestrator", "target"}:
        raise ValueError(f"window {highlight.turns} does not hold both seats")
    stem = destination.stem
    metadata = dict(transcript.metadata)
    metadata["post_type"] = "short_clip"
    window = transcript.model_copy(
        update={
            "session_id": f"{transcript.session_id}_{stem}",
            "utterances": utterances,
            "metadata": metadata,
        }
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    puppets = transcript.metadata.get("render_puppets") or {}
    try:
        rendered = render_debate_animation(
            window,
            audio_by_turn=dict(audio_by_turn or {}),
            output_dir=work_dir,
            skin=skin,
            left_puppet=puppets.get("orchestrator"),
            right_puppet=puppets.get("target"),
            width=int(preset.shorts_width),
            height=int(preset.shorts_height),
            audio_config=settings.audio,
            output_name=f"{stem}.mp4",
            enable_cta=False,
            scene=str(transcript.metadata.get("render_scene") or "random"),
            stage_mode="shot_reverse_shot",
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(rendered), str(destination))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _score(
    window: Sequence[Line],
    start_beat: Mapping[str, Any] | None,
    by_guest: Mapping[int, Mapping[str, Any]],
    *,
    span: float,
    target_s: float,
) -> float:
    score = 0.0
    if start_beat is not None:
        score += _HOOK_SCORE.get(str(start_beat.get("hook") or ""), 0.0)
    guest_beats = [by_guest[line.turn_index] for line in window if line.turn_index in by_guest]
    score += max((_STANCE_SCORE.get(str(beat.get("stance") or ""), 0.0) for beat in guest_beats), default=0.0)
    beats = [start_beat, *guest_beats] if start_beat is not None else guest_beats
    if any(str(beat.get("key") or "") == _FINALE_KEY for beat in beats):
        score += _FINALE_BONUS
    return score - 2.0 * abs(span - target_s) / max(1.0, target_s)


def _overlaps(first: Highlight, second: Highlight) -> bool:
    return first.start_s < second.end_s and second.start_s < first.end_s


def _description_quote(text: str) -> str:
    """The whole line when it is short, else its leading sentences up to 200 characters."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= _DESCRIPTION_QUOTE_MAX:
        return cleaned
    kept: list[str] = []
    for sentence in sentences(cleaned):
        if len(" ".join([*kept, sentence])) > _DESCRIPTION_QUOTE_MAX:
            break
        kept.append(sentence)
    return " ".join(kept)


def _normal(text: str) -> str:
    return " ".join((text or "").split()).lower()


__all__ = [
    "Highlight",
    "Line",
    "ShortClip",
    "choose_highlights",
    "cut_master",
    "line_timeline",
    "make_shorts",
    "pick_quotes",
]
