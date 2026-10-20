# -*- coding: utf-8 -*-
"""ffmpeg/ffprobe resolution and descendant-process cleanup.

Resolve order: ``FFMPEG_BINARY`` → ``PATH`` → ``imageio_ffmpeg`` → common
Windows installs. Aiwake keeps its own copy.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from shared.paths import WINDOWS_FFMPEG_FALLBACK, ffmpeg_binary_override


def kill_descendant_ffmpeg() -> int:
    """Kill ffmpeg processes descended from this Python PID.

    psutil is the portable path. PowerShell is only a Windows fallback and
    is not invoked on Linux or in the ARM container, where it is absent.
    """
    n = 0
    try:
        import psutil

        me = psutil.Process(os.getpid())
        for child in me.children(recursive=True):
            name = (child.name() or "").lower()
            if "ffmpeg" in name:
                try:
                    child.kill()
                    n += 1
                except Exception:  # noqa: BLE001
                    pass
        return n
    except Exception:  # noqa: BLE001
        pass
    if os.name != "nt":
        return n
    try:
        pid = str(os.getpid())
        script = (
            "$seen=@(); $frontier=@(" + pid + "); "
            "while($frontier.Count -gt 0){ $p=$frontier[0]; "
            "if($frontier.Count -gt 1){"
            "$frontier=$frontier[1..($frontier.Count-1)]}"
            "else{$frontier=@()}; "
            'Get-CimInstance Win32_Process -Filter "ParentProcessId=$p" | '
            "ForEach-Object { $seen+=$_; $frontier+=$_.ProcessId } }; "
            "$seen | Where-Object { $_.Name -match 'ffmpeg' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
            "-ErrorAction SilentlyContinue }"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            timeout=20,
            check=False,
            capture_output=True,
        )
    except Exception:  # noqa: BLE001
        return n
    return n


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
