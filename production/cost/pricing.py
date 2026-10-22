# -*- coding: utf-8 -*-
"""List prices and the Google billing cap.

Root ``config`` loads ``.env`` and re-exports these names. The per-reel hard
cap stays on ``production.cost.spend_ledger``.
"""
from __future__ import annotations

import os
import sys

from shared.env import env_bool

# Official 1K list prices used for pre-flight logs + guardrail estimates.
# gemini-2.5-flash-image (Nano Banana) is $0.039. Successors live in
# production.formats.economic_reel.artefact.pricing.
GEMINI_FLASH_IMAGE_1K_USD: float = 0.039
GEMINI_PRO_IMAGE_1K_USD: float = 0.03
GEMINI_PRO_IMAGE_2K_USD: float = 0.134

# ALLOW_GOOGLE_API=false → every Gemini/Imagen call aborts before the HTTP request.
# Unset defaults to True so tests/CI stay unblocked; the factory .env may force false.
ALLOW_GOOGLE_API: bool = env_bool("ALLOW_GOOGLE_API", True)
MAX_GOOGLE_COST_PER_RUN_USD: float = float(os.getenv("MAX_GOOGLE_COST_PER_RUN_USD") or "0.50")
MAX_GOOGLE_RETRIES: int = max(0, int(os.getenv("MAX_GOOGLE_RETRIES") or "2"))

# Estimated USD — dynamic per model via together_image.estimate_together_image_cost().
# Schnell default; override model with TOGETHER_IMAGE_MODEL without changing this floor.
TOGETHER_IMAGE_COST_USD: float = float(os.getenv("TOGETHER_IMAGE_COST_USD") or "0.003")

# Optional overrides (USD); 0 / empty → built-in table in cost_tracker.
RUNPOD_POD_RTX4090_USD_PER_HOUR: float = float(
    os.getenv("RUNPOD_POD_RTX4090_USD_PER_HOUR") or "0"
)
RUNPOD_SERVERLESS_RTX4090_USD_PER_SEC: float = float(
    os.getenv("RUNPOD_SERVERLESS_RTX4090_USD_PER_SEC") or "0"
)


def _facade(name: str):
    """Value on root ``config`` when that module has already published it."""
    me = sys.modules[__name__]
    facade = sys.modules.get("config")
    if facade is not None and facade is not me and name in facade.__dict__:
        return facade.__dict__[name]
    if name in me.__dict__:
        return me.__dict__[name]
    settings = sys.modules.get("production.providers.settings")
    if settings is not None and name in settings.__dict__:
        return settings.__dict__[name]
    raise KeyError(name)


def _listed_flash_image_usd(model_id: str | None) -> float | None:
    try:
        from production.formats.economic_reel.artefact.pricing import NANOBANANA_USD
    except Exception:
        return None
    key = (model_id or "").strip()
    bare = key.split("/")[-1]
    if key in NANOBANANA_USD:
        return float(NANOBANANA_USD[key])
    if bare in NANOBANANA_USD:
        return float(NANOBANANA_USD[bare])
    return None


def estimate_gemini_image_usd(model_id: str | None, *, image_size: str | None = None) -> float:
    """USD / image at the configured size tier (default 1K)."""
    size = (image_size or _facade("GEMINI_IMAGE_SIZE") or "1K").strip().upper()
    low = (model_id or "").lower()
    if "pro" in low or "imagen" in low:
        return (
            _facade("GEMINI_PRO_IMAGE_2K_USD")
            if size == "2K"
            else _facade("GEMINI_PRO_IMAGE_1K_USD")
        )
    listed = _listed_flash_image_usd(model_id)
    if listed is not None:
        return listed
    if ("flash" in low and "image" in low) or "nano-banana" in low:
        return _facade("GEMINI_FLASH_IMAGE_1K_USD")
    if _facade("is_gemini_image_model")(model_id):
        return _facade("GEMINI_FLASH_IMAGE_1K_USD")
    return 0.0
