# -*- coding: utf-8 -*-
"""Final MP4 gate: frame vs subtitle, audio present, loudness, duration."""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from modules.reel_qa.config import frame_samples, min_duration_s, min_mean_volume_db
from modules.reel_qa.relevance import verdict_passed

_LOG = logging.getLogger(__name__)

_MEAN_DB = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_MAX_DB = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


@dataclass(frozen=True)
class SubtitleCue:
    text: str
    start_s: float
    end_s: float

    @property
    def midpoint_s(self) -> float:
        return (float(self.start_s) + float(self.end_s)) / 2.0


def cues_from_word_timings(
    word_timings: list[tuple] | None,
    *,
    words_per_phrase: int = 4,
) -> list[SubtitleCue]:
    words = []
    for row in word_timings or []:
        if not row or len(row) < 3:
            continue
        token = str(row[0] or "").strip()
        try:
            start, end = float(row[1]), float(row[2])
        except (TypeError, ValueError):
            continue
        if token and end > start:
            words.append((token, start, end))
    if not words:
        return []
    size = max(1, int(words_per_phrase))
    cues: list[SubtitleCue] = []
    for i in range(0, len(words), size):
        group = words[i : i + size]
        text = " ".join(w[0] for w in group).strip()
        if text:
            cues.append(SubtitleCue(text=text, start_s=group[0][1], end_s=group[-1][2]))
    return cues


def cues_from_beats(beats: list[dict[str, Any]] | None) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    for beat in beats or []:
        text = str(beat.get("text") or beat.get("spoken_text") or "").strip()
        try:
            start_raw = beat.get("start_s")
            if start_raw is None:
                start_raw = beat.get("start_time") or 0.0
            end_raw = beat.get("end_s")
            if end_raw is None:
                end_raw = beat.get("end_time") or 0.0
            start = float(start_raw)
            end = float(end_raw)
        except (TypeError, ValueError):
            start, end = 0.0, 0.0
        if text and end > start:
            cues.append(SubtitleCue(text=text, start_s=start, end_s=end))
    return cues


def select_cues(cues: list[SubtitleCue], limit: int | None = None) -> list[SubtitleCue]:
    cap = max(1, int(limit if limit is not None else frame_samples()))
    if len(cues) <= cap:
        return list(cues)
    if cap == 1:
        return [cues[len(cues) // 2]]
    picked: list[SubtitleCue] = []
    for i in range(cap):
        idx = int(round(i * (len(cues) - 1) / (cap - 1)))
        cue = cues[idx]
        if not picked or picked[-1] is not cue:
            picked.append(cue)
    return picked


def probe_media(path: Path | str) -> dict[str, Any]:
    """ffprobe duration + audio stream, then ffmpeg mean loudness."""
    video = Path(path)
    empty = {
        "error": "missing_video",
        "duration_s": 0.0,
        "has_audio": False,
        "mean_volume_db": None,
        "max_volume_db": None,
    }
    if not video.is_file():
        return empty
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration:stream=codec_type",
                "-of", "json",
                str(video),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {**empty, "error": f"probe_unavailable:{type(exc).__name__}"}
    if probe.returncode != 0:
        return {**empty, "error": "probe_failed"}
    import json

    try:
        payload = json.loads(probe.stdout or "{}")
    except json.JSONDecodeError:
        return {**empty, "error": "probe_unparseable"}
    try:
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    streams = payload.get("streams") or []
    has_audio = any(str(s.get("codec_type")) == "audio" for s in streams if isinstance(s, dict))
    mean_db = None
    max_db = None
    if has_audio:
        try:
            loud = subprocess.run(
                ["ffmpeg", "-i", str(video), "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "error": f"loudness_unavailable:{type(exc).__name__}",
                "duration_s": duration,
                "has_audio": True,
                "mean_volume_db": None,
                "max_volume_db": None,
            }
        blob = (loud.stderr or "") + "\n" + (loud.stdout or "")
        mean_match = _MEAN_DB.search(blob)
        max_match = _MAX_DB.search(blob)
        if mean_match:
            mean_db = float(mean_match.group(1))
        if max_match:
            max_db = float(max_match.group(1))
    return {
        "error": "",
        "duration_s": duration,
        "has_audio": has_audio,
        "mean_volume_db": mean_db,
        "max_volume_db": max_db,
    }


def extract_frame(video: Path, at_s: float, dest: Path) -> Path | None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", f"{max(0.0, float(at_s)):.3f}",
                "-i", str(video),
                "-frames:v", "1",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not dest.is_file():
        return None
    return dest


def decide_mp4_gate(
    probe: dict[str, Any],
    frames: list[dict[str, Any]] | None,
    *,
    min_duration: float | None = None,
    min_mean_db: float | None = None,
) -> dict[str, Any]:
    """Pure pass/fail from a probe and per-frame scores. Frame rows already carry ``passed``."""
    reasons: list[str] = []
    err = str((probe or {}).get("error") or "")
    if err:
        reasons.append(err)
    duration = float((probe or {}).get("duration_s") or 0.0)
    floor = float(min_duration if min_duration is not None else min_duration_s())
    if duration < floor:
        reasons.append("duration_below_minimum")
    if not (probe or {}).get("has_audio"):
        reasons.append("audio_missing")
    else:
        mean = (probe or {}).get("mean_volume_db")
        if mean is None:
            reasons.append("loudness_unmeasured")
        elif float(mean) < float(min_mean_db if min_mean_db is not None else min_mean_volume_db()):
            reasons.append("audio_too_quiet")
    frame_rows = list(frames or [])
    if not frame_rows:
        reasons.append("no_frame_samples")
    elif any(not row.get("passed") for row in frame_rows):
        reasons.append("frame_subtitle_mismatch")
    unique: list[str] = []
    for reason in reasons:
        if reason and reason not in unique:
            unique.append(reason)
    return {
        "passed": not unique,
        "reasons": unique,
        "duration_s": duration,
        "has_audio": bool((probe or {}).get("has_audio")),
        "mean_volume_db": (probe or {}).get("mean_volume_db"),
        "max_volume_db": (probe or {}).get("max_volume_db"),
        "min_duration_s": floor,
        "frames": frame_rows,
    }


def gate_final_mp4(
    video_path: Path | str,
    cues: list[SubtitleCue] | None,
    *,
    probe_fn: Callable[[Path], dict[str, Any]] | None = None,
    evaluate_fn: Callable[..., dict[str, Any]] | None = None,
    frame_fn: Callable[[Path, float, Path], Path | None] | None = None,
    work_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Sample frames at subtitle midpoints and score them against the subtitle."""
    video = Path(video_path)
    probe = (probe_fn or probe_media)(video)
    chosen = select_cues(list(cues or []))
    frames: list[dict[str, Any]] = []
    if not chosen:
        return decide_mp4_gate(probe, frames)
    judge = evaluate_fn
    if judge is None:
        from modules.reel_qa.relevance import evaluate_chunk_relevance

        judge = evaluate_chunk_relevance
    grab = frame_fn or extract_frame
    folder = Path(work_dir) if work_dir else video.parent / f"{video.stem}_qa_frames"
    for i, cue in enumerate(chosen):
        text = (cue.text or "").strip()
        dest = folder / f"frame_{i:02d}.jpg"
        shot = grab(video, cue.midpoint_s, dest) if text else None
        if not text or shot is None:
            frames.append(
                {
                    "index": i,
                    "text": text,
                    "at_s": round(cue.midpoint_s, 3),
                    "passed": False,
                    "score": 0.0,
                    "reason": "frame_extract_failed" if text else "empty_subtitle",
                }
            )
            continue
        verdict = judge(shot, text, text) or {}
        passed = verdict_passed(verdict) and verdict.get("qa_status") != "unavailable"
        frames.append(
            {
                "index": i,
                "text": text,
                "at_s": round(cue.midpoint_s, 3),
                "passed": bool(passed),
                "score": verdict.get("relevance_score"),
                "reason": verdict.get("rejection_reason"),
                "qa_status": verdict.get("qa_status"),
                "frame_path": str(shot),
            }
        )
        _LOG.info(
            "MP4 frame | t=%.2f passed=%s score=%s text=%.48s",
            cue.midpoint_s,
            passed,
            verdict.get("relevance_score"),
            text,
        )
    return decide_mp4_gate(probe, frames)
