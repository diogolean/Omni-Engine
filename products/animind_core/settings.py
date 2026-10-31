"""Runtime settings, read from the environment only."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    root = Path(os.getenv("ANIMIND_DATA_DIR") or Path.home() / ".animind-core")
    root.mkdir(parents=True, exist_ok=True)
    return root


def api_key() -> str:
    return (os.getenv("CORE_API_KEY") or "").strip()


def gemini_available() -> bool:
    if (os.getenv("ANIMIND_FORCE_MOCK") or "").strip().lower() in {"1", "true", "yes"}:
        return False
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip():
        return False
    return not gemini_dead_reason()


def gemini_dead_reason() -> str:
    """Why Gemini is off for this process (402 / depleted credit / bad key seen once), else ""."""
    try:
        from production.formats.economic_reel.artefact.provider_health import dead_reason  # noqa: PLC0415
    except ImportError:
        return ""
    return dead_reason("gemini")


def is_billing_error(exc: BaseException) -> bool:
    """A Gemini failure that will not recover by retrying (402, depleted prepay, auth)."""
    try:
        from production.formats.economic_reel.artefact.provider_health import is_billing_or_auth  # noqa: PLC0415
    except ImportError:
        text = f"{exc}".lower()
        return any(n in text for n in ("402", "depleted", "prepayment", "billing", "api key not valid"))
    return is_billing_or_auth(exc)


def personas_online() -> bool:
    """Fetch persona sheets from Wikiquote on a cache miss (``ANIMIND_PERSONAS_OFFLINE=1`` disables)."""
    return (os.getenv("ANIMIND_PERSONAS_OFFLINE") or "").strip().lower() not in {"1", "true", "yes"}
