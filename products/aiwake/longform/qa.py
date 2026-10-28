# -*- coding: utf-8 -*-
"""Pre-publish gate for a long episode and the Shorts cut from it.

The long file must land inside the target window and carry an audio stream.
A teaser under 15 seconds never ships.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from products.aiwake.longform.ffmpeg_guard import ffmpeg_binary, run_bounded
from products.aiwake.settings import LongFormatPreset

_PROBE_TIMEOUT_S = 20.0


@dataclass(frozen=True)
class MediaProbe:
    path: str
    duration_s: float | None
    has_audio: bool
    width: int
    height: int


@dataclass
class QAReport:
    passed: bool
    failures: list[str] = field(default_factory=list)
    long_probe: MediaProbe | None = None
    short_probes: list[MediaProbe] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "failures": list(self.failures),
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
    short_probes: list[MediaProbe],
    preset: LongFormatPreset,
    target_s: float,
    shorts_expected: int,
    require_media: bool = True,
) -> QAReport:
    """Judge probes. ``require_media`` is false for a transcript-only run."""
    if not require_media:
        return QAReport(passed=True, failures=[], long_probe=long_probe, short_probes=short_probes)
    failures: list[str] = []
    low, high = duration_window(target_s, preset)
    if long_probe is None or long_probe.duration_s is None:
        failures.append("long video is missing or has no readable duration")
    else:
        duration = long_probe.duration_s
        if not low <= duration <= high:
            failures.append(
                f"long duration {duration:.1f}s is outside {low:.0f}-{high:.0f}s "
                f"for a {target_s:.0f}s target"
            )
        if not long_probe.has_audio:
            failures.append("long video has no audio stream")
        if long_probe.width != preset.resolution[0] or long_probe.height != preset.resolution[1]:
            failures.append(
                f"long video is {long_probe.width}x{long_probe.height}, "
                f"wanted {preset.resolution[0]}x{preset.resolution[1]}"
            )
    if len(short_probes) < 2 or len(short_probes) > 3:
        failures.append(f"expected 2-3 shorts, found {len(short_probes)}")
    elif len(short_probes) != shorts_expected:
        failures.append(f"expected {shorts_expected} shorts, found {len(short_probes)}")
    for probe in short_probes:
        duration = probe.duration_s
        if duration is None:
            failures.append(f"{probe.path} has no readable duration")
            continue
        if duration < float(preset.shorts_min_s):
            failures.append(f"{probe.path} is {duration:.1f}s, under {preset.shorts_min_s:.0f}s")
        if not probe.has_audio:
            failures.append(f"{probe.path} has no audio stream")
        if probe.width >= probe.height:
            failures.append(f"{probe.path} is not vertical ({probe.width}x{probe.height})")
    return QAReport(
        passed=not failures,
        failures=failures,
        long_probe=long_probe,
        short_probes=list(short_probes),
    )


def probe_media(path: Path) -> MediaProbe:
    """Read duration, audio presence, and frame size. The probe itself is timed."""
    if not path.is_file():
        return MediaProbe(str(path), None, False, 0, 0)
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        return _probe_with_ffprobe(path, ffprobe)
    return _probe_with_ffmpeg(path)


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
    if completed.returncode != 0:
        return MediaProbe(str(path), None, False, 0, 0)
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return MediaProbe(str(path), None, False, 0, 0)
    duration = _float_or_none((payload.get("format") or {}).get("duration"))
    has_audio = False
    width = 0
    height = 0
    for stream in payload.get("streams") or []:
        if stream.get("codec_type") == "audio":
            has_audio = True
        if stream.get("codec_type") == "video" and not width:
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
    return MediaProbe(str(path), duration, has_audio, width, height)


def _probe_with_ffmpeg(path: Path) -> MediaProbe:
    completed = run_bounded(
        [ffmpeg_binary(), "-hide_banner", "-i", str(path), "-f", "null", "-"],
        timeout=_PROBE_TIMEOUT_S,
        check=False,
    )
    stderr = completed.stderr or ""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    duration = None
    marker = "Duration: "
    if marker in stderr:
        stamp = stderr.split(marker, 1)[1].split(",", 1)[0].strip()
        duration = _stamp_to_seconds(stamp)
    has_audio = "Audio:" in stderr
    width, height = _frame_size(stderr)
    return MediaProbe(str(path), duration, has_audio, width, height)


def _frame_size(stderr: str) -> tuple[int, int]:
    import re

    match = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", stderr)
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


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
    }
