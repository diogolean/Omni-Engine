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


def _parse_model(raw: str) -> tuple[str, str] | None:
    """``provider:model`` or a bare OpenRouter model id."""
    text = (raw or "").strip()
    if not text:
        return None
    provider, sep, model = text.partition(":")
    provider = provider.strip().lower()
    model = model.strip()
    if not sep or not model:
        return "openrouter", provider
    return provider, model


def _is_claude(provider: str, model: str) -> bool:
    blob = f"{provider} {model}".lower()
    return provider in {"anthropic", "claude"} or "claude" in blob


def _is_free_model(model: str) -> bool:
    return ":free" in (model or "").lower()


def text_model(channel: str) -> tuple[str, str] | None:
    """``(provider, model)`` from ``PIN_TEXT_MODEL``, or None when unset.

    ``openrouter:meta/model:free`` keeps the model id, including ``:free``.
    A bare model id uses OpenRouter. Unset keeps the writer-primary path.
    """
    from shared.channel_flags import value

    return _parse_model(str(value(channel, "PIN_TEXT_MODEL", "") or ""))


def text_model_chain(channel: str) -> list[tuple[str, str]]:
    """Ordered pin-text models from page_config. Paid and Claude ids are dropped.

    The primary ``PIN_TEXT_MODEL`` is first. ``PIN_TEXT_MODEL_FALLBACKS`` follows.
    A ``:free`` primary keeps only other ``:free`` ids, so a removed model can
    404 and the next free model is tried. An explicit paid primary stays a
    single opt-in call. Claude is never returned. An empty list means the
    channel named a model and every id was refused.
    """
    from shared.channel_flags import value

    primary_raw = str(value(channel, "PIN_TEXT_MODEL", "") or "").strip()
    primary = _parse_model(primary_raw)
    if primary is None:
        return []
    provider, model = primary
    if _is_claude(provider, model):
        log.warning("PIN TEXT | refusing Claude model %s | using template", model)
        return []
    if not _is_free_model(model):
        return [primary]

    extras = value(channel, "PIN_TEXT_MODEL_FALLBACKS", []) or []
    raws = [primary_raw]
    if isinstance(extras, str):
        raws.extend(part.strip() for part in extras.split(",") if part.strip())
    elif isinstance(extras, (list, tuple)):
        raws.extend(str(item).strip() for item in extras if str(item).strip())
    chain: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raws:
        spec = _parse_model(raw)
        if spec is None or spec in seen:
            continue
        spec_provider, spec_model = spec
        if _is_claude(spec_provider, spec_model) or not _is_free_model(spec_model):
            log.warning(
                "PIN TEXT | skipping %s:%s | not a :free model",
                spec_provider,
                spec_model,
            )
            continue
        seen.add(spec)
        chain.append(spec)
    return chain


def pin_text_configured(channel: str) -> bool:
    """True when the channel names a pin text model, even if every id is refused."""
    from shared.channel_flags import value

    if str(value(channel, "PIN_TEXT_MODEL", "") or "").strip():
        return True
    extras = value(channel, "PIN_TEXT_MODEL_FALLBACKS", None)
    return bool(extras)


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
