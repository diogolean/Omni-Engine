# -*- coding: utf-8 -*-
"""Per-channel pin spend. Flags live on page_config; this module names none."""

from __future__ import annotations

import logging
from contextlib import AbstractContextManager

log = logging.getLogger(__name__)


def paid_image_fallback(channel: str) -> bool:
    """True when a pin may call Flux after the free sources miss.

    Channels that omit the flag keep the previous paid fallback.
    """
    from shared.channel_flags import flag

    return flag(channel, "PIN_PAID_IMAGE_FALLBACK", True)


def text_model(channel: str) -> tuple[str, str] | None:
    """``(provider, model)`` from ``PIN_TEXT_MODEL``, or None when unset.

    ``openrouter:meta/model:free`` keeps the model id, including ``:free``.
    A bare model id uses OpenRouter. Unset keeps the writer-primary path.
    """
    from shared.channel_flags import value

    raw = str(value(channel, "PIN_TEXT_MODEL", "") or "").strip()
    if not raw:
        return None
    provider, sep, model = raw.partition(":")
    provider = provider.strip().lower()
    model = model.strip()
    if not sep or not model:
        return "openrouter", provider
    return provider, model


def spend_cap_usd(channel: str) -> float | None:
    """Hard cap for one pin, or None when the channel sets no cap."""
    from shared.channel_flags import value

    if not (channel or "").strip():
        return None
    raw = value(channel, "PIN_SPEND_CAP_USD", None)
    if raw is None or raw == "":
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def pin_scope(channel: str) -> AbstractContextManager[None]:
    """Context manager that applies this channel's pin cap, if it has one."""
    from production.cost.spend_ledger import pin_cap_scope

    return pin_cap_scope(spend_cap_usd(channel))


def guard_pin_spend(
    channel: str,
    estimate: float,
    *,
    provider: str,
    model: str,
    source: str,
) -> None:
    """Refuse a paid pin call before the request when the channel cap is below it.

    A ``$0`` estimate passes a ``$0`` cap. Channels with no cap are not checked
    here; the reel cap still applies inside the ledger.
    """
    cap = spend_cap_usd(channel)
    if cap is None:
        return
    extra = max(0.0, float(estimate or 0.0))
    if extra <= cap + 1e-12:
        return
    from production.cost.spend_ledger import SpendCapExceeded

    msg = (
        f"PIN SPEND CAP | provider={provider} | model={model} | src={source} | "
        f"next=${extra:.5f} | cap=${cap:.3f} | no request sent"
    )
    log.error(msg)
    raise SpendCapExceeded(msg)
