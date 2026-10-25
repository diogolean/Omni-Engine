# -*- coding: utf-8 -*-
"""Grab one JPEG frame from a clip. Uses ffmpeg, then MoviePy if ffmpeg fails."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def extract_video_still(video_path: Path, dest: Path) -> str:
    """Extract a mid-clip JPEG still. Returns dest path or empty string."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        return str(dest.resolve())

    try:
        from shared.ffmpeg import run_ffmpeg

        cmd = [
            "ffmpeg", "-y", "-ss", "2", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "3", str(dest),
        ]
        proc = run_ffmpeg(cmd, text=True, timeout=30)
        if proc.returncode == 0 and dest.is_file():
            return str(dest.resolve())
    except Exception as exc:  # noqa: BLE001
        log.debug("ffmpeg still extract failed for %s: %s", video_path.name, exc)

    try:
        from moviepy.editor import VideoFileClip  # noqa: PLC0415

        clip = VideoFileClip(str(video_path))
        t = min(2.0, max(0.1, (clip.duration or 2.0) * 0.2))
        clip.save_frame(str(dest), t)
        clip.close()
        if dest.is_file():
            return str(dest.resolve())
    except Exception as exc:  # noqa: BLE001
        log.debug("moviepy still extract failed for %s: %s", video_path.name, exc)
    return ""
