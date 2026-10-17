# -*- coding: utf-8 -*-
"""Feature flag for real-artefact stills.

Unset means today's Flux path. ``REEL_ARTIFACT_IMAGES=1`` turns the hybrid
on. The provider then defaults to ``chain``: a Nano Banana reference-edit,
then a Commons still when that edit cannot run. Text-only generation is
used only for entities marked safe in the knowledge base.
``REEL_ARTIFACT_IMAGE_PROVIDER`` can force ``commons``, ``nanobanana``,
``flux``, or ``chain``.
"""
from __future__ import annotations

import os

_ON = {"1", "true", "yes", "on", "chain"}
_PROVIDERS = {"commons", "nanobanana", "flux", "chain"}
_OFF = {"0", "off", "false", "no", ""}


def artefact_mode() -> str | None:
    """``None`` when the hybrid is off. Otherwise the provider name."""
    provider = (os.getenv("REEL_ARTIFACT_IMAGE_PROVIDER") or "").strip().lower()
    master = (os.getenv("REEL_ARTIFACT_IMAGES") or "").strip().lower()
    if provider in _OFF and provider != "":
        return None
    if provider in _PROVIDERS:
        return provider
    if master in _ON:
        return "chain"
    return None


def artefact_images_enabled() -> bool:
    return artefact_mode() is not None
