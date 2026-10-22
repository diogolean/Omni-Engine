# -*- coding: utf-8 -*-
"""Per-channel image source policy.

``free_first`` searches the free libraries and falls through to Flux when
nothing passes. ``free_only`` never calls the generator. ``ai_only`` keeps
the channel on its current generator or owned assets.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

POLICIES: frozenset[str] = frozenset({"free_first", "ai_only", "free_only"})

# Channels that are not listed stay on ``ai_only`` so a shared image hook
# does not change their pipeline.
_DEFAULTS: dict[str, str] = {
    "ancient_knowledge": "free_first",
    "amazing_feed": "free_first",
    "anna_protocol": "free_first",
    "annas_garden": "free_first",
    "wonder_feed": "free_first",
    "aiwake": "ai_only",
    "endless_summer_paradise": "ai_only",
    "master_mei": "ai_only",
}

_ALIASES: dict[str, str] = {
    "annas_garden": "anna_protocol",
    "anna_s_garden": "anna_protocol",
    "the_holistic_legacy": "anna_protocol",
}


def normalize_policy(raw: str | None) -> str | None:
    text = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in POLICIES:
        return text
    return None


def channel_key(channel: str | None) -> str:
    return (channel or "").strip().lower().replace("-", "_")


def default_policy(channel: str | None) -> str:
    key = channel_key(channel)
    if key in _DEFAULTS:
        return _DEFAULTS[key]
    alias = _ALIASES.get(key)
    if alias and alias in _DEFAULTS:
        return _DEFAULTS[alias]
    return "ai_only"


def reel_free_images_enabled(page_cfg: dict | None = None) -> bool:
    """Real photographs for reel beats. Off unless the flag is set.

    Cards keep ``IMAGE_SOURCE_POLICY``. A reel beat stays on Flux until
    ``REEL_FREE_IMAGES=1`` (or ``REEL_IMAGE_SOURCE=free``), because a Commons
    hit on the place name is often the wrong picture.
    """
    env = (os.getenv("REEL_FREE_IMAGES") or os.getenv("REEL_IMAGE_SOURCE") or "").strip()
    raw = env
    if not raw and isinstance(page_cfg, dict):
        raw = str(
            page_cfg.get("REEL_FREE_IMAGES") or page_cfg.get("REEL_IMAGE_SOURCE") or ""
        )
    return raw.strip().lower() in {"1", "true", "yes", "on", "free", "free_first"}


def resolve_policy(channel: str | None, page_cfg: dict | None = None) -> str:
    """Page config wins. Otherwise the built-in default for that channel."""
    cfg = page_cfg
    if cfg is None and channel_key(channel):
        cfg = _load_page_cfg(channel_key(channel))
    if isinstance(cfg, dict):
        explicit = cfg.get("IMAGE_SOURCE_POLICY", cfg.get("image_source_policy"))
        parsed = normalize_policy(str(explicit) if explicit is not None else "")
        if parsed:
            return parsed
    return default_policy(channel)


def _load_page_cfg(channel: str) -> dict:
    lookup = _ALIASES.get(channel, channel)
    try:
        from production.pipeline.channel_context import load_page_context

        ctx = load_page_context(lookup)
        page_cfg = getattr(ctx, "page_cfg", None)
        return dict(page_cfg) if isinstance(page_cfg, dict) else {}
    except Exception as exc:  # noqa: BLE001 — unknown channel, use defaults
        logger.debug("image source policy: no page config for %s (%s)", channel, exc)
        return {}
