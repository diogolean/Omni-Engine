# -*- coding: utf-8 -*-
"""Documentary Flux lines. Named artefacts are not invented in the frame."""
from __future__ import annotations

from production.formats.economic_reel.artefact.flux_policy import safe_flux_prompt

_LOOK = (
    "Photoreal documentary photograph from a real camera, 35mm film grain, "
    "natural colour, real location. Simple calm composition. "
    "Not a render, not CGI, not an illustration."
)
_FORBID_OBJECT = (
    "Do not depict a complete mechanism, clean brass gears, or an invented "
    "gold figurine. No close-up of a named artefact."
)


def context_flux_prompt(
    narration: str,
    *,
    name: str = "",
    negatives: tuple[str, ...] | list[str] = (),
) -> str:
    """A Flux-safe context shot after the real object and the edit both failed."""
    return safe_flux_prompt(
        narration,
        name=name,
        negatives=negatives,
        kind="object",
        attempt=1,
    )


def event_flux_prompt(
    narration: str,
    *,
    name: str = "",
    period: str = "",
    negatives: tuple[str, ...] | list[str] = (),
) -> str:
    """Period sea or shoreline. The artefact is not the subject."""
    return safe_flux_prompt(
        narration,
        name=name,
        negatives=negatives,
        period=period,
        event=True,
        kind="scene",
    )
