# -*- coding: utf-8 -*-
"""The ffmpeg kill fallback must not call PowerShell off Windows."""
from __future__ import annotations

import subprocess
import sys

from shared.ffmpeg import kill_descendant_ffmpeg


def test_posix_skips_powershell_when_psutil_is_missing(monkeypatch) -> None:
    monkeypatch.setattr("os.name", "posix")
    monkeypatch.setitem(sys.modules, "psutil", None)

    def _no_powershell(*_args, **_kwargs):
        raise AssertionError("powershell must not run on non-Windows")

    monkeypatch.setattr(subprocess, "run", _no_powershell)
    assert kill_descendant_ffmpeg() == 0
