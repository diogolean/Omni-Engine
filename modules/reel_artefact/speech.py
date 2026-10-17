# -*- coding: utf-8 -*-
"""Speak year ranges without changing the digits on screen.

ElevenLabs reads ``600-1000`` as "six hundred one thousand". The spoken
string uses "to". The subtitle source keeps the original digits.
"""
from __future__ import annotations

import re

# 3–4 digit spans, and shorter spans when an era word follows.
_YEAR_RANGE = re.compile(
    r"\b(\d{3,4})\s*[-–—]\s*(\d{3,4})\b"
    r"|\b(\d{1,4})\s*[-–—]\s*(\d{1,4})(?=\s*(?:AD|CE|BC|BCE)\b)",
    re.IGNORECASE,
)


def spoken_numbers(text: str) -> str:
    """Return the string sent to TTS. Digits stay digits."""

    def _replace(match: re.Match[str]) -> str:
        start = match.group(1) or match.group(3)
        end = match.group(2) or match.group(4)
        return f"{start} to {end}"

    return _YEAR_RANGE.sub(_replace, text or "")


def subtitle_text(text: str) -> str:
    """Caption text. The original range, digits included, is unchanged."""
    return text or ""
