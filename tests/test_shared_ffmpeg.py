"""shared.ffmpeg is the one resolver; old entry points delegate to it."""
from __future__ import annotations

import shared.ffmpeg as ffmpeg


def test_override_wins(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "ffmpeg"
    exe.write_bytes(b"")
    monkeypatch.setenv("FFMPEG_BINARY", str(exe))
    assert ffmpeg.resolve_ffmpeg() == str(exe)


def test_path_then_imageio(monkeypatch) -> None:
    monkeypatch.delenv("FFMPEG_BINARY", raising=False)
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: "/bin/" + name)
    assert ffmpeg.resolve_ffmpeg() == "/bin/ffmpeg"


def test_ffprobe_sibling(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    ff = tmp_path / "ffmpeg"
    probe = tmp_path / "ffprobe"
    ff.write_bytes(b"")
    assert ffmpeg.resolve_ffprobe(str(ff)) is None
    probe.write_bytes(b"")
    assert ffmpeg.resolve_ffprobe(str(ff)) == str(probe)


def test_old_names_delegate() -> None:
    from production.formats.wealth import fingerprint_engine

    assert fingerprint_engine.resolve_ffmpeg is ffmpeg.resolve_ffmpeg
