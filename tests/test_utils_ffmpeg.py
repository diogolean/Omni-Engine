"""utils.ffmpeg is the one resolver; old entry points delegate to it."""
from __future__ import annotations

import utils.ffmpeg as uff


def test_override_wins(monkeypatch, tmp_path):
    exe = tmp_path / "ffmpeg"
    exe.write_bytes(b"")
    monkeypatch.setenv("FFMPEG_BINARY", str(exe))
    assert uff.resolve_ffmpeg() == str(exe)


def test_path_then_imageio(monkeypatch):
    monkeypatch.delenv("FFMPEG_BINARY", raising=False)
    monkeypatch.setattr(uff.shutil, "which", lambda name: "/bin/" + name)
    assert uff.resolve_ffmpeg() == "/bin/ffmpeg"


def test_ffprobe_sibling(monkeypatch, tmp_path):
    monkeypatch.setattr(uff.shutil, "which", lambda name: None)
    ff = tmp_path / "ffmpeg"
    probe = tmp_path / "ffprobe"
    ff.write_bytes(b"")
    assert uff.resolve_ffprobe(str(ff)) is None
    probe.write_bytes(b"")
    assert uff.resolve_ffprobe(str(ff)) == str(probe)


def test_old_names_delegate():
    from core.utils import fingerprint_engine

    assert fingerprint_engine.resolve_ffmpeg is uff.resolve_ffmpeg
