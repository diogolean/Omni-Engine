# -*- coding: utf-8 -*-
"""Resolve a provider name to a backend. Skeletons are returned, not called."""
from __future__ import annotations

from modules.video_providers.base import VideoProvider
from modules.video_providers.elevenlabs import DEFAULT_MODEL, ElevenLabsVideoProvider
from modules.video_providers.errors import VideoRequestError
from modules.video_providers.http import Transport
from modules.video_providers.stubs import GeminiVeoProvider, KlingFalProvider

_ELEVEN = {"elevenlabs", "eleven", "el"}
_GEMINI = {"gemini", "gemini_veo", "veo"}
_KLING = {"kling", "fal", "kling_fal"}


def get_provider(
    name: str,
    *,
    model: str | None = None,
    allow_seedance: bool = False,
    api_key: str | None = None,
    transport: Transport | None = None,
) -> VideoProvider:
    key = (name or "").strip().lower()
    if key in _ELEVEN:
        return ElevenLabsVideoProvider(
            model=model or DEFAULT_MODEL,
            api_key=api_key,
            allow_seedance=allow_seedance,
            transport=transport,
        )
    if key in _GEMINI:
        return GeminiVeoProvider(model=model or "veo-3.1")
    if key in _KLING:
        return KlingFalProvider(model=model or "kling")
    raise VideoRequestError(
        f"Unknown video provider {name!r}. "
        "Known names: elevenlabs (active), gemini, kling."
    )
