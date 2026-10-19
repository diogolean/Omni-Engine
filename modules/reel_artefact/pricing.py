# -*- coding: utf-8 -*-
"""Verified Gemini image prices, standard paid tier, October 2026.

Source: https://ai.google.dev/gemini-api/docs/pricing and the image
generation model list. Flux Schnell is billed at $0.003 here (DeepInfra
charges about $0.002 for a 4-step 1 MP still, so the cap stays conservative).
``gemini-3.1-flash-lite-image`` and ``gemini-nano-banana-2.1`` are
$0.0336. ``gemini-2.5-flash-image`` ($0.039) was removed from the live list
on 2026-10-19: Nano Banana is off by default (``REEL_NANOBANANA_MAX_PER_REEL``
is 0) and only the lite ids may run when it is switched on.
``REEL_NANOBANANA_MODELS`` replaces the list.
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

# Cheapest first. A single REEL_NANOBANANA_MODEL override is prepended.
NANOBANANA_MODELS: tuple[str, ...] = (
    "gemini-3.1-flash-lite-image",
    "gemini-nano-banana-2.1",
)

_FREE_PROVIDERS = {
    "commons",
    "met",
    "smithsonian",
    "openverse",
    "europeana",
    "wikimedia",
    "cache",
    "library",
}


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
    reuse = 0
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
        if provider == "reuse":
            reuse += 1
        elif provider in _FREE_PROVIDERS:
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
        "reuse": reuse,
        "nanobanana_usd": round(nano_usd, 4),
        "flux_usd": round(flux * FLUX_USD, 4),
        "image_usd": round(nano_usd + flux * FLUX_USD, 4),
    }


def generation_bill(path, extra_generations: int = 0, *, attempts: int = 0) -> tuple[str, int]:
    """Cost-tracker key and call count for one beat, retries included.

    A real photograph or a reused still is ``image_commons`` at $0. Nano
    Banana is one call even when a later beat copies the cached file. Flux
    counts the first call plus each retry.
    """
    from modules.reel_artefact.source import source_meta

    extras = max(0, int(extra_generations or 0))
    meta = source_meta(path)
    provider = str(meta.get("provider") or "")
    if not provider:
        calls = max(int(attempts or 0), extras + (1 if path else 0))
        if calls <= 0:
            return "", 0
        return "image_flux_schnell", calls
    if provider == "nanobanana":
        model = str(meta.get("model_id") or "")
        key = "image_nanobanana_lite" if price_for_model(model) <= 0.034 else "image_nanobanana"
        return key, 1
    if provider == "reuse":
        return "image_reuse", 1
    if provider in _FREE_PROVIDERS:
        return "image_commons", 1
    return "image_flux_schnell", 1 + extras
