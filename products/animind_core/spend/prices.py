"""Verified provider rates for Animindly.

Only rates read from an official source in this change are built in.
A model without a rate is not free: the guard refuses the call.
Operator rows in ``ANIMIND_PRICE_TABLE_JSON`` can add a model. They cannot
lower a built-in rate and cannot set a zero price.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

# Peak USD per 1M tokens, converted to micro-USD (1 USD = 1_000_000 micro-USD).
# Checked 2026-11-07 at https://api-docs.deepseek.com/quick_start/pricing
# Model version named on that page: DeepSeek-V4.1-Flash-1017.
# Peak is the upper bound (off-peak is half). The page says prices can change.
_DEEPSEEK_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"
_DEEPSEEK_DATE = "2026-11-07"
_DEEPSEEK_VERSION = "deepseek-peak-2026-11-07"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class TokenRate:
    provider: str
    model: str
    input_miss_per_million: int
    input_hit_per_million: int
    output_per_million: int
    source: str
    source_date: str
    version: str
    unit: str = "tokens"

    def ceiling_micro(self, input_tokens: int, output_tokens: int) -> int:
        """Worst case: every input token is a cache miss."""
        return estimate_micro(self, input_tokens, output_tokens, cached_tokens=0)


@dataclass(frozen=True)
class UnitRate:
    provider: str
    model: str
    micro_usd: int
    unit: str  # image | audio_second | video_second | call
    source: str
    source_date: str
    version: str


Rate = TokenRate | UnitRate

PUBLISHED: dict[tuple[str, str], Rate] = {
    ("deepseek", "deepseek-flash"): TokenRate(
        "deepseek", "deepseek-flash",
        input_miss_per_million=300_000,  # $0.30 / 1M peak cache miss
        input_hit_per_million=6_000,  # $0.006 / 1M peak cache hit
        output_per_million=1_200_000,  # $1.20 / 1M peak output
        source=_DEEPSEEK_SOURCE, source_date=_DEEPSEEK_DATE, version=_DEEPSEEK_VERSION,
    ),
    ("deepseek", "deepseek-v4-pro"): TokenRate(
        "deepseek", "deepseek-v4-pro",
        input_miss_per_million=1_320_000,  # $1.32 / 1M peak cache miss
        input_hit_per_million=44_000,  # $0.044 / 1M peak cache hit
        output_per_million=3_960_000,  # $3.96 / 1M peak output
        source=_DEEPSEEK_SOURCE, source_date=_DEEPSEEK_DATE, version=_DEEPSEEK_VERSION,
    ),
}


def estimate_micro(rate: TokenRate, input_tokens: int, output_tokens: int, cached_tokens: int | None) -> int:
    """Ceil each component so a positive charge cannot round down to zero."""
    cached = 0 if cached_tokens is None else max(0, int(cached_tokens))
    incoming = max(0, int(input_tokens))
    miss = max(0, incoming - cached)
    hit = min(cached, incoming)
    return (
        _ceil(miss, rate.input_miss_per_million)
        + _ceil(hit, rate.input_hit_per_million)
        + _ceil(max(0, int(output_tokens)), rate.output_per_million)
    )


def _ceil(tokens: int, per_million: int) -> int:
    if tokens <= 0 or per_million <= 0:
        return 0
    return (tokens * per_million + 999_999) // 1_000_000


def micro_to_cents(micro: int) -> int:
    """Integer cents, rounded up. A positive sub-cent estimate still holds 1 cent."""
    if micro <= 0:
        return 0
    return max(1, (micro + 9_999) // 10_000)


def lookup(provider: str, model: str) -> Rate | None:
    key = ((provider or "").strip().lower(), (model or "").strip())
    published = PUBLISHED.get(key)
    extra = _operator_table().get(key)
    if published is not None:
        return published
    return extra


def _operator_table() -> dict[tuple[str, str], Rate]:
    raw = (os.getenv("ANIMIND_PRICE_TABLE_JSON") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"__invalid__": UnitRate("invalid", "invalid", 1, "call", "invalid", "1970-01-01", "invalid")}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("entries"), list):
        return {"__invalid__": UnitRate("invalid", "invalid", 1, "call", "invalid", "1970-01-01", "invalid")}
    version = str(parsed.get("version") or "").strip()
    if not version:
        return {"__invalid__": UnitRate("invalid", "invalid", 1, "call", "invalid", "1970-01-01", "invalid")}
    out: dict[tuple[str, str], Rate] = {}
    for entry in parsed["entries"]:
        rate = _operator_entry(entry, version)
        if rate is None:
            return {"__invalid__": UnitRate("invalid", "invalid", 1, "call", "invalid", "1970-01-01", "invalid")}
        out[(rate.provider, rate.model)] = rate
    return out


def operator_table_invalid() -> bool:
    table = _operator_table()
    return "__invalid__" in table


def _operator_entry(entry: Any, version: str) -> Rate | None:
    if not isinstance(entry, dict):
        return None
    provider = str(entry.get("provider") or "").strip().lower()
    model = str(entry.get("model") or "").strip()
    source = str(entry.get("source") or "").strip()
    source_date = str(entry.get("sourceDate") or "").strip()
    unit = str(entry.get("unit") or "").strip()
    if not provider or not model or not source or not _DATE.match(source_date):
        return None
    if provider in {"invalid"}:
        return None
    if unit == "tokens":
        try:
            miss = int(entry["inputMissMicroPerMillion"])
            hit = int(entry["inputHitMicroPerMillion"])
            output = int(entry["outputMicroPerMillion"])
        except (KeyError, TypeError, ValueError):
            return None
        if min(miss, hit, output) <= 0:
            return None
        return TokenRate(provider, model, miss, hit, output, source, source_date, version)
    if unit not in {"image", "audio_second", "video_second", "call"}:
        return None
    try:
        micro = int(entry["microUsd"])
    except (KeyError, TypeError, ValueError):
        return None
    if micro <= 0:
        return None
    return UnitRate(provider, model, micro, unit, source, source_date, version)
