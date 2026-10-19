# -*- coding: utf-8 -*-
"""LOFI BGM lookup: env folder first, repo folder next, voice-only last."""
from __future__ import annotations

from pathlib import Path

import pytest


def _mp3(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(b"\xff\xfb" + b"\0" * 4096)
    return p


def test_env_folder_wins_and_repo_folder_is_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.economic_reel_lofi import assembler
    from core.economic_reel_lofi import config as lofi_cfg

    root = tmp_path / "engine"
    repo = _mp3(root / lofi_cfg.BGM_DIR_REL, "Quiet Piano Bed.mp3")
    extra = _mp3(tmp_path / "drive_bgm", "Soft Keys.MP3")

    monkeypatch.delenv("LOFI_BGM_DIR", raising=False)
    assert assembler.list_library_bgm_tracks(root) == [repo]

    monkeypatch.setenv("LOFI_BGM_DIR", str(tmp_path / "drive_bgm"))
    assert lofi_cfg.bgm_dirs(root)[0] == tmp_path / "drive_bgm"
    assert assembler.list_library_bgm_tracks(root) == [extra]


def test_no_library_means_voice_only_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.economic_reel_lofi import assembler
    from core.economic_reel_lofi import config as lofi_cfg

    monkeypatch.delenv("LOFI_BGM_DIR", raising=False)
    assert assembler.pick_library_bgm(tmp_path / "empty") is None
    assert lofi_cfg.REQUIRE_BGM is False


def test_generated_beds_stay_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.economic_reel_lofi import assembler

    _mp3(tmp_path / "beds", "lofi_bed_01.mp3")
    monkeypatch.setenv("LOFI_BGM_DIR", str(tmp_path / "beds"))
    assert assembler.list_library_bgm_tracks(tmp_path / "engine") == []
