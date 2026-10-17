# -*- coding: utf-8 -*-
"""Provider interface shared by ElevenLabs and the inactive backends."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from modules.video_providers.types import (
    CostEstimate,
    ProviderCapabilities,
    UsageSnapshot,
    VideoJob,
    VideoRequest,
)


class VideoProvider(ABC):
    """Submit a clip, poll it, and download the bytes.

    ``capabilities(request)`` must reject a request the backend cannot run
    before any method that spends credits. ``estimate_cost`` is a hint and
    must not call a paid endpoint.
    """

    name: str

    @abstractmethod
    def capabilities(self, request: VideoRequest | None = None) -> ProviderCapabilities:
        """Describe this backend. Validate ``request`` when it is provided."""

    @abstractmethod
    def submit(self, request: VideoRequest) -> VideoJob:
        """Start one generation. Call ``capabilities(request)`` first."""

    @abstractmethod
    def poll(self, job_id: str) -> VideoJob:
        """Read the job once. Callers wait at least ``min_poll_interval_s``."""

    @abstractmethod
    def download(self, job: VideoJob, dest: Path) -> Path:
        """Save the finished media to ``dest`` without overwriting it."""

    @abstractmethod
    def estimate_cost(self, request: VideoRequest) -> CostEstimate:
        """Return a price hint. Unknown vendor prices use ``None`` amounts."""

    def usage_snapshot(self) -> UsageSnapshot | None:
        """Current credit meter, or ``None`` when the backend has no meter."""
        return None

    def public_params(self, request: VideoRequest) -> dict[str, object]:
        """Request fields safe to store in the sidecar (no inline bytes)."""
        images = [
            {"source": image.source, "role": image.role}
            for image in request.reference_images
        ]
        return {
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "duration": request.duration,
            "aspect": request.aspect,
            "resolution": request.resolution,
            "audio": request.audio,
            "start_frame": request.start_frame,
            "end_frame": request.end_frame,
            "reference_images": images,
            "seed": request.seed,
        }
