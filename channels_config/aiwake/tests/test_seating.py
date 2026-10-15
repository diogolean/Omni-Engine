"""First-speaker seat: fixed sides stay fixed, free pairs vary, and everything follows the opener."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from channels_config.aiwake.animator_bridge import build_speaker_styles
from channels_config.aiwake.avatars import (
    character_map_for,
    render_manifest,
    require_pairing,
    seat_of_role,
)
from channels_config.aiwake.seating import (
    choose_first_seat,
    is_randomizable,
    legal_first_seats,
    plan_first_seats,
)
from channels_config.aiwake.tools.avatar_guard import read_first_seat
from core.animator.compositor import _opening_id

FREE = ("claude-sonnet", "gpt4o", "deepseek-chat")


def _turns(first: str, second: str) -> list[dict]:
    return [
        {"role": "orchestrator", "speaker_name": first, "model_slug": first, "turn_index": 0},
        {"role": "target", "speaker_name": second, "model_slug": second, "turn_index": 1},
        {"role": "orchestrator", "speaker_name": first, "model_slug": first, "turn_index": 2},
    ]


def test_gemini_is_left_only_and_llama_is_right_only() -> None:
    assert legal_first_seats("gemini-flash", "claude-sonnet") == ("left",)
    assert legal_first_seats("gemini-flash", "llama-70b") == ("left",)
    assert legal_first_seats("claude-sonnet", "llama-70b") == ("left",)
    assert legal_first_seats("deepseek-chat", "llama-70b") == ("left",)


@pytest.mark.parametrize("first", FREE)
@pytest.mark.parametrize("second", FREE)
def test_free_pairs_can_open_on_either_side(first: str, second: str) -> None:
    if first == second:
        pytest.skip("mirror match")
    assert legal_first_seats(first, second) == ("left", "right")
    assert is_randomizable(first, second)


def test_pairs_with_gemini_or_llama_are_not_randomised() -> None:
    assert not is_randomizable("gemini-flash", "gpt4o")
    assert not is_randomizable("claude-sonnet", "llama-70b")


def test_choose_rejects_a_fixed_seat_violation() -> None:
    with pytest.raises(ValueError):
        choose_first_seat("gemini-flash", "claude-sonnet", requested="right")
    with pytest.raises(ValueError):
        choose_first_seat("claude-sonnet", "llama-70b", requested="right")
    with pytest.raises(ValueError):
        choose_first_seat("claude-sonnet", "gpt4o", requested="middle")


def test_random_choice_keeps_fixed_sides_and_varies_free_pairs() -> None:
    rng = random.Random(7)
    fixed = {choose_first_seat("gemini-flash", "gpt4o", rng=rng) for _ in range(60)}
    assert fixed == {"left"}
    free = [choose_first_seat("claude-sonnet", "gpt4o", rng=rng) for _ in range(400)]
    right_share = free.count("right") / len(free)
    assert 0.4 < right_share < 0.6


def test_batch_plan_is_about_half_right_and_never_breaks_a_fixed_side() -> None:
    pairs = [
        ("claude-sonnet", "gpt4o"),
        ("claude-sonnet", "deepseek-chat"),
        ("gpt4o", "claude-sonnet"),
        ("deepseek-chat", "claude-sonnet"),
        ("claude-sonnet", "llama-70b"),
        ("gemini-flash", "claude-sonnet"),
        ("claude-sonnet", "gpt4o"),
        ("gpt4o", "deepseek-chat"),
        ("deepseek-chat", "gpt4o"),
        ("claude-sonnet", "deepseek-chat"),
    ]
    for seed in range(20):
        seats = plan_first_seats(pairs, seed=seed)
        assert seats.count("right") == 5
        assert seats[4] == "left" and seats[5] == "left"
        for (orchestrator, target), seat in zip(pairs, seats):
            assert seat in legal_first_seats(orchestrator, target)
    assert plan_first_seats(pairs, seed=1) != plan_first_seats(pairs, seed=2)


def test_seat_of_role_follows_the_first_seat() -> None:
    assert seat_of_role("orchestrator", "left") == "left"
    assert seat_of_role("target", "left") == "right"
    assert seat_of_role("orchestrator", "right") == "right"
    assert seat_of_role("target", "right") == "left"


def test_right_first_manifest_seats_and_faces_follow_the_opener() -> None:
    manifest = render_manifest(_turns("claude-sonnet", "gpt4o"), first_seat="right")
    assert manifest["first_seat"] == "right"
    first, second, third = manifest["turns"]
    assert (first["seat"], first["facing"]) == ("right", "left")
    assert (second["seat"], second["facing"]) == ("left", "right")
    assert third["seat"] == "right"
    assert first["nameplate"] == "CLAUDE"


def test_left_first_manifest_is_unchanged() -> None:
    manifest = render_manifest(_turns("claude-sonnet", "gpt4o"))
    assert manifest["first_seat"] == "left"
    assert [item["seat"] for item in manifest["turns"]] == ["left", "right", "left"]


def test_manifest_refuses_fixed_side_models_on_the_wrong_seat() -> None:
    with pytest.raises(ValueError):
        render_manifest(_turns("gemini-flash", "gpt4o"), first_seat="right")
    with pytest.raises(ValueError):
        render_manifest(_turns("gpt4o", "llama-70b"), first_seat="right")


def test_avatar_is_still_the_model() -> None:
    seats = character_map_for("claude-sonnet", "gpt4o", first_seat="right")
    assert seats == {"orchestrator": "claude_cyborg_v1", "target": "chatgpt_cyborg_v1"}
    asker, answerer = require_pairing("claude-sonnet", "gpt4o", first_seat="right")
    assert asker.puppet == "claude_cyborg_v1"
    assert answerer.puppet == "chatgpt_cyborg_v1"


def test_speaker_styles_dock_the_opener_on_the_chosen_side() -> None:
    seats = {"orchestrator": "claude_cyborg_v1", "target": "chatgpt_cyborg_v1"}
    left_first = {s.character_id: s for s in build_speaker_styles(seats, first_seat="left")}
    right_first = {s.character_id: s for s in build_speaker_styles(seats, first_seat="right")}
    assert left_first["claude_cyborg_v1"].facing == "right"
    assert left_first["claude_cyborg_v1"].opens
    assert right_first["claude_cyborg_v1"].facing == "left"
    assert right_first["claude_cyborg_v1"].opens
    assert right_first["chatgpt_cyborg_v1"].facing == "right"
    assert not right_first["chatgpt_cyborg_v1"].opens


def test_fixed_side_models_keep_their_facing_in_styles() -> None:
    seats = {"orchestrator": "gemini_cyborg_v2", "target": "chatgpt_cyborg_v1"}
    by_id = {s.character_id: s for s in build_speaker_styles(seats, first_seat="left")}
    assert by_id["gemini_cyborg_v2"].facing == "right"
    with pytest.raises(ValueError):
        build_speaker_styles(seats, first_seat="right")


def test_camera_opens_on_the_marked_speaker() -> None:
    seats = {"orchestrator": "claude_cyborg_v1", "target": "chatgpt_cyborg_v1"}
    styles = {s.character_id: s for s in build_speaker_styles(seats, first_seat="right")}
    rigs = {"chatgpt_cyborg_v1": object(), "claude_cyborg_v1": object()}
    assert _opening_id(styles, rigs, default="chatgpt_cyborg_v1") == "claude_cyborg_v1"
    unmarked = {key: type(value)(value.character_id, value.label, value.accent_hex, value.facing) for key, value in styles.items()}
    assert _opening_id(unmarked, rigs, default="chatgpt_cyborg_v1") == "chatgpt_cyborg_v1"


def test_guard_reads_the_stored_first_seat(tmp_path: Path) -> None:
    (tmp_path / "s1.json").write_text(json.dumps({"metadata": {"first_seat": "right"}}), encoding="utf-8")
    (tmp_path / "s2.json").write_text(json.dumps({"metadata": {}}), encoding="utf-8")
    assert read_first_seat("s1", tmp_path) == "right"
    assert read_first_seat("s2", tmp_path) == "left"
    assert read_first_seat("missing", tmp_path) == "left"
