# -*- coding: utf-8 -*-
"""Assemble the YouTube package after the renderer returns.

Chapters start where each chapter's first question starts on the rendered
timeline. The title quotes the cold open when it fits (else the line that
best stands alone), the description quotes the guest's final round, the
Shorts come from the strongest exchanges, the bill comes from the tokens the
room recorded, and the pre-publish gate judges all of it.
Everything lands in the episode folder: ``metadata.json`` and ``shorts/``.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from products.aiwake.pipeline import PipelineResult
from products.aiwake.settings import AiwakeSettings

from .cost import actual_episode_cost, estimate_episode_cost
from .metadata import (
    ChapterMark,
    build_youtube_package,
    chapter_problems,
    plan_chapter_marks,
    youtube_chapters,
)
from .plan import END_HOLD_S, EpisodePlan
from .qa import episode_problems, episode_warnings, evaluate_package, probe_media, voice_problems
from .shorts import choose_highlights, line_timeline, make_shorts
from .text import sentences

_LOG = logging.getLogger("aiwake.longform")
# Headroom under the Shorts cap for the re-render's closing hold and the encoder.
_SHORT_HEADROOM_S = END_HOLD_S + 1.0
# Guest answers that are punchy enough to be a title: confessions and comebacks.
_TITLE_STANCES = ("crack", "counter")
_GUEST_QUOTE_MIN_WORDS = 5
_TITLE_QUOTE = re.compile(r'^"(.+)" \| ')


def finalize_longform(
    result: PipelineResult,
    plan: EpisodePlan,
    settings: AiwakeSettings,
    *,
    offline: bool,
    output_dir: Path,
    orchestrator: str,
    target: str,
    shorts_count: int | None = None,
    require_media: bool = True,
    audio_by_turn: Mapping[int, Any] | None = None,
    skin: str = "v2",
    work_root: Path | None = None,
) -> PipelineResult:
    """Write chapters and copy, make the Shorts, price the run, and gate it.

    ``require_media`` is false for a transcript-only run: nothing is probed
    or rendered, and the gate judges the story and the chapters only.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    preset = settings.long_format
    count = max(2, min(3, int(shorts_count if shorts_count is not None else preset.shorts_count)))
    transcript = result.transcript
    episode = transcript.metadata.get("episode")
    episode = episode if isinstance(episode, dict) else None
    beats: list[Mapping[str, Any]] = list((episode or {}).get("beats") or [])
    existing = _read_json(result.metadata_path)

    long_probe = probe_media(result.video_path) if require_media and result.video_path else None
    duration_s = (
        (long_probe.duration_s if long_probe else None)
        or _float(existing.get("duration_s"))
        or _float((episode or {}).get("estimated_duration_s"))
        or float(plan.target_duration_s)
    )
    lines = line_timeline(transcript.utterances, existing.get("timestamps"))
    starts = {line.turn_index: line.start_s for line in lines}
    chapters = youtube_chapters(_chapter_marks(plan, beats, starts), duration_s=duration_s)

    texts = {item.turn_index: item.text for item in transcript.utterances}
    hook = _cold_open(transcript, beats, texts)
    package = build_youtube_package(
        hook=hook,
        topic=transcript.topic,
        orchestrator=orchestrator,
        target=target,
        plan=plan,
        chapters=chapters,
        duration_s=duration_s,
        opening_category=str(transcript.metadata.get("opening_category") or ""),
        spoken=" ".join(texts.values()),
        title_lines=_title_lines(beats, texts),
        guest_quotes=_finale_quotes(beats, texts),
    )
    estimate = estimate_episode_cost(plan, preset, orchestrator=orchestrator, target=target, offline=offline)
    actual = actual_episode_cost(transcript.metadata.get("token_usage"), preset, offline=offline)

    video_name = result.video_path.name if result.video_path is not None else ""
    shorts_dir = output_dir / "shorts"
    clips = []
    if require_media and lines:
        highlights = choose_highlights(
            lines,
            beats,
            count=count,
            min_s=float(preset.shorts_min_s),
            target_s=float(preset.shorts_target_s),
            max_s=float(preset.shorts_max_s) - _SHORT_HEADROOM_S,
        )
        title_quote = _TITLE_QUOTE.match(package.title)
        try:
            clips = make_shorts(
                highlights,
                transcript=transcript,
                lines=lines,
                output_dir=shorts_dir,
                settings=settings,
                master=result.video_path,
                master_duration_s=duration_s,
                audio_by_turn=audio_by_turn,
                skin=skin,
                long_title=package.title,
                long_video=video_name,
                orchestrator=orchestrator,
                target=target,
                avoid_quotes=[title_quote.group(1)] if title_quote else [],
                work_root=work_root,
            )
        except Exception as exc:  # noqa: BLE001 — a Shorts failure must not drop the episode
            _LOG.error("Shorts failed: %s", exc)
    short_probes = [probe_media(clip.path) for clip in clips] if require_media else []

    problems = [
        *episode_problems(end_reason=result.end_reason, episode=episode),
        *(voice_problems(transcript.utterances, audio_by_turn) if require_media else []),
        *chapter_problems(chapters, duration_s=duration_s),
    ]
    warnings = episode_warnings(episode, max_hook_gap_s=_hook_gap(beats, starts))
    if preset.shorts_mode == "regenerate":
        warnings.extend(
            f"{clip.path.name} was cut from the master because its re-render failed"
            for clip in clips
            if clip.mode == "cut"
        )
    report = evaluate_package(
        long_probe=long_probe,
        short_probes=short_probes,
        preset=preset,
        target_s=plan.target_duration_s,
        shorts_expected=count,
        require_media=require_media,
        problems=problems,
        warnings=warnings,
    )

    payload = dict(existing)
    payload.update(
        {
            "session_id": transcript.session_id,
            "post_type": "long_format",
            "title": package.title,
            "description": package.description,
            "tags": package.tags,
            "hashtags": package.hashtags,
            "chapters": package.to_dict()["chapters"],
            "resolution": [plan.resolution[0], plan.resolution[1]],
            "target_duration_s": plan.target_duration_s,
            "duration_s": round(duration_s, 3),
            "topic": transcript.topic,
            "hook": hook,
            "video": video_name or None,
            "models": {"orchestrator": orchestrator, "target": target},
            "episode_plan": plan.to_dict(),
            "episode": episode,
            "cost_estimate": estimate.to_dict(),
            "cost_actual": actual.to_dict(),
            "shorts": [clip.metadata for clip in clips],
            "qa": report.to_dict(),
        }
    )
    payload.setdefault("transcript", transcript.to_script())
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if clips:
        (shorts_dir / "manifest.json").write_text(
            json.dumps([clip.metadata for clip in clips], indent=2) + "\n",
            encoding="utf-8",
        )
    return replace(
        result,
        metadata_path=metadata_path,
        shorts=tuple(clip.path for clip in clips),
        qa_passed=report.passed,
        qa_failures=tuple(report.failures),
        qa_warnings=tuple(report.warnings),
        cost_estimate_usd=estimate.usd,
        cost_actual_usd=actual.usd,
    )


def _chapter_marks(
    plan: EpisodePlan,
    beats: Sequence[Mapping[str, Any]],
    starts: Mapping[int, float],
) -> list[ChapterMark]:
    """One mark per chapter the episode reached, at its first host line."""
    if not beats:
        return plan_chapter_marks(plan)
    marks: list[ChapterMark] = []
    seen: set[int] = set()
    for beat in beats:
        index = int(beat.get("segment_index", -1))
        if index in seen:
            continue
        seen.add(index)
        turn = beat.get("host_turn")
        start = starts.get(int(turn)) if turn is not None else None
        if start is None:
            start = float(beat.get("start_s") or 0.0)
        marks.append(ChapterMark(start, str(beat.get("title") or ""), str(beat.get("kind") or "round")))
    return marks


def _cold_open(transcript: Any, beats: Sequence[Mapping[str, Any]], texts: Mapping[int, str]) -> str:
    for beat in beats:
        if beat.get("kind") == "intro" and beat.get("host_turn") in texts:
            return texts[beat["host_turn"]]
    first = next((item for item in transcript.utterances if item.role.value == "orchestrator"), None)
    return first.text if first is not None else ""


def _title_lines(beats: Sequence[Mapping[str, Any]], texts: Mapping[int, str]) -> list[str]:
    """Lines the title may quote after the cold open: every round question, then
    the guest's cracks and comebacks. The verdict exchange is left out."""
    rounds = [beat for beat in beats if beat.get("kind") == "round"]
    host = [texts[beat["host_turn"]] for beat in rounds if beat.get("host_turn") in texts]
    guest = [
        texts[beat["guest_turn"]]
        for beat in rounds
        if beat.get("stance") in _TITLE_STANCES and beat.get("guest_turn") in texts
    ]
    return host + guest


def _finale_quotes(beats: Sequence[Mapping[str, Any]], texts: Mapping[int, str]) -> list[str]:
    """Whole guest sentences from the end of the fight: every crack, then the last word."""
    found: list[str] = []
    for stance in ("crack", "last_word"):
        for beat in beats:
            if beat.get("stance") != stance or beat.get("guest_turn") not in texts:
                continue
            found.extend(
                sentence
                for sentence in sentences(texts[beat["guest_turn"]])
                if len(sentence.split()) >= _GUEST_QUOTE_MIN_WORDS and '"' not in sentence
            )
    return found


def _hook_gap(beats: Sequence[Mapping[str, Any]], starts: Mapping[int, float]) -> float | None:
    """Longest stretch between hooks on the rendered timeline, or None without one."""
    times = sorted(
        starts[int(beat["host_turn"])]
        for beat in beats
        if beat.get("hook") and beat.get("host_turn") is not None and int(beat["host_turn"]) in starts
    )
    if len(times) < 2:
        return None
    return max(later - earlier for earlier, later in zip(times, times[1:]))


def _read_json(path: Path | None) -> dict:
    if path is None or not Path(path).is_file():
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


__all__ = ["finalize_longform"]
