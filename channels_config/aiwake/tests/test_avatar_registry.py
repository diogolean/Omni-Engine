"""Seat, puppet, voice, and relabel rules for the avatar registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from channels_config.aiwake.avatars import (
    AVATAR_BY_MODEL_FAMILY,
    assert_speaker_matches_model,
    avatar_for,
    character_map_for,
    model_family,
    render_manifest,
    require_asset,
    require_pairing,
)

VALID = (
    ("gemini-flash", "llama-70b"),
    ("gemini-flash", "gpt4o"),
    ("gemini-flash", "claude-sonnet"),
    ("gemini-flash", "deepseek-chat"),
    ("gpt4o", "llama-70b"),
    ("gpt4o", "claude-sonnet"),
    ("gpt4o", "deepseek-chat"),
    ("claude-sonnet", "llama-70b"),
    ("claude-sonnet", "gpt4o"),
    ("claude-sonnet", "deepseek-chat"),
    ("deepseek-chat", "llama-70b"),
    ("deepseek-chat", "gpt4o"),
    ("deepseek-chat", "claude-sonnet"),
)


@pytest.mark.parametrize("left,right", VALID)
def test_valid_pairings(left: str, right: str) -> None:
    asker, answerer = require_pairing(left, right)
    seats = character_map_for(left, right)
    assert seats["orchestrator"] == asker.puppet
    assert seats["target"] == answerer.puppet
    assert asker.voice
    assert answerer.nameplate


@pytest.mark.parametrize(
    "left,right",
    [
        ("llama-70b", "gpt4o"),
        ("llama-70b", "gemini-flash"),
        ("gpt4o", "gemini-flash"),
        ("claude-sonnet", "gemini-flash"),
        ("deepseek-chat", "gemini-flash"),
        ("gemini-flash", "gemini-flash"),
        ("llama-70b", "llama-70b"),
        ("gpt4o", "gpt4o"),
    ],
)
def test_invalid_pairings_raise(left: str, right: str) -> None:
    with pytest.raises(ValueError):
        require_pairing(left, right)


def test_unknown_model_raises() -> None:
    with pytest.raises(ValueError):
        avatar_for("mistral")


def test_puppet_must_match_model() -> None:
    with pytest.raises(ValueError):
        character_map_for("gemini-flash", "llama-70b", left_puppet="deepseek_cyborg_v3")
    with pytest.raises(ValueError):
        character_map_for("claude-sonnet", "gpt4o", right_puppet="llama_cyborg_v2")


def test_voice_is_the_model_voice() -> None:
    assert avatar_for("llama-70b").voice != avatar_for("deepseek-chat").voice
    assert avatar_for("openai/gpt-4o").puppet == "chatgpt_cyborg_v1"


def test_missing_asset_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        require_asset("llama_cyborg_v2", tmp_path)


def test_relabel_without_regeneration_raises() -> None:
    with pytest.raises(ValueError):
        assert_speaker_matches_model("DeepSeek", "meta-llama/llama-3.3-70b-instruct")
    assert_speaker_matches_model("Llama 3.3 70B", "meta-llama/llama-3.3-70b-instruct")


def test_manifest_records_the_real_model() -> None:
    manifest = render_manifest(
        [
            {"role": "orchestrator", "speaker_name": "Gemini", "model_slug": "gemini-flash", "turn_index": 0},
            {"role": "target", "speaker_name": "Llama", "model_slug": "llama-70b", "turn_index": 1},
        ]
    )
    assert manifest["turns"][0]["nameplate"] == "GEMINI"
    assert manifest["turns"][0]["seat"] == "left"
    assert manifest["turns"][0]["facing"] == "right"
    assert manifest["turns"][1]["puppet"] == "llama_cyborg_v2"
    assert manifest["turns"][1]["voice"] == AVATAR_BY_MODEL_FAMILY["llama"].voice


def test_manifest_rejects_a_relabeled_turn() -> None:
    with pytest.raises(ValueError):
        render_manifest(
            [
                {
                    "role": "orchestrator",
                    "speaker_name": "DeepSeek",
                    "model_slug": "meta-llama/llama-3.3-70b-instruct",
                    "turn_index": 0,
                },
                {"role": "target", "speaker_name": "Claude", "model_slug": "claude-sonnet", "turn_index": 1},
            ]
        )


def test_family_aliases() -> None:
    assert model_family("GPT-4o") == "gpt-4o"
    assert model_family("Claude Sonnet 5") == "claude"
