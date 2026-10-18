# -*- coding: utf-8 -*-
"""Knobs for reel visual QA. Environment wins over the defaults below."""
from __future__ import annotations

import os

RELEVANCE_THRESHOLD: float = 0.70
MAX_ATTEMPTS: int = 3
# Gemini answers inside the window more often than Qwen. Qwen stays the
# fallback when Gemini is down.
MODEL_CHAIN: tuple[str, ...] = ("gemini", "qwen")
JUDGE_TIMEOUT_S: float = 25.0
GEMINI_MODEL: str = "models/gemini-2.5-flash"
MIN_SECONDS_PER_IMAGE: float = 4.0
MAX_SECONDS_PER_IMAGE: float = 5.0
MIN_DURATION_S: float = 15.0
# volumedetect mean below this is treated as a silent or near-silent mix.
MIN_MEAN_VOLUME_DB: float = -45.0
FRAME_SAMPLES: int = 4
WORDS_PER_SECOND: float = 2.4
# Extra judge calls after a timeout or an unparseable reply. The still is
# not regenerated for that, and a real low score still fails closed.
JUDGE_RETRIES: int = 2


def _raw(name: str) -> str:
    env = (os.getenv(name) or "").strip()
    if env:
        return env
    try:
        import config as app_config

        val = getattr(app_config, name, None)
        if val is not None and str(val).strip():
            return str(val).strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _float(name: str, default: float, *, low: float, high: float) -> float:
    raw = _raw(name)
    try:
        val = float(raw) if raw else default
    except (TypeError, ValueError):
        val = default
    return max(low, min(high, val))


def _int(name: str, default: int, *, low: int, high: int) -> int:
    raw = _raw(name)
    try:
        val = int(raw) if raw else default
    except (TypeError, ValueError):
        val = default
    return max(low, min(high, val))


def relevance_threshold() -> float:
    return _float("REEL_QA_RELEVANCE_THRESHOLD", RELEVANCE_THRESHOLD, low=0.0, high=1.0)


def max_attempts() -> int:
    return _int("REEL_QA_MAX_ATTEMPTS", MAX_ATTEMPTS, low=1, high=6)


def model_chain() -> list[str]:
    raw = _raw("REEL_QA_MODEL_CHAIN") or ",".join(MODEL_CHAIN)
    names: list[str] = []
    for part in raw.split(","):
        name = part.strip().lower()
        if name in {"gemini-flash", "gemini_flash", "flash"}:
            name = "gemini"
        if name in {"openrouter", "qwen-vl"}:
            name = "qwen"
        if name and name not in names:
            names.append(name)
    return names or list(MODEL_CHAIN)


def gemini_model() -> str:
    return _raw("REEL_QA_GEMINI_MODEL") or GEMINI_MODEL


def min_seconds_per_image() -> float:
    return _float(
        "REEL_QA_MIN_SECONDS_PER_IMAGE",
        MIN_SECONDS_PER_IMAGE,
        low=1.5,
        high=12.0,
    )


def max_seconds_per_image() -> float:
    """Target hold per still. The operating band is 4–6 seconds."""
    floor = min_seconds_per_image()
    cap = _float(
        "REEL_QA_MAX_SECONDS_PER_IMAGE",
        MAX_SECONDS_PER_IMAGE,
        low=floor,
        high=12.0,
    )
    return max(floor, cap)


def min_duration_s() -> float:
    return _float("REEL_QA_MIN_DURATION_S", MIN_DURATION_S, low=1.0, high=600.0)


def min_mean_volume_db() -> float:
    return _float("REEL_QA_MIN_MEAN_VOLUME_DB", MIN_MEAN_VOLUME_DB, low=-90.0, high=0.0)


def frame_samples() -> int:
    return _int("REEL_QA_FRAME_SAMPLES", FRAME_SAMPLES, low=1, high=12)


def judge_retries() -> int:
    """How many extra judge calls to spend on a technical failure."""
    return _int("REEL_QA_JUDGE_RETRIES", JUDGE_RETRIES, low=0, high=4)


def judge_timeout_s() -> float:
    """Seconds one vision judge call may take. ``REEL_QA_JUDGE_TIMEOUT`` wins."""
    return _float("REEL_QA_JUDGE_TIMEOUT", JUDGE_TIMEOUT_S, low=1.0, high=90.0)
