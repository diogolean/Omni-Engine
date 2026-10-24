# -*- coding: utf-8 -*-
"""A timed-out ffmpeg call has to take its children with it."""
from __future__ import annotations

import subprocess
import time

import psutil
import pytest

from shared.ffmpeg import run_ffmpeg


def test_timeout_kills_the_process_tree() -> None:
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_ffmpeg(["bash", "-c", "sleep 60 & wait"], timeout=0.4)
    assert time.monotonic() - started < 5
    leftover = [
        child.name()
        for child in psutil.Process().children(recursive=True)
        if "sleep" in (child.name() or "")
    ]
    assert leftover == []
