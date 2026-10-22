"""The parallax hook is opt-in and never breaks the Ken Burns path."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from modules.reel_hook import parallax


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "REEL_HOOK_PARALLAX",
        "REEL_HOOK_PARALLAX_PRESET",
        "REEL_HOOK_PARALLAX_STRENGTH",
        "REEL_HOOK_PARALLAX_MIN_S",
        "REEL_HOOK_PARALLAX_MAX_S",
    ):
        monkeypatch.delenv(name, raising=False)


def _still(tmp_path: Path) -> Path:
    from PIL import Image

    path = tmp_path / "hook.jpg"
    Image.new("RGB", (64, 96), (90, 120, 150)).save(path)
    return path


def test_flag_is_off_by_default() -> None:
    assert parallax.resolve_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "on", "YES"])
def test_flag_reads_env(monkeypatch, value: str) -> None:
    monkeypatch.setenv("REEL_HOOK_PARALLAX", value)
    assert parallax.resolve_enabled() is True
    assert parallax.resolve_enabled(False) is False


def test_flag_off_leaves_videos_untouched(tmp_path: Path, monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise AssertionError("renderer must not run with the flag off")

    monkeypatch.setattr(parallax, "render_hook_clip", _boom)
    monkeypatch.setattr(parallax, "_render", _boom)
    stills = [_still(tmp_path)]
    assert parallax.hook_video_override(
        stills, None, act_duration=4.5, out_dir=tmp_path,
    ) == ([], False)
    given = [None, tmp_path / "b.mp4"]
    videos, hold = parallax.hook_video_override(
        stills, given, act_duration=4.5, out_dir=tmp_path,
    )
    assert videos == given and hold is False


def test_missing_deps_fall_back_to_ken_burns(tmp_path: Path, monkeypatch, caplog) -> None:
    monkeypatch.setenv("REEL_HOOK_PARALLAX", "1")
    monkeypatch.setattr(parallax, "missing_dependencies", lambda: ["torch", "transformers"])

    def _boom(*_a, **_k):
        raise AssertionError("renderer must not run without its packages")

    monkeypatch.setattr(parallax, "_render", _boom)
    with caplog.at_level("WARNING", logger="production.formats.economic_reel.hook.parallax"):
        videos, hold = parallax.hook_video_override(
            [_still(tmp_path)], None, act_duration=4.5, out_dir=tmp_path,
        )
    assert videos == [] and hold is False
    assert any("missing torch" in r.getMessage() for r in caplog.records)


def test_render_error_falls_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REEL_HOOK_PARALLAX", "1")
    monkeypatch.setattr(parallax, "missing_dependencies", lambda: [])

    def _fail(*_a, **_k):
        raise RuntimeError("ffmpeg exited 1")

    monkeypatch.setattr(parallax, "_render", _fail)
    assert parallax.hook_video_override(
        [_still(tmp_path)], None, act_duration=4.5, out_dir=tmp_path,
    ) == ([], False)


def test_flag_on_puts_clip_in_slot_zero_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REEL_HOOK_PARALLAX", "1")
    monkeypatch.setattr(parallax, "missing_dependencies", lambda: [])
    seen: dict = {}

    def _fake(in_path, out_path, preset, *, seconds, fps, size, crf=20):
        seen.update(seconds=seconds, preset=preset, size=size)
        Path(out_path).write_bytes(b"\0" * 4096)
        return {"secs_total": 0.1, "secs_depth": 0.0}

    monkeypatch.setattr(parallax, "_render", _fake)
    stills = [_still(tmp_path), tmp_path / "b.jpg", tmp_path / "c.jpg"]
    videos, hold = parallax.hook_video_override(
        stills, None, act_duration=7.0, out_dir=tmp_path / "hook",
    )
    assert hold is True
    assert len(videos) == 3 and videos[1] is None and videos[2] is None
    assert Path(videos[0]).name.startswith("hook_parallax_")
    assert seen["seconds"] == 5.0
    assert seen["size"] == (1080, 1920)


def test_existing_act_zero_video_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REEL_HOOK_PARALLAX", "1")
    wan = tmp_path / "wan.mp4"
    wan.write_bytes(b"x")
    monkeypatch.setattr(parallax, "render_hook_clip", lambda *a, **k: 1 / 0)
    assert parallax.hook_video_override(
        [_still(tmp_path)], [wan], act_duration=4.0, out_dir=tmp_path,
    ) == ([wan], False)


def test_clip_seconds_stays_in_four_to_five() -> None:
    assert parallax.clip_seconds(4.5) == 4.5
    assert parallax.clip_seconds(9.0) == 5.0
    assert parallax.clip_seconds(4.2) == 4.2
    # Never longer than the act it fills.
    assert parallax.clip_seconds(3.0) == 3.0


def test_default_motion_is_stronger_than_prototype() -> None:
    proto = parallax.PRESETS["push_orbit"]
    p = parallax.resolve_preset()
    assert p.orbit_x == pytest.approx(proto.orbit_x * 2)
    assert p.dolly == pytest.approx(proto.dolly * 2)
    assert p.margin >= proto.margin


def test_unknown_preset_uses_push_orbit(monkeypatch) -> None:
    monkeypatch.setenv("REEL_HOOK_PARALLAX_PRESET", "nope")
    monkeypatch.setenv("REEL_HOOK_PARALLAX_STRENGTH", "1")
    assert parallax.resolve_preset() == parallax.resolve_preset("push_orbit", 1.0)


def test_import_does_not_pull_heavy_packages() -> None:
    code = (
        "import sys, modules.reel_hook.parallax as p; "
        "p.resolve_enabled(); p.resolve_preset(); "
        "bad=[m for m in ('torch','transformers','cv2','numpy') if m in sys.modules]; "
        "print(','.join(bad))"
    )
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == ""
