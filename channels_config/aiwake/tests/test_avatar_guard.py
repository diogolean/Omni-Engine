"""Real frames: the nameplate color has to match the model that is speaking."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from channels_config.aiwake.tools.avatar_guard import nameplate_family

FRAMES = Path(__file__).resolve().parent / "fixtures" / "frames"


def test_real_frames_match_their_nameplate() -> None:
    expected = {
        "gemini.jpg": "gemini",
        "llama.jpg": "llama",
        "gpt4o.jpg": "gpt-4o",
        "claude.jpg": "claude",
        "deepseek.jpg": "deepseek",
    }
    for name, family in expected.items():
        assert nameplate_family(Image.open(FRAMES / name)) == family


def test_deepseek_plate_is_not_llama() -> None:
    """The scripted-apologies opening shows DeepSeek while the text model is Llama."""
    seen = nameplate_family(Image.open(FRAMES / "deepseek.jpg"))
    assert seen != "llama"
    assert seen == "deepseek"
