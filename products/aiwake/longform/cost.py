# -*- coding: utf-8 -*-
"""Planning cost for one long-form episode.

Edge TTS is free. The ffmpeg render is local. The figure is the language-model
bill at the rates in the channel config, including a retry allowance. It is a
planning estimate, not an invoice.
"""
from __future__ import annotations

from dataclasses import dataclass

from products.aiwake.longform.plan import EpisodePlan
from products.aiwake.settings import LongFormatPreset

_RETRY_FACTOR = 1.2
_BASE_INPUT_TOKENS = 1800
_TOKENS_PER_PRIOR_EXCHANGE = 80
_OUTPUT_TOKENS = 90


@dataclass(frozen=True)
class CostEstimate:
    usd: float
    input_tokens: int
    output_tokens: int
    orchestrator: str
    target: str
    note: str

    def to_dict(self) -> dict[str, object]:
        return {
            "usd": round(self.usd, 4),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "orchestrator": self.orchestrator,
            "target": self.target,
            "note": self.note,
        }


def estimate_episode_cost(
    plan: EpisodePlan,
    preset: LongFormatPreset,
    *,
    orchestrator: str,
    target: str,
    offline: bool = False,
) -> CostEstimate:
    """Estimate the model bill for ``plan`` at the configured rates."""
    calls = plan.turns * 2 * _RETRY_FACTOR
    average_prior = max(0, plan.turns - 1) / 2.0
    input_each = _BASE_INPUT_TOKENS + _TOKENS_PER_PRIOR_EXCHANGE * average_prior
    input_tokens = int(round(calls * input_each))
    output_tokens = int(round(calls * _OUTPUT_TOKENS))
    per_seat_in = input_tokens / 2.0
    per_seat_out = output_tokens / 2.0
    if offline:
        usd = 0.0
        note = "Offline stub. Edge TTS and the local render add no model bill."
    else:
        usd = _seat_cost(preset, orchestrator, per_seat_in, per_seat_out)
        usd += _seat_cost(preset, target, per_seat_in, per_seat_out)
        note = (
            "Planning estimate for the language models only. "
            "Edge TTS is free and ffmpeg runs on the machine."
        )
    return CostEstimate(
        usd=round(usd, 4),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        orchestrator=orchestrator,
        target=target,
        note=note,
    )


def _seat_cost(preset: LongFormatPreset, name: str, input_tokens: float, output_tokens: float) -> float:
    rate = _rate_for(preset, name)
    return (
        input_tokens * rate.input_usd_per_million
        + output_tokens * rate.output_usd_per_million
    ) / 1_000_000.0


def _rate_for(preset: LongFormatPreset, name: str):
    token = (name or "").strip().lower()
    rates = preset.model_rates
    if token in rates:
        return rates[token]
    if "claude" in token:
        return rates["claude-sonnet"]
    if "gpt-4o" in token or "gpt4o" in token:
        return rates["gpt4o"]
    if "llama" in token:
        return rates["llama-70b"]
    if "deepseek-r1" in token or token.endswith("/deepseek-r1"):
        return rates["deepseek-r1"]
    if "deepseek" in token:
        return rates["deepseek-chat"]
    if "gemini" in token and "pro" in token:
        return rates["gemini-pro"]
    if "gemini" in token:
        return rates["gemini-flash"]
    # Unknown slug: price it like the expensive opt-in so a surprise seat
    # cannot look cheaper than it is.
    return rates["claude-sonnet"]
