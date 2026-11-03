# -*- coding: utf-8 -*-
"""Cheap seats by default. Expensive models are a config opt-in."""
from __future__ import annotations

from products.aiwake.settings import AiwakeSettings, LongFormatPreset


class ExpensiveModelRefused(ValueError):
    """Raised when a long-form seat needs ``allow_expensive: true``."""


def is_expensive(name: str, preset: LongFormatPreset) -> bool:
    """True for Claude and the other aliases listed on the preset."""
    token = (name or "").strip().lower()
    if not token:
        return False
    aliases = {item.strip().lower() for item in preset.expensive_aliases}
    if token in aliases:
        return True
    if "claude" in token:
        return True
    if "gpt-4o" in token or "gpt4o" in token or token.startswith("openai/gpt-4"):
        return True
    if "gemini-2.5-pro" in token or "gemini-pro" in token:
        return True
    if "deepseek-r1" in token:
        return True
    return False


def longform_roster(preset: LongFormatPreset) -> tuple[str, ...]:
    """Models ``--random-matchups`` may seat in a long episode.

    The channel's ``cheap_roster``. Opt-in models listed there are dropped
    unless ``allow_expensive`` is true.

    Raises:
        ValueError: Nothing is left to seat.
    """
    roster = tuple(
        name.strip()
        for name in preset.cheap_roster
        if name.strip() and (preset.allow_expensive or not is_expensive(name, preset))
    )
    if len(roster) < 2:
        raise ValueError(
            "long_format.cheap_roster needs at least two seatable models for --random-matchups"
        )
    return roster


def resolve_longform_seats(
    settings: AiwakeSettings,
    *,
    orchestrator: str | None,
    target: str | None,
) -> tuple[str, str]:
    """Pick the pair for one episode.

    Omitted seats use the cheap defaults in the channel config. Passing
    Claude, GPT-4o, Gemini Pro, or DeepSeek R1 raises unless
    ``long_format.allow_expensive`` is true.
    """
    preset = settings.long_format
    left = (orchestrator or preset.default_orchestrator).strip()
    right = (target or preset.default_target).strip()
    from products.aiwake.pipeline import matchup_is_legal

    if not matchup_is_legal(left, right):
        raise ValueError(
            f"illegal long-form seats {left} vs {right}: "
            "Gemini only orchestrates, Llama is only interrogated, and a model cannot face itself"
        )
    if not preset.allow_expensive:
        for name in (left, right):
            if is_expensive(name, preset):
                raise ExpensiveModelRefused(
                    f"{name} is opt-in. Set long_format.allow_expensive: true "
                    "in products/aiwake/aiwake_config.yaml before seating it."
                )
    return left, right
