# -*- coding: utf-8 -*-
"""ADR 0008 M1: the target import contracts load and produce a report.

Broken contracts are expected until the packages move. This test fails
only when the contract file cannot be read or the linter cannot run.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_CONTRACTS = (
    "Animation does not import products, channels, or the factory pipeline",
    "Production does not import distribution",
    "Distribution does not import production, animation, or products",
    "Factory code does not import Aiwake",
    "Aiwake does not import the production pipeline",
    "Channel packages do not import the factory or Aiwake",
    "Path and process helpers do not import the factory",
    "Shared does not import the factory",
)


def test_import_contracts_name_the_target_rules() -> None:
    data = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    contracts = data["tool"]["importlinter"]["contracts"]
    assert [row["name"] for row in contracts] == list(_CONTRACTS)
    assert data["tool"]["importlinter"]["include_external_packages"] is False


def test_import_contracts_report_runs() -> None:
    exe = shutil.which("lint-imports")
    assert exe, "lint-imports is not installed (requirements-ci.txt)"
    proc = subprocess.run(
        [exe],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    report = proc.stdout + proc.stderr
    assert "Traceback" not in report
    assert "KEPT" in proc.stdout or "BROKEN" in proc.stdout
    for name in _CONTRACTS:
        assert name in proc.stdout
    # 0: every contract kept. 1: at least one broken contract (report only).
    assert proc.returncode in (0, 1)
