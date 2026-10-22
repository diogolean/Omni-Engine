# TODO M10: move this Aiwake-specific module to products/aiwake/.
"""Exclusive character-to-voice reservations for the debate engine."""

from .registry import (
    ANDREW,
    BRIAN,
    CHRISTOPHER,
    ERIC,
    RYAN,
    VoiceCollisionError,
    assign_debater_voices,
    voice_for,
)

__all__ = [
    "ANDREW",
    "BRIAN",
    "CHRISTOPHER",
    "ERIC",
    "RYAN",
    "VoiceCollisionError",
    "assign_debater_voices",
    "voice_for",
]
