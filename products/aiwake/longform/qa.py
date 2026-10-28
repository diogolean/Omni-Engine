# -*- coding: utf-8 -*-
"""Pre-publish gate for a long episode and the Shorts cut from it.

Failures block publishing:

* the long file is missing, outside the duration window for its target, has
  no audio stream, or is not 1920x1080;
* the debate stopped before the verdict exchange, or a line has no voice;
* YouTube would drop the chapters;
* there are not 2-3 Shorts, or a Short is under 15 s, over the Shorts cap,
  silent, or not vertical.

Warnings do not block: a hook gap over the limit, a hook that did not land,
lines that shipped on their last try with a soft miss, a finale that never
came, a Short cut from the master instead of re-rendered.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from products.aiwake.settings import LongFormatPreset
from shared.ffmpeg import resolve_ffmpeg, resolve_ffprobe

from .ffmpeg_guard import run_bounded

_PROBE_TIMEOUT_S = 20.0
_SHORT_SLACK_S = 1.0
_FRAME_SIZE = re.compile(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})")


@dataclass(frozen=True)
class MediaProbe:
    path: str
    duration_s: float | None
    has_audio: bool
    width: int
    height: int
    error: str = ""


@dataclass
class QAReport:
    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    long_probe: MediaProbe | None = None
    short_probes: list[MediaProbe] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "failures": list(self.failures),
            "warnings": list(self.warnings),
            "long": _probe_dict(self.long_probe),
            "shorts": [_probe_dict(item) for item in self.short_probes],
        }


def duration_window(target_s: float, preset: LongFormatPreset) -> tuple[float, float]:
    """Acceptance band for one target, clamped to the 5–10 minute product range."""
    low = max(float(preset.min_duration_s), float(target_s) - float(preset.qa_slack_s))
    high = min(float(preset.max_duration_s), float(target_s) + float(preset.qa_slack_s))
    if low > high:
        return float(preset.min_duration_s), float(preset.max_duration_s)
    return low, high


def evaluate_package(
    *,
    long_probe: MediaProbe | None,
    short_probes: Sequence[MediaProbe],
    preset: LongFormatPreset,
    target_s: float,
    shorts_expected: int,
    require_media: bool = True,
    problems: Sequence[str] = (),
    warnings: Sequence[str] = (),
) -> QAReport:
    """Judge the probes plus the episode ``problems`` found upstream.

    ``require_media`` is false for a transcript-only run: the media checks
    are skipped, the episode checks still count.
    """
    failures = [item for item in problems if item]
    if require_media:
        failures.extend(_long_failures(long_probe, preset, target_s))
        failures.extend(_short_failures(short_probes, preset, shorts_expected))
    return QAReport(
        passed=not failures,
        failures=failures,
        warnings=[item for item in warnings if item],
        long_probe=long_probe,
        short_probes=list(short_probes),
    )


def episode_problems(*, end_reason: str, episode: Mapping[str, Any] | None) -> list[str]:
    """Blocking story problems: the debate stopped early or never reached the verdict."""
    problems: list[str] = []
    if end_reason != "complete":
        problems.append(f"the debate ended early ({end_reason})")
    if episode is not None:
        verdict = [beat for beat in episode.get("beats") or [] if beat.get("kind") == "outro"]
        if not verdict or verdict[-1].get("guest_turn") is None:
            problems.append("the episode never finished the verdict exchange")
    return problems


def episode_warnings(episode: Mapping[str, Any] | None, *, max_hook_gap_s: float | None = None) -> list[str]:
    """Retention and script-quality notes that do not block publishing."""
    if not episode:
        return []
    warnings: list[str] = []
    limit = float(episode.get("hook_max_gap_s") or 0.0)
    gap = float(episode.get("max_hook_gap_s") or 0.0) if max_hook_gap_s is None else float(max_hook_gap_s)
    if limit and gap > limit + 0.5:
        warnings.append(f"the longest stretch without a hook was {gap:.0f}s (limit {limit:.0f}s)")
    missed = int(episode.get("hooks_missed") or 0)
    if missed:
        warnings.append(f"{missed} hook(s) did not land the way the director asked")
    relaxed = int(episode.get("relaxed_lines") or 0)
    if relaxed:
        details = sorted(
            {
                problem.partition(":")[0]
                for beat in episode.get("beats") or []
                for found in (beat.get("relaxed") or {}).values()
                for problem in found
            }
        )
        warnings.append(
            f"{relaxed} line(s) shipped on their last try with a soft miss ({', '.join(details) or 'unknown'})"
        )
    beats = episode.get("beats") or []
    if beats and not any(beat.get("key") == "chokehold" for beat in beats):
        warnings.append("the episode reached the verdict without the chokehold round")
    return warnings


def voice_problems(utterances: Sequence[Any], audio_by_turn: Mapping[int, Any] | None) -> list[str]:
    """A spoken line with no voice track plays as silence under its subtitle."""
    if audio_by_turn is None:
        return []
    missing = [item.turn_index for item in utterances if item.turn_index not in audio_by_turn]
    if not missing:
        return []
    listed = ", ".join(str(turn) for turn in missing[:8])
    return [f"{len(missing)} of {len(utterances)} lines have no voice track (turns {listed})"]


def probe_media(path: Path) -> MediaProbe:
    """Read duration, audio presence, and frame size. The probe itself is timed."""
    path = Path(path)
    if not path.is_file():
        return MediaProbe(str(path), None, False, 0, 0, "missing")
    try:
        ffmpeg = resolve_ffmpeg()
    except RuntimeError:
        ffmpeg = ""
    ffprobe = resolve_ffprobe(ffmpeg) if ffmpeg else shutil.which("ffprobe")
    try:
        if ffprobe:
            return _probe_with_ffprobe(path, ffprobe)
        if ffmpeg:
            return _probe_with_ffmpeg(path, ffmpeg)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return MediaProbe(str(path), None, False, 0, 0, f"probe failed: {exc}")
    return MediaProbe(str(path), None, False, 0, 0, "neither ffprobe nor ffmpeg was found")


def _long_failures(probe: MediaProbe | None, preset: LongFormatPreset, target_s: float) -> list[str]:
    if probe is None or probe.duration_s is None:
        reason = f" ({probe.error})" if probe is not None and probe.error else ""
        return [f"long video is missing or has no readable duration{reason}"]
    failures: list[str] = []
    low, high = duration_window(target_s, preset)
    if not low <= probe.duration_s <= high:
        failures.append(
            f"long duration {probe.duration_s:.1f}s is outside {low:.0f}-{high:.0f}s "
            f"for a {target_s:.0f}s target"
        )
    if not probe.has_audio:
        failures.append("long video has no audio stream")
    wanted_w, wanted_h = preset.resolution
    if (probe.width, probe.height) != (wanted_w, wanted_h):
        failures.append(f"long video is {probe.width}x{probe.height}, wanted {wanted_w}x{wanted_h}")
    return failures


def _short_failures(probes: Sequence[MediaProbe], preset: LongFormatPreset, expected: int) -> list[str]:
    failures: list[str] = []
    if not 2 <= len(probes) <= 3:
        failures.append(f"expected 2-3 shorts, found {len(probes)}")
    elif len(probes) != expected:
        failures.append(f"expected {expected} shorts, found {len(probes)}")
    for probe in probes:
        name = Path(probe.path).name
        if probe.duration_s is None:
            failures.append(f"{name} has no readable duration")
            continue
        if probe.duration_s < float(preset.shorts_min_s):
            failures.append(f"{name} is {probe.duration_s:.1f}s, under {preset.shorts_min_s:.0f}s")
        if probe.duration_s > float(preset.shorts_max_s) + _SHORT_SLACK_S:
            failures.append(f"{name} is {probe.duration_s:.1f}s, over {preset.shorts_max_s:.0f}s")
        if not probe.has_audio:
            failures.append(f"{name} has no audio stream")
        if probe.width >= probe.height:
            failures.append(f"{name} is not vertical ({probe.width}x{probe.height})")
    return failures


def _probe_with_ffprobe(path: Path, ffprobe: str) -> MediaProbe:
    completed = run_bounded(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        timeout=_PROBE_TIMEOUT_S,
        check=False,
    )
    stdout = _text(completed.stdout)
    if completed.returncode != 0:
        return MediaProbe(str(path), None, False, 0, 0, _text(completed.stderr).strip()[-200:])
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return MediaProbe(str(path), None, False, 0, 0, "ffprobe printed invalid JSON")
    duration = _float_or_none((payload.get("format") or {}).get("duration"))
    has_audio = False
    width = height = 0
    for stream in payload.get("streams") or []:
        if stream.get("codec_type") == "audio":
            has_audio = True
        if stream.get("codec_type") == "video" and not width:
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
    return MediaProbe(str(path), duration, has_audio, width, height)


def _probe_with_ffmpeg(path: Path, ffmpeg: str) -> MediaProbe:
    """Header read only. ``ffmpeg -i`` with no output exits 1 after printing the streams."""
    completed = run_bounded([ffmpeg, "-hide_banner", "-i", str(path)], timeout=_PROBE_TIMEOUT_S, check=False)
    stderr = _text(completed.stderr)
    duration = None
    marker = "Duration: "
    if marker in stderr:
        stamp = stderr.split(marker, 1)[1].split(",", 1)[0].strip()
        duration = _stamp_to_seconds(stamp)
    match = _FRAME_SIZE.search(stderr)
    width, height = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    return MediaProbe(str(path), duration, "Audio:" in stderr, width, height)


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def _stamp_to_seconds(stamp: str) -> float | None:
    parts = stamp.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _probe_dict(probe: MediaProbe | None) -> dict[str, object] | None:
    if probe is None:
        return None
    return {
        "path": probe.path,
        "duration_s": None if probe.duration_s is None else round(probe.duration_s, 3),
        "has_audio": probe.has_audio,
        "width": probe.width,
        "height": probe.height,
        "error": probe.error,
    }


__all__ = [
    "MediaProbe",
    "QAReport",
    "duration_window",
    "episode_problems",
    "episode_warnings",
    "evaluate_package",
    "probe_media",
    "voice_problems",
]
