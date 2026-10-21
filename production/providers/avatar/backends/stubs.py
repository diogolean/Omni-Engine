# -*- coding: utf-8 -*-
"""Extension points that are intentionally not implemented.

Civitai on-site generation and Gemini image generation can be added later
behind :class:`models_avatar.backends.base.GenerationBackend`. These classes
fail loudly. They do not return a placeholder image or call a live API.
"""
from __future__ import annotations

from production.providers.avatar.backends.base import GenerationJob, GenerationResult
from production.providers.avatar.errors import BackendNotImplemented


class CivitaiOnsiteBackend:
    """Future Civitai on-site image generation.

    The public REST API used by :mod:`models_avatar.civitai.client` covers
    model metadata and file download. It does not document a stable
    text-to-image job API. Implement :meth:`generate` when that exists.
    """

    name = "civitai_onsite"

    def generate(self, job: GenerationJob) -> GenerationResult:  # noqa: ARG002
        raise BackendNotImplemented(
            "Civitai on-site generation is not implemented. "
            "Add a GenerationBackend that submits a documented Civitai "
            "image job and writes job.dest. Do not fake the image."
        )


class GeminiImageBackend:
    """Future Gemini image backend.

    When this is implemented, route the call through ``core.google_guardrail``
    so the factory billing caps still apply. This stub does not import or
    call the Gemini client.
    """

    name = "gemini_image"

    def generate(self, job: GenerationJob) -> GenerationResult:  # noqa: ARG002
        raise BackendNotImplemented(
            "Gemini image generation is not implemented. "
            "Wire core.google_guardrail and write job.dest when adding it. "
            "Do not fake the image."
        )
