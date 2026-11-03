# -*- coding: utf-8 -*-
"""YouTube failures both production and distribution have to catch.

The upload client still lives in ``distribution.publishers.api``. This
exception is the shared type so a caller can write ``except`` without
importing that package.
"""
from __future__ import annotations


class YouTubeQuotaExceededError(RuntimeError):
    """Raised when YouTube's ~20 videos/day channel upload cap (or a related
    quota/rate error) is hit mid-upload. Callers should catch this distinctly
    from other upload failures: queue the remaining videos via
    ``queue_pending_upload`` / ``queue_pending_upload_from_envelope`` and let
    the pipeline complete gracefully instead of crashing."""
