"""Admin-only entrance to ``generate_avatar``.

A normal account never reaches the factory. A trusted admin still stops
before the socket when the Gemini key or the published image rate is missing.
"""
from __future__ import annotations

import os
from typing import Any

from .spend.prices import lookup

FACTORY_MODEL = "gemini-2.5-flash-image"
DETECT_MODEL = "gemini-2.5-flash"


def image_key_present() -> bool:
    return bool((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())


def published_image_rate() -> Any:
    return lookup("google", FACTORY_MODEL)


def published_rates() -> bool:
    """The complete factory needs image and feature-detection prices."""
    return all(lookup("google", model) is not None for model in (FACTORY_MODEL, DETECT_MODEL))


def run(body: dict[str, Any], owner: str, base_url: str) -> dict[str, Any]:
    """One admin character through ``generate_avatar``."""
    from .generate_avatar import generate_avatar  # noqa: PLC0415

    return generate_avatar(body, owner, base_url)
