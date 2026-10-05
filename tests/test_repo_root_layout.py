# -*- coding: utf-8 -*-
"""The repo root stays free of scratch, preview, review, and log folders."""
from __future__ import annotations

from pathlib import Path

import pytest

from utils.pipeline_paths import review_path, temp_path

_REPO = Path(__file__).resolve().parents[1]


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
    assert scratch.is_dir()
    assert review.parent.is_dir()
