"""Explicit spend limits. A missing value is not a default quota."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

_INT = re.compile(r"^\d+$")


@dataclass(frozen=True)
class Limits:
    budget_cents: int
    user_cents: int
    timeout_ms: int
    max_retries: int
    rate_per_min: int
    concurrency: int
    max_input_tokens: int
    max_output_tokens: int
    max_total_tokens: int
    max_images: int
    max_audio_seconds: int
    max_video_seconds: int


def _integer(name: str, low: int, high: int) -> int | None:
    raw = os.getenv(name)
    if raw is None or not _INT.match(raw.strip()):
        return None
    value = int(raw.strip())
    if value < low or value > high:
        return None
    return value


def generation_block() -> str | None:
    """``None`` when paid calls are switched on. Otherwise a fail-closed block."""
    raw = os.getenv("ANIMIND_GENERATION")
    if raw is None or raw.strip() == "":
        return "disabled"
    if raw.strip() == "1":
        return None
    return "unavailable"


def read_limits() -> Limits | None:
    budget = _integer("ANIMIND_SPEND_BUDGET_CENTS", 0, 1_000_000_000)
    user = _integer("ANIMIND_SPEND_USER_CENTS", 0, 1_000_000_000)
    timeout = _integer("ANIMIND_SPEND_TIMEOUT_MS", 1, 120_000)
    retries = _integer("ANIMIND_SPEND_MAX_RETRIES", 0, 8)
    rate = _integer("ANIMIND_SPEND_RATE_PER_MIN", 1, 600)
    concurrency = _integer("ANIMIND_SPEND_CONCURRENCY", 1, 1000)
    max_in = _integer("ANIMIND_SPEND_MAX_INPUT_TOKENS", 1, 2_000_000)
    max_out = _integer("ANIMIND_SPEND_MAX_OUTPUT_TOKENS", 1, 2_000_000)
    max_total = _integer("ANIMIND_SPEND_MAX_TOTAL_TOKENS", 1, 4_000_000)
    images = _integer("ANIMIND_SPEND_MAX_IMAGES", 0, 100)
    audio = _integer("ANIMIND_SPEND_MAX_AUDIO_SEC", 0, 3_600)
    video = _integer("ANIMIND_SPEND_MAX_VIDEO_SEC", 0, 3_600)
    values = (budget, user, timeout, retries, rate, concurrency, max_in, max_out, max_total, images, audio, video)
    if any(item is None for item in values):
        return None
    if max_in + max_out > max_total:  # type: ignore[operator]
        return None
    return Limits(
        budget_cents=budget, user_cents=user, timeout_ms=timeout, max_retries=retries,
        rate_per_min=rate, concurrency=concurrency, max_input_tokens=max_in,
        max_output_tokens=max_out, max_total_tokens=max_total, max_images=images,
        max_audio_seconds=audio, max_video_seconds=video,
    )


def request_over_cap(body: dict, limits: Limits) -> str | None:
    """Reject a caller ask that is already over the configured unit cap."""
    def ask(*keys: str) -> float | None:
        for key in keys:
            value = body.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            return float(value)
        return None

    checks = (
        ("input", ask("maxInputTokens"), limits.max_input_tokens),
        ("output", ask("maxOutputTokens"), limits.max_output_tokens),
        ("total", ask("maxTotalTokens"), limits.max_total_tokens),
        ("images", ask("maxImages"), limits.max_images),
        ("audio", ask("maxAudioSeconds"), limits.max_audio_seconds),
        ("video", ask("maxVideoSeconds", "targetDurationSec", "duration"), limits.max_video_seconds),
    )
    for name, value, cap in checks:
        if value is not None and value > cap:
            return name
    return None
