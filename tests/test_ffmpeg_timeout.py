"""A stuck ffmpeg fails its beat instead of holding the reel (s23d)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from core import reel_ffmpeg_render as render


def test_run_raises_when_ffmpeg_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render, "_ffmpeg_timeout_s", lambda: 0.5)
    with pytest.raises(RuntimeError, match="timed out"):
        render._run([sys.executable, "-c", "import time; time.sleep(5)"])


def test_timeout_env_has_a_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REEL_FFMPEG_TIMEOUT_S", "1")
    assert render._ffmpeg_timeout_s() == 30.0
    monkeypatch.setenv("REEL_FFMPEG_TIMEOUT_S", "x")
    assert render._ffmpeg_timeout_s() == 600.0


def test_subprocess_still_imported() -> None:
    assert render.subprocess is subprocess
