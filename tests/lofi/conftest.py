# -*- coding: utf-8 -*-
"""Collect LoFi probe modules that do not use a test_ filename."""
from __future__ import annotations

from pathlib import Path

import pytest

_PROBE = ("_probe.py",)


def pytest_collect_file(parent: pytest.Collector, file_path: Path):
    name = file_path.name
    if name.endswith(_PROBE) or name.startswith("probe_"):
        return pytest.Module.from_parent(parent, path=file_path)
    return None
