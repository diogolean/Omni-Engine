# -*- coding: utf-8 -*-
"""One ffmpeg/ffprobe resolver for engine tools.

Order: ``FFMPEG_BINARY`` → ``PATH`` → ``imageio_ffmpeg`` → common Windows installs.
Process cleanup lives in ``utils.process_tools``. Aiwake keeps its own copy.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from utils.pipeline_paths import WINDOWS_FFMPEG_FALLBACK, ffmpeg_binary_override


def resolve_ffmpeg() -> str:
    """Return an ffmpeg executable path or raise ``RuntimeError``."""
    override = ffmpeg_binary_override()
    if override is not None:
        return str(override)
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    try:
        import imageio_ffmpeg  # type: ignore  # noqa: PLC0415

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return str(exe)
    except Exception:  # noqa: BLE001
        pass
    for cand in (
        Path.home() / "AppData/Local/Programs/Stremio/ffmpeg.exe",
        WINDOWS_FFMPEG_FALLBACK,
    ):
        if cand.is_file():
            return str(cand)
    raise RuntimeError(
        "ffmpeg not found on PATH, imageio_ffmpeg, or common install paths."
    )


def resolve_ffprobe(ffmpeg: str) -> str | None:
    """``ffprobe`` on PATH, else next to ``ffmpeg``, else ``None``."""
    fp = shutil.which("ffprobe")
    if fp:
        return fp
    for name in ("ffprobe.exe", "ffprobe"):
        sibling = Path(ffmpeg).with_name(name)
        if sibling.is_file():
            return str(sibling)
    return None
