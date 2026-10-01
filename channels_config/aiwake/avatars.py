"""One registry: avatar, model, nameplate, voice, and seat.

Every renderer, CLI path, and TTS call derives the face from the utterance's
model. A puppet, voice, or seat that disagrees with that model raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Avatar:
    family: str
    model_slug: str
    puppet: str
    nameplate: str
    voice: str
    allowed_seats: frozenset[str]
    facing: str


AVATAR_BY_MODEL_FAMILY: dict[str, Avatar] = {
    "gemini": Avatar(
        "gemini",
        "gemini-flash",
        "gemini_cyborg_v2",
        "GEMINI",
        "en-US-BrianNeural",
        frozenset({"left"}),
        "right",
    ),
    "llama": Avatar(
        "llama",
        "llama-70b",
        "llama_cyborg_v2",
        "LLAMA",
        "en-US-ChristopherNeural",
        frozenset({"right"}),
        "left",
    ),
    "gpt-4o": Avatar(
        "gpt-4o",
        "gpt4o",
        "chatgpt_cyborg_v1",
        "CHATGPT",
        "en-US-AndrewNeural",
        frozenset({"left", "right"}),
        "seat",
    ),
    "claude": Avatar(
        "claude",
        "claude-sonnet",
        "claude_cyborg_v1",
        "CLAUDE",
        "en-GB-RyanNeural",
        frozenset({"left", "right"}),
        "seat",
    ),
    "deepseek": Avatar(
        "deepseek",
        "deepseek-chat",
        "deepseek_cyborg_v3",
        "DEEPSEEK",
        "en-US-EricNeural",
        frozenset({"left", "right"}),
        "seat",
    ),
}

_ALIASES = {
    "gemini": "gemini",
    "gemini-flash": "gemini",
    "google/gemini-3.5-flash": "gemini",
    "llama": "llama",
    "llama-70b": "llama",
    "meta-llama/llama-3.3-70b-instruct": "llama",
    "gpt": "gpt-4o",
    "gpt4o": "gpt-4o",
    "gpt-4o": "gpt-4o",
    "chatgpt": "gpt-4o",
    "openai/gpt-4o": "gpt-4o",
    "claude": "claude",
    "claude-sonnet": "claude",
    "anthropic/claude-sonnet-5": "claude",
    "deepseek": "deepseek",
    "deepseek-chat": "deepseek",
    "deepseek/deepseek-chat": "deepseek",
}


def model_family(model: str | None) -> str:
    token = (model or "").strip().lower().replace("_", "-")
    if token in _ALIASES:
        return _ALIASES[token]
    if "llama" in token:
        return "llama"
    if "gemini" in token:
        return "gemini"
    if "deepseek" in token:
        return "deepseek"
    if "claude" in token:
        return "claude"
    if "gpt" in token or "chatgpt" in token or "openai" in token:
        return "gpt-4o"
    return ""


def avatar_for(model: str | None) -> Avatar:
    family = model_family(model)
    avatar = AVATAR_BY_MODEL_FAMILY.get(family)
    if avatar is None:
        raise ValueError(f"unknown model {model!r}")
    return avatar


def facing_for(model: str | None, seat: str) -> str:
    avatar = avatar_for(model)
    if avatar.facing == "seat":
        if seat == "left":
            return "right"
        if seat == "right":
            return "left"
        raise ValueError(f"unknown seat {seat!r}")
    return avatar.facing


def require_pairing(orchestrator: str | None, target: str | None) -> tuple[Avatar, Avatar]:
    """LEFT is the asker. RIGHT is the answerer. Illegal seats raise."""
    left = avatar_for(orchestrator)
    right = avatar_for(target)
    if left.family == right.family:
        raise ValueError(f"mirror match is not allowed: {left.family}")
    if "left" not in left.allowed_seats:
        raise ValueError(f"{left.family} cannot sit on the left")
    if "right" not in right.allowed_seats:
        raise ValueError(f"{right.family} cannot sit on the right")
    return left, right


def character_map_for(
    orchestrator: str | None,
    target: str | None,
    *,
    left_puppet: str | None = None,
    right_puppet: str | None = None,
) -> dict[str, str]:
    left, right = require_pairing(orchestrator, target)
    if left_puppet and left_puppet.strip() != left.puppet:
        raise ValueError(f"puppet {left_puppet!r} is not the {left.family} avatar")
    if right_puppet and right_puppet.strip() != right.puppet:
        raise ValueError(f"puppet {right_puppet!r} is not the {right.family} avatar")
    return {"orchestrator": left.puppet, "target": right.puppet}


def require_asset(puppet: str, puppets_dir: Path) -> Path:
    path = Path(puppets_dir) / puppet
    if not path.is_dir():
        raise FileNotFoundError(f"missing avatar asset {path}")
    return path


def assert_speaker_matches_model(speaker_name: str | None, model_slug: str | None) -> None:
    """A renamed speaker without a new generation from that model is a relabel."""
    speaker = model_family(speaker_name)
    model = model_family(model_slug)
    if not speaker or not model or speaker != model:
        raise ValueError(
            f"speaker {speaker_name!r} does not match model {model_slug!r}; "
            "regenerate the text with that model"
        )


def render_manifest(turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-turn identity. The first orchestrator and target models fix the seats."""
    left_model = ""
    right_model = ""
    for turn in turns:
        role = str(turn.get("role") or "")
        if role in {"orchestrator", "left"} and not left_model:
            left_model = str(turn.get("model_slug") or turn.get("model") or "")
        if role in {"target", "right"} and not right_model:
            right_model = str(turn.get("model_slug") or turn.get("model") or "")
    left, right = require_pairing(left_model, right_model)
    rows = []
    for index, turn in enumerate(turns):
        role = str(turn.get("role") or "")
        seat = "left" if role in {"orchestrator", "left"} else "right"
        model = str(turn.get("model_slug") or turn.get("model") or "")
        assert_speaker_matches_model(str(turn.get("speaker_name") or turn.get("speaker") or ""), model)
        avatar = avatar_for(model)
        if seat == "left" and avatar.family != left.family:
            raise ValueError(f"turn {index} model {avatar.family} is not the left seat")
        if seat == "right" and avatar.family != right.family:
            raise ValueError(f"turn {index} model {avatar.family} is not the right seat")
        rows.append(
            {
                "turn": int(turn.get("turn_index") if turn.get("turn_index") is not None else index),
                "model_slug": model,
                "puppet": avatar.puppet,
                "seat": seat,
                "facing": facing_for(model, seat),
                "nameplate": avatar.nameplate,
                "voice": avatar.voice,
            }
        )
    return {"turns": rows}
