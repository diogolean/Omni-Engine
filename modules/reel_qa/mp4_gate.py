# -*- coding: utf-8 -*-
"""Final MP4 gate: frame vs subtitle, audio present, loudness, duration."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from modules.reel_qa.config import frame_samples, min_duration_s, min_mean_volume_db
from modules.reel_qa.relevance import verdict_passed

_LOG = logging.getLogger(__name__)

_MEAN_DB = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_MAX_DB = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_AUDIO_STREAM = re.compile(
    r"Stream\s+#\d+:\d+(?:\[[^\]]+\])?(?:\([^)]+\))?:\s*Audio:",
    re.IGNORECASE,
)


def ffmpeg_executable() -> str | None:
    """``FFMPEG_BINARY``, else ffmpeg on PATH, else imageio-ffmpeg."""
    override = (os.getenv("FFMPEG_BINARY") or "").strip()
    if override and Path(override).is_file():
        return override
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None
    if exe and Path(exe).is_file():
        return str(exe)
    return None


def ffprobe_executable() -> str | None:
    """``FFPROBE_BINARY``, ffprobe on PATH, or a sibling of ffmpeg."""
    override = (os.getenv("FFPROBE_BINARY") or "").strip()
    if override and Path(override).is_file():
        return override
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return None
    parent = Path(ffmpeg).parent
    for name in ("ffprobe.exe", "ffprobe"):
        sibling = parent / name
        if sibling.is_file():
            return str(sibling)
    return None


def _technical_probe_error(error: str) -> bool:
    err = (error or "").strip()
    return err.startswith("probe_unavailable") or err in {
        "probe_failed",
        "probe_unparseable",
    }


def _duration_from_banner(blob: str) -> float:
    match = _DURATION.search(blob or "")
    if not match:
        return 0.0
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600.0 + minutes * 60.0 + seconds


def _loudness(ffmpeg: str, video: Path) -> tuple[float | None, float | None, str]:
    try:
        loud = subprocess.run(
            [ffmpeg, "-i", str(video), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, None, f"loudness_unavailable:{type(exc).__name__}"
    blob = (loud.stderr or "") + "\n" + (loud.stdout or "")
    mean_match = _MEAN_DB.search(blob)
    max_match = _MAX_DB.search(blob)
    mean_db = float(mean_match.group(1)) if mean_match else None
    max_db = float(max_match.group(1)) if max_match else None
    return mean_db, max_db, ""


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


def _empty_probe(error: str) -> dict[str, Any]:
    return {
        "error": error,
        "duration_s": 0.0,
        "has_audio": False,
        "mean_volume_db": None,
        "max_volume_db": None,
    }


def _probe_ffprobe(binary: str, video: Path) -> dict[str, Any] | None:
    """JSON probe. ``None`` means the caller should try the ffmpeg banner."""
    try:
        probe = subprocess.run(
            [
                binary,
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
    except (OSError, subprocess.TimeoutExpired):
        return None
    if probe.returncode != 0:
        return None
    import json

    try:
        payload = json.loads(probe.stdout or "{}")
    except json.JSONDecodeError:
        return None
    try:
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    streams = payload.get("streams") or []
    has_audio = any(
        str(stream.get("codec_type")) == "audio"
        for stream in streams
        if isinstance(stream, dict)
    )
    return {
        "error": "",
        "duration_s": duration,
        "has_audio": has_audio,
        "mean_volume_db": None,
        "max_volume_db": None,
    }


def _probe_ffmpeg_banner(binary: str, video: Path) -> dict[str, Any] | None:
    """Parse ``ffmpeg -i`` stderr. imageio-ffmpeg builds often have no ffprobe."""
    try:
        proc = subprocess.run(
            [binary, "-hide_banner", "-i", str(video)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    blob = (proc.stderr or "") + "\n" + (proc.stdout or "")
    if "Duration:" not in blob:
        return None
    return {
        "error": "",
        "duration_s": _duration_from_banner(blob),
        "has_audio": bool(_AUDIO_STREAM.search(blob)),
        "mean_volume_db": None,
        "max_volume_db": None,
    }


def probe_media(path: Path | str) -> dict[str, Any]:
    """Duration, audio stream, and mean loudness.

    Uses ffprobe when it is on PATH. Otherwise parses ``ffmpeg -i`` and
    ``volumedetect`` from PATH or the imageio-ffmpeg binary. A missing
    binary is ``probe_unavailable``, not a silent reel.
    """
    video = Path(path)
    if not video.is_file():
        return _empty_probe("missing_video")
    measured: dict[str, Any] | None = None
    ffprobe = ffprobe_executable()
    if ffprobe:
        measured = _probe_ffprobe(ffprobe, video)
    ffmpeg = ffmpeg_executable()
    if measured is None and ffmpeg:
        measured = _probe_ffmpeg_banner(ffmpeg, video)
    if measured is None:
        return _empty_probe("probe_unavailable:FileNotFoundError")
    if measured.get("has_audio") and ffmpeg:
        mean_db, max_db, loud_err = _loudness(ffmpeg, video)
        measured["mean_volume_db"] = mean_db
        measured["max_volume_db"] = max_db
        if loud_err:
            measured["error"] = loud_err
    return measured


def extract_frame(video: Path, at_s: float, dest: Path) -> Path | None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [
                ffmpeg, "-y",
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
    duration = float((probe or {}).get("duration_s") or 0.0)
    floor = float(min_duration if min_duration is not None else min_duration_s())
    # No binary, or a probe that returned nothing usable. Do not invent
    # "too short" / "no audio" / "subtitle mismatch" from the missing data.
    if _technical_probe_error(err):
        return {
            "passed": False,
            "reasons": [err],
            "duration_s": duration,
            "has_audio": bool((probe or {}).get("has_audio")),
            "mean_volume_db": (probe or {}).get("mean_volume_db"),
            "max_volume_db": (probe or {}).get("max_volume_db"),
            "min_duration_s": floor,
            "frames": list(frames or []),
            "technical_failure": True,
        }
    if err:
        reasons.append(err)
    if duration < floor:
        reasons.append("duration_below_minimum")
    narration_end = (probe or {}).get("narration_end_s")
    if narration_end is not None and float(narration_end) > 0.5:
        if duration < float(narration_end) - 0.3:
            reasons.append("narration_truncated")
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
    else:
        technical = {"black", "flat", "glitch", "overlay"}
        semantic = False
        for row in frame_rows:
            if row.get("passed"):
                continue
            reason = str(row.get("reason") or "")
            if reason in technical:
                label = f"frame_{reason}"
                if label not in reasons:
                    reasons.append(label)
            else:
                semantic = True
        if semantic:
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


def _same_approved_still(frame: Path, still: str) -> bool:
    """True when the compiled frame is the beat's still, Ken Burns included.

    A push-in, a darker grade, and the subtitle crop change the pixels.
    The match drops the average brightness and tries the whole still, its
    upper area, and a mild inset. A different picture stays below the line.
    """
    left = _center_luma(frame)
    if left is None:
        return False
    scores = []
    samples = (
        _center_luma(still),
        _center_luma(still, box=(0.0, 0.0, 1.0, 0.76)),
        _center_luma(still, box=(0.08, 0.06, 0.92, 0.90)),
    )
    for sample in samples:
        if sample is None:
            continue
        scores.append(_correlated(left, sample))
    return bool(scores) and max(scores) >= 0.68


def _center_luma(
    path: Path | str,
    box: tuple[float, float, float, float] | None = None,
) -> list[int] | None:
    try:
        with Image.open(path) as opened:
            image = opened.convert("L")
    except (OSError, ValueError):
        return None
    width, height = image.size
    if width < 8 or height < 8:
        return None
    if box is not None:
        left, top, right, bottom = box
        image = image.crop((
            int(width * left),
            int(height * top),
            max(int(width * left) + 8, int(width * right)),
            max(int(height * top) + 8, int(height * bottom)),
        ))
    # The whole frame, tiny. A Ken Burns crop of the same still stays
    # correlated. A different photograph does not.
    sample = image.resize((16, 16), Image.Resampling.BILINEAR)
    return list(sample.getdata())


def _correlated(left: list[int], right: list[int]) -> float:
    count = min(len(left), len(right))
    if count == 0:
        return 0.0
    mean_left = sum(left[:count]) / count
    mean_right = sum(right[:count]) / count
    numer = 0.0
    left_energy = 0.0
    right_energy = 0.0
    for index in range(count):
        a = left[index] - mean_left
        b = right[index] - mean_right
        numer += a * b
        left_energy += a * a
        right_energy += b * b
    if left_energy < 1.0 or right_energy < 1.0:
        return 1.0 if abs(mean_left - mean_right) < 12 else 0.0
    return numer / ((left_energy ** 0.5) * (right_energy ** 0.5))


def frame_fault(path: Path | str) -> str:
    """``black``, ``flat``, ``glitch``, or ``overlay`` when the frame is unusable."""
    try:
        with Image.open(path) as opened:
            image = opened.convert("RGB")
    except (OSError, ValueError):
        return "glitch"
    width, height = image.size
    if width < 8 or height < 8:
        return "glitch"
    small = image.resize((32, 32), Image.Resampling.BOX)
    pixels = list(small.getdata())
    if not pixels:
        return "glitch"
    luma = [((red + green + blue) / 3.0) for red, green, blue in pixels]
    mean = sum(luma) / len(luma)
    variance = sum((value - mean) ** 2 for value in luma) / len(luma)
    if mean < 10 or (variance < 8 and mean < 18):
        return "black"
    extremes = sum(1 for value in luma if value < 2 or value > 253)
    if extremes / len(luma) > 0.45:
        return "glitch"
    # A solid band across the middle is an overlay, not the photograph.
    row = luma[16 * 32: 17 * 32]
    if row and max(row) - min(row) < 4 and abs((sum(row) / len(row)) - mean) > 40:
        return "overlay"
    return ""


def beat_for_cue(cue: SubtitleCue, beats: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The still's beat for this subtitle, so the frame uses the still's rubric."""
    text = " ".join((cue.text or "").lower().split())
    if not text or not beats:
        return None
    hyphen = text.replace(" to ", "-")
    best: dict[str, Any] | None = None
    best_score = 0
    for beat in beats:
        spoken = " ".join(str(beat.get("text") or beat.get("spoken_text") or "").lower().split())
        if not spoken:
            continue
        if text in spoken or (hyphen and hyphen in spoken):
            return beat
        tokens = [token for token in text.split() if len(token) > 2]
        score = sum(1 for token in tokens if token in spoken)
        if score > best_score:
            best_score = score
            best = beat
    if best_score >= 2:
        return best
    return None


def gate_final_mp4(
    video_path: Path | str,
    cues: list[SubtitleCue] | None,
    *,
    probe_fn: Callable[[Path], dict[str, Any]] | None = None,
    evaluate_fn: Callable[..., dict[str, Any]] | None = None,
    frame_fn: Callable[[Path, float, Path], Path | None] | None = None,
    work_dir: Path | str | None = None,
    beats: list[dict[str, Any]] | None = None,
    narration_end_s: float | None = None,
) -> dict[str, Any]:
    """Sample frames at subtitle midpoints and score them with the still rubric.

    The frame is cropped above the subtitle band, and the prompt is the beat's
    image prompt rather than the four-word subtitle. An artefact beat also
    receives the reference photograph.
    """
    video = Path(video_path)
    probe = dict((probe_fn or probe_media)(video) or {})
    if narration_end_s is not None and float(narration_end_s) > 0.5:
        probe["narration_end_s"] = float(narration_end_s)
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
        clean = shot
        try:
            from modules.reel_qa.frames import strip_subtitle_band

            stripped = strip_subtitle_band(shot, dest.with_name(dest.stem + "_clean.jpg"))
            if stripped is not None:
                clean = stripped
        except Exception:  # noqa: BLE001
            clean = shot
        beat = beat_for_cue(cue, beats)
        spoken = str((beat or {}).get("text") or text)
        image_prompt = str((beat or {}).get("prompt") or spoken)
        beat_index = (beat or {}).get("index")
        still = str((beat or {}).get("image_path") or "")
        approved = bool(
            beat
            and beat.get("passed") is True
            and still
            and Path(still).is_file()
        )
        if approved and _same_approved_still(clean, still):
            fault = frame_fault(clean)
            frames.append(
                {
                    "index": i,
                    "text": text,
                    "at_s": round(cue.midpoint_s, 3),
                    "passed": not fault,
                    "score": (beat or {}).get("score"),
                    "reason": fault or "beat-approved",
                    "qa_status": fault or "beat-approved",
                    "frame_path": str(shot),
                    "beat_index": beat_index,
                }
            )
            _LOG.info(
                "MP4 frame | t=%.2f beat=%s | same-still | %s",
                cue.midpoint_s,
                beat_index,
                fault or "trusted",
            )
            continue
        if approved:
            _LOG.info(
                "MP4 frame | t=%.2f beat=%s | frame-differs | judging",
                cue.midpoint_s,
                beat_index,
            )
        if beat and bool(beat.get("is_cta")):
            fault = frame_fault(clean)
            frames.append(
                {
                    "index": i,
                    "text": text,
                    "at_s": round(cue.midpoint_s, 3),
                    "passed": not fault,
                    "score": (beat or {}).get("score"),
                    "reason": fault or "cta",
                    "qa_status": fault or "cta",
                    "frame_path": str(shot),
                    "beat_index": beat_index,
                }
            )
            _LOG.info(
                "MP4 frame | t=%.2f beat=%s | cta | %s",
                cue.midpoint_s,
                beat_index,
                fault or "skipped content check",
            )
            continue
        from modules.reel_qa.relevance import call_judge

        planned = str(
            (beat or {}).get("subject")
            or (beat or {}).get("shot_subject")
            or image_prompt
        )
        verdict = call_judge(
            judge,
            clean,
            spoken,
            image_prompt,
            banned_subjects=(beat or {}).get("banned_subjects"),
            reference_image=str((beat or {}).get("reference_image") or ""),
            canonical_shape=str((beat or {}).get("canonical_shape") or ""),
            score_against="shot",
            shot_subject=planned,
            shot_setting=str((beat or {}).get("setting") or (beat or {}).get("shot_setting") or ""),
            shot_era=str((beat or {}).get("era") or (beat or {}).get("shot_era") or ""),
            topic=str((beat or {}).get("topic") or ""),
            scene_type=str((beat or {}).get("scene_type") or "landscape"),
            geography=str((beat or {}).get("geography") or ""),
        ) or {}
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
                "beat_index": beat_index,
            }
        )
        _LOG.info(
            "MP4 frame | t=%.2f beat=%s passed=%s score=%s text=%.48s",
            cue.midpoint_s,
            beat_index,
            passed,
            verdict.get("relevance_score"),
            text,
        )
    return decide_mp4_gate(probe, frames)
