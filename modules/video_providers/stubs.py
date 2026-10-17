# -*- coding: utf-8 -*-
"""Inactive video backends.

They share ``VideoProvider`` so a channel can name them in config, but
``capabilities(request)`` refuses the request before any network call.
Prices below are the research notes, not a live quote.
"""
from __future__ import annotations

from pathlib import Path

from modules.video_providers.base import VideoProvider
from modules.video_providers.errors import ProviderInactiveError
from modules.video_providers.types import (
    CostEstimate,
    ProviderCapabilities,
    VideoJob,
    VideoRequest,
)


def _inactive(name: str) -> ProviderInactiveError:
    return ProviderInactiveError(
        f"{name} is a skeleton backend and is not active. No request was sent."
    )


class GeminiVeoProvider(VideoProvider):
    """Gemini API Veo. Not wired and not called."""

    name = "gemini"

    def __init__(self, *, model: str = "veo-3.1") -> None:
        self.model = (model or "veo-3.1").strip()

    def capabilities(self, request: VideoRequest | None = None) -> ProviderCapabilities:
        caps = ProviderCapabilities(
            provider=self.name,
            active=False,
            model=self.model,
            models=("veo-3.1",),
            default_model="veo-3.1",
            quality_model=None,
            durations_secs=(),
            aspects=("16:9", "9:16"),
            resolutions=("720p", "1080p"),
            max_reference_images=0,
            supports_negative_prompt=False,
            supports_seed=False,
            supports_generate_audio=True,
            supports_start_end_frame=False,
            reference_requires_duration=None,
            min_poll_interval_s=10.0,
            poll_backoff=1.5,
            poll_max_interval_s=60.0,
            notes="Skeleton only. Research note: Veo 3.1 Lite about $0.05/s. Not verified live.",
        )
        if request is not None:
            raise _inactive("Gemini Veo")
        return caps

    def submit(self, request: VideoRequest) -> VideoJob:
        self.capabilities(request)
        raise _inactive("Gemini Veo")

    def poll(self, job_id: str) -> VideoJob:
        raise _inactive("Gemini Veo")

    def download(self, job: VideoJob, dest: Path) -> Path:
        raise _inactive("Gemini Veo")

    def estimate_cost(self, request: VideoRequest) -> CostEstimate:
        seconds = float(request.duration or 0.0)
        usd = round(0.05 * seconds, 4) if seconds else None
        return CostEstimate(
            provider=self.name,
            model=self.model,
            credits=None,
            usd=usd,
            unit="usd_per_second",
            note=(
                "Skeleton. Research note lists Gemini Veo 3.1 Lite at $0.05/s. "
                "Not checked against a current price card, and this backend sends nothing."
            ),
        )


class KlingFalProvider(VideoProvider):
    """Kling via its official API or fal.ai. Not wired and not called.

    ElevenLabs does not expose Kling on the Flows API (web app only).
    """

    name = "kling"

    def __init__(self, *, model: str = "kling") -> None:
        self.model = (model or "kling").strip()

    def capabilities(self, request: VideoRequest | None = None) -> ProviderCapabilities:
        caps = ProviderCapabilities(
            provider=self.name,
            active=False,
            model=self.model,
            models=("kling", "fal"),
            default_model="kling",
            quality_model=None,
            durations_secs=(),
            aspects=("16:9", "9:16", "1:1"),
            resolutions=("720p", "1080p"),
            max_reference_images=0,
            supports_negative_prompt=True,
            supports_seed=False,
            supports_generate_audio=False,
            supports_start_end_frame=False,
            reference_requires_duration=None,
            min_poll_interval_s=10.0,
            poll_backoff=1.5,
            poll_max_interval_s=60.0,
            notes=(
                "Skeleton only. Research note: Kling official API about $0.084–$0.168/s. "
                "Not verified live."
            ),
        )
        if request is not None:
            raise _inactive("Kling/fal")
        return caps

    def submit(self, request: VideoRequest) -> VideoJob:
        self.capabilities(request)
        raise _inactive("Kling/fal")

    def poll(self, job_id: str) -> VideoJob:
        raise _inactive("Kling/fal")

    def download(self, job: VideoJob, dest: Path) -> Path:
        raise _inactive("Kling/fal")

    def estimate_cost(self, request: VideoRequest) -> CostEstimate:
        seconds = float(request.duration or 0.0)
        low = round(0.084 * seconds, 4) if seconds else None
        high = round(0.168 * seconds, 4) if seconds else None
        return CostEstimate(
            provider=self.name,
            model=self.model,
            credits=None,
            usd=None,
            usd_low=low,
            usd_high=high,
            unit="usd_per_second",
            note=(
                "Skeleton. Research note lists the Kling official API at $0.084–$0.168/s. "
                "Not checked against a current price card, and this backend sends nothing."
            ),
        )
