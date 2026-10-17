# -*- coding: utf-8 -*-
"""Errors for the video provider layer.

``retryable`` is the only signal a caller should use to decide whether another
attempt could succeed. Validation and billing failures are never retryable,
because a retry would either be rejected again or spend credits twice.
"""
from __future__ import annotations


class VideoError(RuntimeError):
    """Base error. Subclasses set ``retryable``."""

    retryable = False

    def __init__(
        self,
        message: str,
        *,
        retryable: bool | None = None,
        job_id: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        if retryable is not None:
            self.retryable = retryable
        self.job_id = job_id
        self.failure_reason = failure_reason


class VideoRequestError(VideoError):
    """The request is invalid. Nothing was submitted and no credits were spent."""

    retryable = False


class VideoDisabledError(VideoRequestError):
    """The channel has video generation turned off."""


class ProviderInactiveError(VideoRequestError):
    """The backend is a skeleton and must not be called."""


class VideoProviderError(VideoError):
    """The provider accepted the request shape, then failed while talking to the API."""


# Failed generations are not charged (ElevenLabs Flows video GET).
# Transient model/platform failures can be retried. Policy, parameters, and
# billing failures will fail the same way again.
RETRYABLE_FAILURE_REASONS = frozenset(
    {
        "timeout",
        "model_error",
        "dependency_failed",
        "internal_error",
    }
)
TERMINAL_FAILURE_REASONS = frozenset(
    {
        "moderated",
        "invalid_parameters",
        "charging_failed",
    }
)


def failure_is_retryable(reason: str | None) -> bool:
    """Map a Flows ``failure_reason`` to retryable or not.

    Unknown reasons are not retryable, so a new permanent code cannot loop.
    """
    key = (reason or "").strip().lower()
    if key in RETRYABLE_FAILURE_REASONS:
        return True
    return False
