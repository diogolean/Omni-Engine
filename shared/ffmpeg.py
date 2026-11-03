# -*- coding: utf-8 -*-
"""ffmpeg/ffprobe resolution and descendant-process cleanup.

Resolve order: ``FFMPEG_BINARY`` → ``PATH`` → ``imageio_ffmpeg`` → common
Windows installs. Aiwake keeps its own copy.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any

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


def kill_process_tree(proc: subprocess.Popen) -> None:
    """Stop ``proc`` and every process it started.

    ``subprocess`` timeout kills only the direct child. A filter graph can
    leave grandchildren running after that child is gone.
    """
    pid = proc.pid
    if not pid:
        return
    if os.name != "nt":
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass
    else:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        import psutil

        try:
            parent = psutil.Process(pid)
        except psutil.NoSuchProcess:
            parent = None
        if parent is not None:
            nodes = parent.children(recursive=True)
            nodes.append(parent)
            for node in nodes:
                try:
                    node.kill()
                except psutil.Error:
                    pass
    except Exception:  # noqa: BLE001 — psutil is optional at the call site
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def popen_ffmpeg(
    cmd: list[str],
    *,
    stdin: int | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
    bufsize: int = -1,
    text: bool = False,
) -> subprocess.Popen:
    """Start ffmpeg in its own session so a timeout can kill the whole tree."""
    return subprocess.Popen(
        list(cmd),
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        bufsize=bufsize,
        text=text,
        start_new_session=True,
    )


def run_ffmpeg(
    cmd: list[str],
    *,
    timeout: float,
    input: bytes | str | None = None,  # noqa: A002 — matches subprocess.run
    text: bool = False,
    stdin: Any = None,
    stdout: Any = subprocess.PIPE,
    stderr: Any = subprocess.PIPE,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run one ffmpeg or ffprobe command.

    The process group is killed when ``timeout`` elapses or when the caller
    is cancelled while the process is still alive.
    """
    if timeout <= 0:
        raise ValueError("ffmpeg timeout must be positive")
    if stdin is None:
        stdin = subprocess.PIPE if input is not None else subprocess.DEVNULL
    proc = popen_ffmpeg(
        list(cmd),
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        text=text,
    )
    try:
        try:
            out, err = proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            kill_process_tree(proc)
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out, err = exc.output, exc.stderr
            raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err) from exc
        result = subprocess.CompletedProcess(list(cmd), proc.returncode or 0, out, err)
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, list(cmd), output=out, stderr=err
            )
        return result
    finally:
        if proc.poll() is None:
            kill_process_tree(proc)


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
