"""Machine paths come from env keys, and the old defaults still hold when unset."""
from __future__ import annotations

import importlib
from pathlib import Path

import utils.pipeline_paths as pp


def test_drive_root_default_and_override(monkeypatch, tmp_path):
    monkeypatch.delenv("OMNI_DRIVE_ROOT", raising=False)
    monkeypatch.setattr(pp, "_parse_dotenv_file", lambda name: None)
    assert pp.drive_root() == Path(r"G:\My Drive\Z sosFiles\Z_act\@ NETWORK")
    monkeypatch.setenv("OMNI_DRIVE_ROOT", str(tmp_path))
    assert pp.drive_root() == tmp_path


def test_windows_factory_root_keeps_historical_layout():
    assert pp.WINDOWS_FACTORY_ROOT.parts[-2:] == (
        "@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT",
        "Unified Multi-Page Factory",
    )


def test_ffmpeg_override(monkeypatch, tmp_path):
    monkeypatch.delenv("FFMPEG_BINARY", raising=False)
    assert pp.ffmpeg_binary_override() is None
    monkeypatch.setenv("FFMPEG_BINARY", str(tmp_path / "missing"))
    assert pp.ffmpeg_binary_override() is None
    exe = tmp_path / "ffmpeg"
    exe.write_bytes(b"")
    monkeypatch.setenv("FFMPEG_BINARY", str(exe))
    assert pp.ffmpeg_binary_override() == exe


def test_fingerprint_engine_uses_ffmpeg_binary(monkeypatch, tmp_path):
    exe = tmp_path / "ffmpeg"
    exe.write_bytes(b"")
    monkeypatch.setenv("FFMPEG_BINARY", str(exe))
    from core.utils import fingerprint_engine

    assert fingerprint_engine.resolve_ffmpeg() == str(exe)


def test_windows_fonts_dir(monkeypatch):
    monkeypatch.delenv("WINDIR", raising=False)
    assert pp.windows_fonts_dir() == Path(r"C:\Windows") / "Fonts"
    monkeypatch.setenv("WINDIR", r"D:\Win")
    assert pp.windows_fonts_dir() == Path(r"D:\Win") / "Fonts"


def test_find_font_prefers_font_dirs_then_repo(monkeypatch, tmp_path):
    monkeypatch.delenv("FONT_DIRS", raising=False)
    repo_font = next(pp.repo_fonts_dir().rglob("*.ttf"))
    assert pp.find_font(repo_font.name) is not None
    custom = tmp_path / repo_font.name
    custom.write_bytes(b"x")
    monkeypatch.setenv("FONT_DIRS", str(tmp_path))
    assert pp.find_font(repo_font.name) == custom
    assert pp.find_font("definitely-not-a-font-xyz.ttf") is None


def test_ancient_knowledge_voice_reference_is_repo_relative():
    mod = importlib.import_module("channels_config.ancient_knowledge.page_config")
    text = Path(mod.VOICE_REFERENCE_TEXT)
    assert text.is_file()
    assert "C:\\dev" not in mod.VOICE_REFERENCE_AUDIO
    assert Path(mod.VOICE_REFERENCE_AUDIO).parent == text.parent


def test_no_hardcoded_drive_letters_in_engine_code():
    root = Path(pp.__file__).resolve().parents[1]
    allowed = {root / "utils" / "pipeline_paths.py", root / "core" / "animator" / "asset_generator.py"}
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        if rel.parts[0] in {"tests", ".git"} or path in allowed:
            continue
        if rel.parts[:2] == ("channels_config", "aiwake"):
            continue  # Aiwake stays self-contained.
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in ('r"G:\\', 'r"C:\\dev', 'r"C:\\Users', 'r"C:\\Windows\\Fonts', '"C:/Windows/Fonts', 'r"C:\\ffmpeg'):
            if needle in text:
                offenders.append(f"{rel}: {needle}")
    assert offenders == []
