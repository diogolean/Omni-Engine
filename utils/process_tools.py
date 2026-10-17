# -*- coding: utf-8 -*-
"""Process helpers that behave the same on Windows and Linux."""
from __future__ import annotations

import os
import subprocess


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
