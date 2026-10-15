"""Which side the first speaker sits on.

The orchestrator asks first. Until now it always sat on the left. The seat is
now a per-episode choice: the first speaker sits on ``first_seat`` and the
answerer sits on the other side.

Hard rules come from the avatar registry and are never relaxed here:

* Gemini sits LEFT only.
* Llama sits RIGHT only.
* Claude, GPT-4o and DeepSeek may sit on either side.

So a pair that includes Gemini or Llama has exactly one legal ``first_seat``
and is never randomised. Only pairs among Claude, GPT-4o and DeepSeek are.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Literal

from .avatars import SEATS, avatar_for, other_seat

Seat = Literal["left", "right"]
RANDOM = "random"


def seats_for(first_seat: str) -> dict[str, str]:
    """Seat per role for an episode whose first speaker sits on ``first_seat``."""
    return {"orchestrator": first_seat, "target": other_seat(first_seat)}


def legal_first_seats(orchestrator: str | None, target: str | None) -> tuple[str, ...]:
    """Seats the first speaker may take, given both models' fixed sides."""
    asker = avatar_for(orchestrator)
    answerer = avatar_for(target)
    if asker.family == answerer.family:
        raise ValueError(f"mirror match is not allowed: {asker.family}")
    return tuple(
        seat
        for seat in SEATS
        if seat in asker.allowed_seats and other_seat(seat) in answerer.allowed_seats
    )


def is_randomizable(orchestrator: str | None, target: str | None) -> bool:
    """True only for pairs where both sides are legal for both models."""
    return len(legal_first_seats(orchestrator, target)) == len(SEATS)


def choose_first_seat(
    orchestrator: str | None,
    target: str | None,
    *,
    requested: str | None = None,
    rng: random.Random | None = None,
) -> str:
    """Pick the first speaker's seat.

    ``requested`` may be ``left``, ``right``, ``random`` or ``None`` (random).
    A fixed request that breaks a hard rule raises. A random request on a pair
    with a fixed side returns that side.
    """
    legal = legal_first_seats(orchestrator, target)
    if not legal:
        raise ValueError(f"no legal seating for {orchestrator!r} vs {target!r}")
    wanted = (requested or RANDOM).strip().lower()
    if wanted in SEATS:
        if wanted not in legal:
            raise ValueError(
                f"{orchestrator!r} first on the {wanted} breaks a fixed seat; legal: {legal}"
            )
        return wanted
    if wanted != RANDOM:
        raise ValueError(f"first seat must be left, right or random, got {requested!r}")
    return (rng or random.Random()).choice(legal)


def plan_first_seats(
    pairs: Sequence[tuple[str, str]],
    *,
    seed: int | None = None,
) -> tuple[str, ...]:
    """First seat for each episode of a batch, aiming at half of them on the right.

    Pairs with a fixed side keep it. The free pairs are drawn so that the whole
    batch lands as close to half-right as the fixed pairs allow, in random order.
    """
    rng = random.Random(seed)
    legal = [legal_first_seats(orchestrator, target) for orchestrator, target in pairs]
    seats: list[str | None] = [options[0] if len(options) == 1 else None for options in legal]
    free = [index for index, seat in enumerate(seats) if seat is None]
    fixed_right = sum(1 for seat in seats if seat == "right")
    want_right = round(len(pairs) / 2)
    need = max(0, min(len(free), want_right - fixed_right))
    rng.shuffle(free)
    for position, index in enumerate(free):
        seats[index] = "right" if position < need else "left"
    if any(seat is None for seat in seats) or any(
        seat not in options for seat, options in zip(seats, legal)
    ):
        raise ValueError("could not seat every episode legally")
    return tuple(str(seat) for seat in seats)


__all__ = [
    "RANDOM",
    "SEATS",
    "choose_first_seat",
    "is_randomizable",
    "legal_first_seats",
    "other_seat",
    "plan_first_seats",
    "seats_for",
]
