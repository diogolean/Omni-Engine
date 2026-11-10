"""Runtime settings, read from the environment only."""
from __future__ import annotations

import os
from pathlib import Path


def run_budget_usd() -> float:
    """Hard cap for one avatar build or one benchmark run. Default US$0.10."""
    raw = (os.getenv("ANIMIND_RUN_BUDGET_USD") or "").strip()
    if not raw:
        return 0.10
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.10


def data_dir() -> Path:
    root = Path(os.getenv("ANIMIND_DATA_DIR") or Path.home() / ".animind-core")
    root.mkdir(parents=True, exist_ok=True)
    return root


def api_key() -> str:
    return (os.getenv("CORE_API_KEY") or "").strip()


def force_mock() -> bool:
    return (os.getenv("ANIMIND_FORCE_MOCK") or "").strip().lower() in {"1", "true", "yes"}


def dry_run() -> bool:
    """Unpaid mock generation for hosting checks. Never opens a paid socket."""
    return (os.getenv("ANIMIND_DRY_RUN") or "").strip().lower() in {"1", "true", "yes"}


def cors_origins() -> list[str]:
    raw = (os.getenv("ANIMIND_CORS_ORIGINS") or "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def gemini_available() -> bool:
    if force_mock():
        return False
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip():
        return False
    return not gemini_dead_reason()


def deepseek_available() -> bool:
    if force_mock():
        return False
    return bool((os.getenv("DEEPSEEK_API_KEY") or "").strip())


def script_llm_available() -> bool:
    """Whether at least one paid script provider can be selected."""
    return deepseek_available() or gemini_available()


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
