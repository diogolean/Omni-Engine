# -*- coding: utf-8 -*-
"""Ancient Knowledge text providers. A billing failure is permanent for the run.

Gemini is tried first. OpenRouter, then DeepSeek, then Anthropic, receive the
same prompt. Master Mei does not use this module.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Callable

logger = logging.getLogger(__name__)

_BILLING = (
    "402",
    "401",
    "403",
    "prepayment",
    "credits are depleted",
    "depleted",
    "billing",
    "payment required",
    "invalid api key",
    "api key not valid",
    "permission denied",
    "permission_denied",
    "unauthorized",
    "unauthenticated",
)

_DEAD: dict[str, str] = {}
_PREFLIGHT_OK = False
_PROBE = "Reply with the single word ok."


def is_billing_or_auth(exc: BaseException | None) -> bool:
    """402, a depleted prepaid balance, and auth failures do not recover."""
    if exc is None:
        return False
    text = f"{type(exc).__name__} {exc}".lower()
    if any(needle in text for needle in _BILLING):
        return True
    return "resource_exhausted" in text and any(
        needle in text for needle in ("402", "prepayment", "depleted", "billing", "payment")
    )


def mark_dead(provider: str, reason: str) -> None:
    name = (provider or "").strip().lower()
    if not name:
        return
    _DEAD[name] = str(reason or "dead")[:300]
    logger.warning("provider dead | %s | %s", name, _DEAD[name][:180])


def provider_dead(provider: str) -> bool:
    return (provider or "").strip().lower() in _DEAD


def dead_reason(provider: str) -> str:
    return _DEAD.get((provider or "").strip().lower(), "")


def reset_providers() -> None:
    """Tests start from a live set."""
    global _PREFLIGHT_OK
    _DEAD.clear()
    _PREFLIGHT_OK = False


def complete_ak_text(
    prompt: str,
    callers: list[tuple[str, Callable[[str], str] | None]],
    *,
    accept: Callable[[str], bool] | None = None,
) -> str:
    """Try each provider once. Billing marks it dead and moves on."""
    from modules.reel_artefact.net_retry import is_network_error, retry_sleep

    for name, call in callers:
        if call is None or provider_dead(name):
            continue
        text = _one_call(name, call, prompt, is_network_error, retry_sleep)
        if text and (accept is None or accept(text)):
            logger.info("AK text | provider=%s | chars=%d", name, len(text))
            return text
    return ""


def _one_call(name, call, prompt, is_network_error, retry_sleep) -> str:
    try:
        return (call(prompt) or "").strip()
    except Exception as exc:  # noqa: BLE001
        if is_billing_or_auth(exc):
            mark_dead(name, str(exc))
            logger.error("AK text billing | provider=%s | no retry | %s", name, exc)
            return ""
        if not is_network_error(exc):
            logger.warning("AK text | provider=%s failed (%s)", name, type(exc).__name__)
            return ""
        logger.warning("AK text | provider=%s network (%s) — one retry", name, type(exc).__name__)
        try:
            retry_sleep(1)
            return (call(prompt) or "").strip()
        except Exception as exc2:  # noqa: BLE001
            if is_billing_or_auth(exc2):
                mark_dead(name, str(exc2))
            logger.warning("AK text | provider=%s retry failed (%s)", name, type(exc2).__name__)
            return ""


def openrouter_text(prompt: str, *, timeout: float = 25.0, max_tokens: int = 1800) -> str:
    """One OpenRouter chat completion. The model is not a Gemini id."""
    import config as app_config

    key = (app_config.OPENROUTER_API_KEY or "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY missing")
    model = (os.getenv("OPENROUTER_TEXT_MODEL") or "qwen/qwen3.5-flash-02-23").strip()
    from openai import OpenAI

    client = OpenAI(
        api_key=key,
        base_url=app_config.OPENROUTER_BASE_URL,
        timeout=timeout,
        default_headers={
            "HTTP-Referer": "https://omni-engine.local",
            "X-Title": "omni-engine-ak-text",
        },
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


def preflight_text(
    channel: str,
    *,
    probes: dict[str, Callable[[], str] | None] | None = None,
) -> None:
    """Probe Ancient Knowledge text providers before any image work.

    A channel other than Ancient Knowledge returns immediately. When every
    configured provider is dead, this raises. One live provider is enough,
    and a dead Gemini stays dead for the rest of the run.
    """
    global _PREFLIGHT_OK
    if (channel or "").strip().lower() != "ancient_knowledge":
        return
    if _PREFLIGHT_OK:
        return
    chosen = default_text_probes() if probes is None else probes
    live = 0
    failures: list[str] = []
    configured = 0
    for name, probe in chosen.items():
        if probe is None:
            continue
        configured += 1
        if provider_dead(name):
            failures.append(f"{name}: {dead_reason(name) or 'dead'}")
            continue
        ok, detail = _run_probe(name, probe)
        if ok:
            live += 1
        elif detail:
            failures.append(detail)
    if live == 0:
        detail = "; ".join(failures) or "no text provider configured"
        raise RuntimeError(
            f"text preflight failed — no live provider ({detail}). "
            "Stopping before image work."
        )
    _PREFLIGHT_OK = True
    logger.info(
        "text preflight | live=%d configured=%d dead=%s",
        live,
        configured,
        ",".join(sorted(_DEAD)) or "none",
    )


def default_text_probes() -> dict[str, Callable[[], str]]:
    """Tiny calls for providers that have a key. Unconfigured ones are absent."""
    import config as app_config

    probes: dict[str, Callable[[], str]] = {}
    if (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or app_config.GEMINI_API_KEY):
        probes["gemini"] = _probe_gemini
    if app_config.OPENROUTER_API_KEY:
        probes["openrouter"] = lambda: openrouter_text(_PROBE, timeout=3.0, max_tokens=8)
    if app_config.DEEPSEEK_API_KEY:
        probes["deepseek"] = _probe_deepseek
    if app_config.ANTHROPIC_API_KEY:
        probes["anthropic"] = _probe_anthropic
    return probes


def _run_probe(name: str, probe: Callable[[], str]) -> tuple[bool, str]:
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(probe).result(timeout=4.0)
        return True, ""
    except FuturesTimeout:
        return False, f"{name}: timeout"
    except Exception as exc:  # noqa: BLE001
        if is_billing_or_auth(exc):
            mark_dead(name, str(exc))
        return False, f"{name}: {exc}"


def _probe_gemini() -> str:
    import config as app_config
    from core.google_guardrail import guarded_generate_content, make_guarded_gemini_client

    key = (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or app_config.GEMINI_API_KEY
        or ""
    ).strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing")
    model = (
        os.getenv("GEMINI_ECONOMIC_BRAIN_MODEL")
        or getattr(app_config, "GEMINI_ECONOMIC_BRAIN_MODEL", "")
        or "gemini-2.5-flash"
    )
    client = make_guarded_gemini_client(key)
    resp = guarded_generate_content(
        client,
        model=str(model),
        contents=_PROBE,
        source="provider_health.preflight",
        kind="text",
    )
    return str(getattr(resp, "text", None) or "ok")


def _probe_deepseek() -> str:
    from openai import OpenAI

    import config as app_config

    client = OpenAI(
        api_key=app_config.DEEPSEEK_API_KEY,
        base_url=app_config.DEEPSEEK_BASE_URL,
        timeout=3.0,
    )
    resp = client.chat.completions.create(
        model=app_config.DEEPSEEK_FLASH_MODEL,
        messages=[{"role": "user", "content": _PROBE}],
        max_tokens=8,
        temperature=0,
    )
    return (resp.choices[0].message.content or "").strip()


def _probe_anthropic() -> str:
    from anthropic import Anthropic

    import config as app_config

    client = Anthropic(api_key=app_config.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=app_config.CLAUDE_MODEL,
        max_tokens=8,
        messages=[{"role": "user", "content": _PROBE}],
    )
    chunks = [getattr(block, "text", "") for block in (getattr(message, "content", []) or [])]
    return "\n".join(part for part in chunks if part).strip()
