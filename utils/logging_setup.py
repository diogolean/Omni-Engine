# -*- coding: utf-8 -*-
"""Logging setup for CLI entrypoints. Call once from ``main()``, never at import.

Aiwake keeps its own logging under channels_config/aiwake.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

_DEFAULT_NOISY = ("googleapiclient.discovery", "google.auth", "urllib3")


def setup_cli_logging(verbose: bool = False, *, extra_noisy: Iterable[str] = ()) -> None:
    """``HH:MM:SS  LEVEL  message`` to stderr; quiet Google/urllib3 unless verbose."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )
    if not verbose:
        for noisy in (*_DEFAULT_NOISY, *extra_noisy):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def setup_gemini_tool_logging() -> None:
    """``time | message`` (only if nothing configured logging yet), Gemini SDK hushed."""
    from utils.gemini_usage import hush_gemini_sdk_logs  # noqa: PLC0415

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(message)s",
        )
    hush_gemini_sdk_logs()
