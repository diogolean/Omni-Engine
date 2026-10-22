# -*- coding: utf-8 -*-
"""The repo root stays free of scratch, preview, review, and log folders."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from utils.pipeline_paths import review_path, temp_path

_REPO = Path(__file__).resolve().parents[1]

# Tracked root names. Missing names are fine (another change may add them).
# A new tracked root entry has to be added here and in docs/architecture/REPO_LAYOUT.md.
_ALLOWED_ROOT = {
    ".dockerignore",
    ".env.example",
    ".gitattributes",
    ".gitleaksignore",
    ".github",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".cursor",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "Dockerfile",
    "Fonts",
    "README.md",
    "accounts",
    "agents",
    "animation",
    "assets",
    "channel_loader.py",
    "channels_config",
    "config",
    "config.py",
    "core",
    "distribution",
    "docker",
    "docker-compose.yml",
    "docs",
    "engine",
    "infra",
    "main.py",
    "models_avatar",
    "modules",
    "portfolio_showcase",
    "production",
    "products",
    "pyproject.toml",
    "quality",
    "requirements-ci.txt",
    "requirements-docker.txt",
    "requirements.txt",
    "run_main_noupload.py",
    "scripts",
    "shared",
    "tests",
    "tools",
    "utils",
}


def _scratch_dir_names(root: Path) -> list[str]:
    found: list[str] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if (
            name.startswith("_tmp")
            or name in {"_review", "_cp_preview"}
            or "log" in name.lower()
        ):
            found.append(name)
    return sorted(found)


def _tracked_root_names() -> set[str]:
    out = subprocess.check_output(
        ["git", "ls-files"],
        cwd=_REPO,
        text=True,
    )
    names: set[str] = set()
    for line in out.splitlines():
        if not line or line.startswith("."):
            # Keep dotted top-level names (.github, .gitignore) and skip nothing else.
            pass
        top = line.split("/", 1)[0]
        if top:
            names.add(top)
    return names


def test_tracked_root_matches_the_layout_allowlist() -> None:
    extra = sorted(_tracked_root_names() - _ALLOWED_ROOT)
    assert extra == [], (
        "New root entries need a home in docs/architecture/REPO_LAYOUT.md "
        f"and the allowlist in this test: {extra}"
    )


def test_outputs_and_error_screenshots_are_not_tracked() -> None:
    out = subprocess.check_output(["git", "ls-files"], cwd=_REPO, text=True)
    bad = [
        line
        for line in out.splitlines()
        if line.startswith("outputs/")
        or "/screenshots/" in line
        or line.endswith(".npz")
        or line.endswith(".psd")
        or line.endswith("_recovered.txt")
        or line.endswith("_recovered_chat.txt")
        or "/__mine_" in line
        or line.startswith("__mine_")
    ]
    assert bad == [], f"Runtime junk is tracked: {bad[:12]}"


def test_repo_root_has_no_scratch_dirs() -> None:
    found = _scratch_dir_names(_REPO)
    assert found == [], (
        "Scratch folders belong under outputs/<channel>/ via "
        f"temp_path() and review_path(), not at the repo root: {found}"
    )


def test_temp_and_review_paths_stay_under_channel_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    scratch = temp_path("aiwake", "frames")
    review = review_path("Wonder_Feed", "still.png")
    assert scratch == tmp_path / "aiwake" / "_tmp" / "frames"
    assert review == tmp_path / "wonder_feed" / "_review" / "still.png"
    # Parent folders exist; the last part is left for the caller (file or dir).
    assert scratch.parent.is_dir()
    assert review.parent.is_dir()
    assert not review.exists()
    assert temp_path("aiwake").is_dir()
