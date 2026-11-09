"""Admin-only entrance to ``generate_avatar``.

A normal account never reaches the factory. A trusted admin still stops
before the socket when the Gemini key or the published image rate is missing.
"""
from __future__ import annotations

import os
from typing import Any

from .spend.prices import ImageRate, TokenRate, lookup, micro_to_cents

FACTORY_MODEL = "gemini-2.5-flash-image"
DETECT_MODEL = "gemini-2.5-flash"


def image_key_present() -> bool:
    return bool((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())


def published_image_rate() -> Any:
    return lookup("google", FACTORY_MODEL)


def published_rates() -> bool:
    """The complete factory needs image and feature-detection prices."""
    return all(lookup("google", model) is not None for model in (FACTORY_MODEL, DETECT_MODEL))


def run(body: dict[str, Any], owner: str, base_url: str) -> dict[str, Any]:
    """One admin character through ``generate_avatar``."""
    from .generate_avatar import generate_avatar  # noqa: PLC0415

    return generate_avatar(body, owner, base_url)


def keep(owner: str, result: dict[str, Any]) -> dict[str, Any]:
    from .generate_avatar import keep as persist  # noqa: PLC0415

    return persist(owner, result)


def get(owner: str | None, character_id: str) -> dict[str, Any] | None:
    from .generate_avatar import get as load  # noqa: PLC0415

    return load(owner, character_id)


def preflight() -> dict[str, Any]:
    """Readiness for one controlled full-factory character. No provider call."""
    from . import rig  # noqa: PLC0415
    from .spend import ledger  # noqa: PLC0415
    from .spend.limits import read_limits  # noqa: PLC0415

    limits = read_limits()
    image = lookup("google", FACTORY_MODEL)
    detect = lookup("google", DETECT_MODEL)
    image_micro = (
        image.ceiling_micro(limits.max_input_tokens, 1)
        if limits is not None and isinstance(image, ImageRate)
        else None
    )
    detect_micro = (
        detect.ceiling_micro(limits.max_input_tokens, limits.max_output_tokens)
        if limits is not None and isinstance(detect, TokenRate)
        else None
    )
    request_micro = (
        image_micro * rig.MAX_IMAGE_CALLS + detect_micro * rig.MAX_DETECT_CALLS
        if image_micro is not None and detect_micro is not None
        else None
    )
    checks = {
        "generationEnabled": os.getenv("ANIMIND_GENERATION") == "1",
        "credentialProvisioned": image_key_present(),
        "diogoApproved": os.getenv("ANIMIND_AVATAR_SPEND_APPROVED") == "1",
        "ledgerConfigured": ledger.configured(),
        "ledgerReachable": ledger.ping() if ledger.configured() else False,
        "limitsConfigured": limits is not None,
        "imagePricePublished": isinstance(image, ImageRate),
        "detectPricePublished": isinstance(detect, TokenRate),
        "imageAttempts": limits is not None and limits.max_images >= rig.MAX_IMAGE_CALLS,
        "retries": limits is not None and limits.max_retries + 1 >= rig.MAX_RETRIES,
        "requestBudget": (
            limits is not None
            and request_micro is not None
            and limits.request_cents >= micro_to_cents(request_micro)
            and limits.user_cents >= micro_to_cents(request_micro)
            and limits.budget_cents >= micro_to_cents(request_micro)
        ),
    }
    requirements = {
        "generationEnabled": "ANIMIND_GENERATION=1",
        "credentialProvisioned": "GEMINI_API_KEY or GOOGLE_API_KEY",
        "diogoApproved": "ANIMIND_AVATAR_SPEND_APPROVED=1 after Diogo approval",
        "ledgerConfigured": "ANIMIND_SPEND_DATABASE_URL",
        "ledgerReachable": "reachable ANIMIND_SPEND_DATABASE_URL",
        "limitsConfigured": "all ANIMIND_SPEND_* limits including REQUEST_CENTS",
        "imagePricePublished": f"official CORE price for {FACTORY_MODEL}",
        "detectPricePublished": f"official CORE price for {DETECT_MODEL}",
        "imageAttempts": f"ANIMIND_SPEND_MAX_IMAGES>={rig.MAX_IMAGE_CALLS}",
        "retries": f"ANIMIND_SPEND_MAX_RETRIES>={rig.MAX_RETRIES - 1}",
        "requestBudget": "request, UTC user/day, and global cents >= computed request ceiling",
    }
    return {
        "schema": "animind.avatarPreflight.v1",
        "ready": all(checks.values()),
        "providerCall": False,
        "characters": 1,
        "checks": checks,
        "missing": [requirements[name] for name, passed in checks.items() if not passed],
        "models": {
            "image": _rate_doc(image),
            "detectFeatures": _rate_doc(detect),
        },
        "limits": None if limits is None else {
            "requestCeilingMicro": request_micro,
            "requestCeilingCents": None if request_micro is None else micro_to_cents(request_micro),
            "globalCents": limits.budget_cents,
            "userDayCents": limits.user_cents,
            "requestCents": limits.request_cents,
            "maxImages": limits.max_images,
            "maxRetries": limits.max_retries,
            "ratePerMinute": limits.rate_per_min,
            "concurrency": limits.concurrency,
            "maxInputTokensPerCall": limits.max_input_tokens,
            "maxOutputTokensPerCall": limits.max_output_tokens,
        },
        "callPlan": rig.call_plan(),
        "execute": {
            "method": "POST",
            "path": "/v1/avatars",
            "requiredHeaders": [
                "Authorization",
                "X-Animind-User",
                "X-Animind-Admin",
                "Idempotency-Key",
                "X-Request-Id",
            ],
            "body": {"name": "<one character>", "prompt": "<one character>", "generate": True},
        },
    }


def _rate_doc(rate: Any) -> dict[str, Any] | None:
    if rate is None:
        return None
    out = {
        "provider": rate.provider,
        "model": rate.model,
        "unit": rate.unit,
        "source": rate.source,
        "sourceDate": rate.source_date,
        "priceVersion": rate.version,
    }
    if isinstance(rate, ImageRate):
        out |= {
            "inputMicroPerMillion": rate.input_per_million,
            "outputMicroPerImage": rate.output_per_image,
        }
    elif isinstance(rate, TokenRate):
        out |= {
            "inputMissMicroPerMillion": rate.input_miss_per_million,
            "inputHitMicroPerMillion": rate.input_hit_per_million,
            "outputMicroPerMillion": rate.output_per_million,
        }
    return out
