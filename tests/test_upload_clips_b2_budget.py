# -*- coding: utf-8 -*-
"""The clip uploader must use the budgeted client and stop on a daily cap."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.media.b2_budget import B2DailyCapError


def _clip(tmp_path: Path) -> Path:
    folder = tmp_path / "clips"
    folder.mkdir()
    path = folder / "reel.mp4"
    path.write_bytes(b"not-a-real-video")
    return folder


def test_dry_run_does_not_open_a_b2_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import upload_clips_to_b2 as mod

    created: list[str] = []

    class _Uploader:
        def __init__(self, bucket_name: str | None = None) -> None:
            created.append(bucket_name or "")

        def upload(self, local_path: Path, *, content_type: str = "video/mp4") -> str:
            raise AssertionError(local_path)

    monkeypatch.setattr("agents.media.b2_client.B2VideoUploader", _Uploader)
    mod.main(
        [
            "--clips-dir",
            str(_clip(tmp_path)),
            "--dry-run",
            "--bucket",
            "unit-bucket",
        ]
    )
    assert created == []


def test_cap_stops_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import upload_clips_to_b2 as mod

    seen: list[tuple[str, str]] = []

    class _Uploader:
        def __init__(self, bucket_name: str | None = None) -> None:
            self.bucket_name = bucket_name or ""

        def upload(self, local_path: Path, *, content_type: str = "video/mp4") -> str:
            seen.append((self.bucket_name, content_type))
            raise B2DailyCapError("B2 daily Class B budget reached (1500).")

    monkeypatch.setattr("agents.media.b2_client.B2VideoUploader", _Uploader)
    with pytest.raises(SystemExit) as exc:
        mod.main(
            [
                "--clips-dir",
                str(_clip(tmp_path)),
                "--bucket",
                "unit-bucket",
                "--output",
                str(tmp_path / "planner.xlsx"),
            ]
        )
    assert exc.value.code == 1
    assert seen == [("unit-bucket", "video/mp4")]
    assert not (tmp_path / "planner.xlsx").exists()
