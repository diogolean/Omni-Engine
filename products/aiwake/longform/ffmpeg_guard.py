# -*- coding: utf-8 -*-
"""Reject ffmpeg graphs that can run until the laptop cooks.

``apad`` with no ``whole_dur`` and ``amix`` with ``duration=longest`` do not
finish when a source is open-ended. Every call in this package goes through
:func:`run_bounded`, which checks the command and then kills the process
group on a finite timeout.
"""
from __future__ import annotations

import re
import subprocess

from shared.ffmpeg import resolve_ffmpeg, run_ffmpeg

_APAD = re.compile(r"apad(?:=[^,;\[\]\s]+)?", re.IGNORECASE)
_LONGEST = re.compile(r"duration=longest|(?:^|[:\s])longest=1(?:$|[:\s,])", re.IGNORECASE)


class UnboundedFfmpeg(ValueError):
    """The command would not be allowed to start."""


def assert_bounded_ffmpeg(cmd: list[str]) -> None:
    """Raise when a filter can pad or mix without an end."""
    blob = " ".join(cmd)
    for match in _APAD.finditer(blob):
        body = match.group(0)
        if "whole_dur=" not in body.lower():
            raise UnboundedFfmpeg(f"unbounded apad in ffmpeg command: {body}")
    if _LONGEST.search(blob):
        raise UnboundedFfmpeg("amix duration=longest is not allowed")


def run_bounded(
    cmd: list[str],
    *,
    timeout: float,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run one ffmpeg or ffprobe command that is both finite and timed."""
    if timeout <= 0:
        raise ValueError("ffmpeg timeout must be positive")
    assert_bounded_ffmpeg(cmd)
    return run_ffmpeg(cmd, timeout=timeout, check=check)


def ffmpeg_binary() -> str:
    return resolve_ffmpeg()
