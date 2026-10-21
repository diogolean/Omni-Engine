# -*- coding: utf-8 -*-
"""Error types for models_avatar.

Retry policy is explicit: prompt and config mistakes are not retried.
Transport failures and selected HTTP statuses are.
"""
from __future__ import annotations


class ModelsAvatarError(RuntimeError):
    """Base error for this package."""


class AvatarConfigError(ModelsAvatarError):
    """An avatar folder or preset is missing or inconsistent."""


class PromptPolicyError(ModelsAvatarError):
    """A prompt violated identity, garment, or safety rules."""


class GenerationError(ModelsAvatarError):
    """The selected backend could not produce an image."""

    retryable = False


class RetryableGenerationError(GenerationError):
    """A generation failure that an unattended batch may try again."""

    retryable = True


class HttpStatusError(GenerationError):
    """HTTP response that is not a successful body."""

    def __init__(self, status: int, method: str, url: str, detail: str) -> None:
        self.status = status
        self.method = method
        self.url = url
        super().__init__(f"HTTP {status} {method} {url}: {detail[:500]}")

    @property
    def retryable(self) -> bool:  # type: ignore[override]
        return self.status in {408, 429, 500, 502, 503, 504}


class BackendNotImplemented(ModelsAvatarError, NotImplementedError):
    """Extension-point backend. There is no placeholder implementation."""


class CivitaiError(ModelsAvatarError):
    """Civitai request failed or the token is missing."""

    retryable = False


class RetryableCivitaiError(CivitaiError):
    retryable = True


def is_retryable(exc: BaseException) -> bool:
    """True when an unattended runner should try the same job again."""
    flag = getattr(exc, "retryable", None)
    return bool(flag)
