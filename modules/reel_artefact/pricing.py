# -*- coding: utf-8 -*-
"""Verified Gemini image prices, standard paid tier, October 2026.

Source: https://ai.google.dev/gemini-api/docs/pricing and the image
generation model list. Flux Dev/Schnell stays $0.003 in the cost tracker.
``gemini-2.5-flash-image`` (Nano Banana) is listed at $0.039 and was
scheduled to shut down on 2 October 2026. The live call tries it first,
then the current cheap successors.
"""
from __future__ import annotations

from pathlib import Path

FLUX_USD = 0.003

# model id -> USD per generated image at the documented 1K tier
NANOBANANA_USD: dict[str, float] = {
    "gemini-2.5-flash-image": 0.039,
    "models/gemini-2.5-flash-image": 0.039,
    "gemini-3.1-flash-image": 0.067,
    "models/gemini-3.1-flash-image": 0.067,
    "gemini-3.1-flash-lite-image": 0.0336,
    "models/gemini-3.1-flash-lite-image": 0.0336,
    "gemini-nano-banana-2.1": 0.0336,
    "models/gemini-nano-banana-2.1": 0.0336,
}

# Tried in order. The first id is the one Diogo asked to evaluate.
NANOBANANA_MODELS: tuple[str, ...] = (
    "gemini-2.5-flash-image",
    "gemini-nano-banana-2.1",
    "gemini-3.1-flash-lite-image",
)


def price_for_model(model_id: str) -> float:
    key = (model_id or "").strip()
    if key in NANOBANANA_USD:
        return NANOBANANA_USD[key]
    bare = key.split("/")[-1]
    return NANOBANANA_USD.get(bare, NANOBANANA_USD["gemini-2.5-flash-image"])


def hybrid_reel_usd(
    *,
    photo_beats: int,
    generic_beats: int,
    nano_images: int,
    nano_usd: float,
) -> dict:
    """Image spend for one reel. Planner and judge calls are extra."""
    photos = max(0, int(photo_beats))
    generic = max(0, int(generic_beats))
    nanos = max(0, int(nano_images))
    commons = max(0, photos - nanos)
    flux = generic * FLUX_USD
    banana = nanos * float(nano_usd)
    return {
        "commons_stills": commons,
        "nanobanana_images": nanos,
        "flux_stills": generic,
        "commons_usd": 0.0,
        "nanobanana_usd": round(banana, 4),
        "flux_usd": round(flux, 4),
        "image_usd": round(banana + flux, 4),
    }


def tally_paths(paths: list | tuple) -> dict[str, float | int]:
    """Count stills by the source sidecar written next to each file."""
    import json

    commons = 0
    flux = 0
    nano = 0
    nano_usd = 0.0
    for raw in paths:
        side = Path(str(raw) + ".source.json")
        provider = ""
        model = ""
        if side.is_file():
            try:
                payload = json.loads(side.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict):
                provider = str(payload.get("provider") or "")
                model = str(payload.get("model_id") or "")
        if provider in {"commons", "met", "smithsonian"}:
            commons += 1
        elif provider == "nanobanana":
            nano += 1
            nano_usd += price_for_model(model)
        else:
            flux += 1
    return {
        "commons": commons,
        "nanobanana": nano,
        "flux": flux,
        "nanobanana_usd": round(nano_usd, 4),
        "flux_usd": round(flux * FLUX_USD, 4),
        "image_usd": round(nano_usd + flux * FLUX_USD, 4),
    }
