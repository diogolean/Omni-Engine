"""Rendered seat: left faces right, right faces left, forbidden sides raise."""

from __future__ import annotations

import pytest

from channels_config.aiwake.animator_bridge import build_speaker_styles
from channels_config.aiwake.avatars import facing_for, screen_placement
from core.animator.compositor import place_body_offset


def test_gemini_asker_draws_left_facing_right() -> None:
    place = screen_placement("gemini-flash", "left")
    assert place["facing"] == "right"
    assert place["side"] == "left"
    assert place["center_x"] < place["frame_width"] / 2
    styles = build_speaker_styles({"orchestrator": "gemini_cyborg_v2", "target": "llama_cyborg_v2"})
    gemini = next(style for style in styles if style.character_id == "gemini_cyborg_v2")
    assert gemini.facing == "right"
    offset = place_body_offset("right", 540, 1.0)
    assert offset + 540 < 1080 / 2


def test_llama_answerer_draws_right_facing_left() -> None:
    place = screen_placement("llama-70b", "right")
    assert place["facing"] == "left"
    assert place["side"] == "right"
    assert place["center_x"] > place["frame_width"] / 2
    offset = place_body_offset("left", 540, 1.0)
    assert offset + 540 > 1080 / 2


def test_gemini_right_and_llama_left_raise() -> None:
    with pytest.raises(ValueError):
        facing_for("gemini-flash", "right")
    with pytest.raises(ValueError):
        screen_placement("gemini-flash", "right")
    with pytest.raises(ValueError):
        facing_for("llama-70b", "left")
    with pytest.raises(ValueError):
        build_speaker_styles({"orchestrator": "llama_cyborg_v2", "target": "gemini_cyborg_v2"})


@pytest.mark.parametrize(
    "model,puppet",
    [
        ("gpt4o", "chatgpt_cyborg_v1"),
        ("claude-sonnet", "claude_cyborg_v1"),
        ("deepseek-chat", "deepseek_cyborg_v3"),
    ],
)
def test_full_view_models_draw_on_either_side(model: str, puppet: str) -> None:
    left = screen_placement(model, "left")
    right = screen_placement(model, "right")
    assert left["facing"] == "right" and left["center_x"] < 540
    assert right["facing"] == "left" and right["center_x"] > 540
    styles = build_speaker_styles({"orchestrator": puppet, "target": "llama_cyborg_v2"})
    asker = next(style for style in styles if style.character_id == puppet)
    assert asker.facing == "right"
