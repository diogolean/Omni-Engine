# -*- coding: utf-8 -*-
"""Language-model cost of one long episode: the planning estimate and the measured bill.

Edge TTS is free and the ffmpeg render runs on the machine, so the language
models are the whole bill. ``estimate_episode_cost`` prices the plan before
anyone speaks. ``actual_episode_cost`` prices the tokens the room recorded,
rejected drafts included. Neither is an invoice: the rates come from the
channel config.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from products.aiwake.settings import LongFormatPreset, ModelTokenRate

from .plan import EpisodePlan

# Long-form checks reject more drafts than a short clip does.
_RETRY_FACTOR = 1.5
# Persona, output contract, subject and the director's brief per call, fitted
# to the token ledger of an offline 6-minute episode. The host also carries
# the memory brief, so its base is larger.
_HOST_BASE_TOKENS = 1400
_GUEST_BASE_TOKENS = 950
# The room replays every earlier line as chat history: about 33 tokens of
# text plus the message wrapper.
_TOKENS_PER_PRIOR_LINE = 37
_HOST_OUTPUT_TOKENS = 30
_GUEST_OUTPUT_TOKENS = 45
_UNKNOWN_RATE = ModelTokenRate(input_usd_per_million=3.0, output_usd_per_million=15.0)
# Checked in order: the first family whose words all appear in the slug wins.
_FAMILY_RATES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("claude",), "claude-sonnet"),
    (("gpt",), "gpt4o"),
    (("llama",), "llama-70b"),
    (("deepseek", "r1"), "deepseek-r1"),
    (("deepseek",), "deepseek-chat"),
    (("gemini", "pro"), "gemini-pro"),
    (("gemini",), "gemini-flash"),
)


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


@dataclass(frozen=True)
class CostActual:
    usd: float
    calls: int
    input_tokens: int
    output_tokens: int
    seats: dict[str, dict[str, object]] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "usd": round(self.usd, 4),
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "seats": {role: dict(entry) for role, entry in self.seats.items()},
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
    """Estimate the model bill for ``plan`` at the configured rates.

    The host speaks at exchange ``e`` over ``2e`` earlier lines and the guest
    over ``2e + 1``; the closing verdict is one more host call over every line.
    """
    exchanges = plan.turns
    host_calls = exchanges + 1
    host_prior = sum(2 * exchange for exchange in range(exchanges)) + 2 * exchanges
    guest_prior = sum(2 * exchange + 1 for exchange in range(exchanges))
    host_in = _RETRY_FACTOR * (_HOST_BASE_TOKENS * host_calls + _TOKENS_PER_PRIOR_LINE * host_prior)
    guest_in = _RETRY_FACTOR * (_GUEST_BASE_TOKENS * exchanges + _TOKENS_PER_PRIOR_LINE * guest_prior)
    host_out = _RETRY_FACTOR * host_calls * _HOST_OUTPUT_TOKENS
    guest_out = _RETRY_FACTOR * exchanges * _GUEST_OUTPUT_TOKENS
    if offline:
        usd = 0.0
        note = "Offline stub. Edge TTS and the local render add no model bill."
    else:
        usd = _price(rate_for(preset, orchestrator), host_in, host_out, thinking=True)
        usd += _price(rate_for(preset, target), guest_in, guest_out, thinking=True)
        note = (
            f"Planning estimate for about {exchanges} exchanges with a {_RETRY_FACTOR:g}x retry "
            "allowance, language models only. Edge TTS is free and ffmpeg runs on the machine."
        )
    return CostEstimate(
        usd=round(usd, 4),
        input_tokens=int(round(host_in + guest_in)),
        output_tokens=int(round(host_out + guest_out)),
        orchestrator=orchestrator,
        target=target,
        note=note,
    )


def actual_episode_cost(
    usage: Mapping[str, Mapping[str, object]] | None,
    preset: LongFormatPreset,
    *,
    offline: bool = False,
) -> CostActual:
    """Price the tokens the room recorded per seat.

    OpenRouter's completion count already includes thinking tokens. The
    Gemini SDK's count does not, so a direct Google seat gets the rate's
    ``output_factor``.
    """
    seats: dict[str, dict[str, object]] = {}
    total = 0.0
    calls = prompt_total = completion_total = 0
    for role, entry in (usage or {}).items():
        model = str(entry.get("model") or "")
        provider = str(entry.get("provider") or "")
        prompt = int(entry.get("prompt_tokens") or 0)
        completion = int(entry.get("completion_tokens") or 0)
        count = int(entry.get("calls") or 0)
        usd = 0.0 if offline or provider == "offline" else _price(
            rate_for(preset, model),
            prompt,
            completion,
            thinking=provider == "google",
        )
        seats[str(role)] = {
            "model": model,
            "provider": provider,
            "calls": count,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "usd": round(usd, 5),
        }
        total += usd
        calls += count
        prompt_total += prompt
        completion_total += completion
    note = (
        "Offline stub: the tokens are what a live run would send, billed at $0."
        if offline
        else "Measured from the provider token counts at the channel's configured rates."
    )
    return CostActual(
        usd=round(total, 4),
        calls=calls,
        input_tokens=prompt_total,
        output_tokens=completion_total,
        seats=seats,
        note=note,
    )


def rate_for(preset: LongFormatPreset, name: str) -> ModelTokenRate:
    """The configured rate for an alias or provider slug.

    An unknown slug, or a family missing from the config, is priced like the
    expensive opt-in, so a surprise seat cannot look cheaper than it is.
    """
    token = (name or "").strip().lower()
    rates = preset.model_rates
    for key in (token, token.rsplit("/", 1)[-1]):
        if key in rates:
            return rates[key]
    for needles, key in _FAMILY_RATES:
        if all(needle in token for needle in needles):
            return rates.get(key) or rates.get("claude-sonnet") or _UNKNOWN_RATE
    return rates.get("claude-sonnet") or _UNKNOWN_RATE


def _price(rate: ModelTokenRate, input_tokens: float, output_tokens: float, *, thinking: bool) -> float:
    factor = rate.output_factor if thinking else 1.0
    return (
        input_tokens * rate.input_usd_per_million
        + output_tokens * factor * rate.output_usd_per_million
    ) / 1_000_000.0


__all__ = [
    "CostActual",
    "CostEstimate",
    "actual_episode_cost",
    "estimate_episode_cost",
    "rate_for",
]
