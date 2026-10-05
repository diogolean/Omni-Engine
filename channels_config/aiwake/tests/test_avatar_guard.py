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


def test_held_uploads_are_cleared_when_the_plate_matches() -> None:
    from channels_config.aiwake.tools.avatar_guard import CLEARED_HELD_IDS, turn_visual_reason

    assert len(CLEARED_HELD_IDS) == 48
    assert len(set(CLEARED_HELD_IDS)) == 48
    # The expected face is in the video, just not inside the old center band.
    samples = [{"seen": "gemini", "center_x": 600, "frame": Image.new("RGB", (1080, 1920))}]
    assert turn_visual_reason("gemini", samples, "left") == ""
    # A different face, clearly on the left seat, still fails.
    wrong = [{"seen": "llama", "center_x": 200, "frame": Image.new("RGB", (1080, 1920))}]
    assert turn_visual_reason("gemini", wrong, "left") == "avatar_mismatch"
    # An empty read is not a failure. That was the false positive.
    assert turn_visual_reason("gemini", [], "left") == ""
    assert "_KnJUrdA9Xk" in CLEARED_HELD_IDS
    assert "8h4lif5wQ98" in CLEARED_HELD_IDS
    assert "060lEpr4Gdg" in CLEARED_HELD_IDS
    assert "QjXnEGYxj9o" in CLEARED_HELD_IDS


def test_deepseek_plate_is_not_llama() -> None:
    """The scripted-apologies opening shows DeepSeek while the text model is Llama."""
    seen = nameplate_family(Image.open(FRAMES / "deepseek.jpg"))
    assert seen != "llama"
    assert seen == "deepseek"
