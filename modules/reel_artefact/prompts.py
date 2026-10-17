# -*- coding: utf-8 -*-
"""Documentary Flux lines. Named artefacts are not invented in the frame."""
from __future__ import annotations

_LOOK = (
    "Photoreal documentary photograph from a real camera, 35mm film grain, "
    "natural colour, real location. Simple calm composition. "
    "Not a render, not CGI, not an illustration."
)
_FORBID_OBJECT = (
    "Do not depict a complete mechanism, clean brass gears, or an invented "
    "gold figurine. No close-up of a named artefact."
)


def context_flux_prompt(narration: str, *, name: str = "") -> str:
    """A context shot after the real object and the edit both failed."""
    spoken = " ".join((narration or "").split())
    subject = (
        "A museum hall, a display case seen from across the room, "
        "or the landscape of the region. The object itself stays out of the frame."
    )
    named = f" Do not depict {name}." if name else ""
    return f"{_LOOK} {subject}{named} {_FORBID_OBJECT} Narration: {spoken}."


def event_flux_prompt(
    narration: str,
    *,
    name: str = "",
    period: str = "",
    negatives: tuple[str, ...] | list[str] = (),
) -> str:
    """Period scene. The artefact is not the subject."""
    spoken = " ".join((narration or "").split())
    when = period or "the period of the narration"
    bans = [str(item).strip().rstrip(".") for item in negatives if str(item).strip()]
    if not any("scuba" in item.lower() for item in bans):
        bans.append("no modern scuba")
    if not any("boat" in item.lower() for item in bans):
        bans.append("no modern boats")
    named = f" Do not depict {name}." if name else ""
    ban_line = ", ".join(bans) + "."
    return (
        f"{_LOOK} A historical scene in {when}. People, boats, and tools match "
        f"that period. Calm sea or shoreline, uncluttered frame."
        f"{named} {ban_line} {_FORBID_OBJECT} Narration: {spoken}."
    )
