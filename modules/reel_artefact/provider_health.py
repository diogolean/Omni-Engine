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
_LAST_WRITER: tuple[str, str] = ("", "")
_RESOLVED_CLAUDE = ""
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
    global _PREFLIGHT_OK, _LAST_WRITER, _RESOLVED_CLAUDE
    _DEAD.clear()
    _PREFLIGHT_OK = False
    _LAST_WRITER = ("", "")
    _RESOLVED_CLAUDE = ""


def remember_text_writer(provider: str, model: str) -> None:
    """The provider that actually wrote the Ancient Knowledge narration."""
    global _LAST_WRITER
    _LAST_WRITER = ((provider or "").strip().lower(), (model or "").strip())


def text_writer_banner() -> str:
    """Humanizer line for the provider that wrote the script. Empty before that."""
    provider, model = _LAST_WRITER
    if not provider:
        return ""
    pretty = {
        "openrouter": "OpenRouter",
        "deepseek": "DeepSeek",
        "anthropic": "Anthropic",
        "gemini": "Gemini",
    }.get(provider, provider)
    if model:
        return f"{pretty} `{model}`"
    return pretty


def resolved_claude_model() -> str:
    """A Claude id that answered /v1/messages. Empty until the probe finds one."""
    return _RESOLVED_CLAUDE


def complete_ak_text(
    prompt: str,
    callers: list[tuple[str, Callable[[str], str] | None]],
    *,
    accept: Callable[[str], bool] | None = None,
    refine: Callable[[str], str] | None = None,
) -> str:
    """Try each provider once. A draft that fails ``accept`` falls through.

    Billing marks that provider dead and moves on. ``refine`` runs first, so
    a reasoning blob can be reduced to the narration before the check.
    """
    from shared.retry import is_network_error, retry_sleep
    from modules.reel_artefact.script_grounding import strip_model_wrapper

    for name, call in callers:
        if call is None or provider_dead(name):
            continue
        raw = _one_call(name, call, prompt, is_network_error, retry_sleep)
        if not raw:
            continue
        logger.info(
            "AK text draft | provider=%s | model=%s | %s",
            name,
            _model_label(name),
            _preview(raw),
        )
        text = strip_model_wrapper(raw)
        if refine is not None:
            text = (refine(text) or "").strip()
        if text and (accept is None or accept(text)):
            remember_text_writer(name, _model_label(name))
            logger.info(
                "AK text | provider=%s | model=%s | chars=%d",
                name,
                _model_label(name),
                len(text),
            )
            return text
        if text:
            logger.info(
                "AK text rejected | provider=%s | model=%s | chars=%d",
                name,
                _model_label(name),
                len(text),
            )
    return ""


def _preview(text: str, limit: int = 200) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def _model_label(provider: str) -> str:
    import config as app_config

    name = (provider or "").strip().lower()
    if name == "openrouter":
        return (os.getenv("OPENROUTER_TEXT_MODEL") or "qwen/qwen3.5-flash-02-23").strip()
    if name == "deepseek":
        return str(getattr(app_config, "DEEPSEEK_FLASH_MODEL", "") or "")
    if name == "anthropic":
        return _RESOLVED_CLAUDE or str(getattr(app_config, "CLAUDE_MODEL", "") or "")
    if name == "gemini":
        return (
            os.getenv("GEMINI_ECONOMIC_BRAIN_MODEL")
            or str(getattr(app_config, "GEMINI_ECONOMIC_BRAIN_MODEL", "") or "")
        )
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
        extra_body={"reasoning": {"exclude": True, "effort": "none"}},
    )
    raw = (resp.choices[0].message.content or "").strip()
    logger.info(
        "AK text draft | provider=openrouter | model=%s | %s",
        model,
        _preview(raw),
    )
    from modules.reel_artefact.script_grounding import strip_model_wrapper

    return strip_model_wrapper(raw)


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
    live_names: list[str] = []
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
            live_names.append(name)
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
        "text preflight | live=%d configured=%d dead=%s live_providers=%s",
        live,
        configured,
        ",".join(sorted(_DEAD)) or "none",
        ",".join(live_names) or "none",
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


def _message_not_found(exc: BaseException) -> bool:
    code = getattr(exc, "status_code", None)
    if code is None:
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
    if code == 404:
        return True
    text = str(exc).lower()
    return "not_found" in text or "not found" in text or "404" in text


def _claude_message(client: object, model: str) -> str:
    message = client.messages.create(  # type: ignore[attr-defined]
        model=model,
        max_tokens=8,
        messages=[{"role": "user", "content": _PROBE}],
    )
    chunks = [
        getattr(block, "text", "")
        for block in (getattr(message, "content", []) or [])
    ]
    return "\n".join(part for part in chunks if part).strip()


def _claude_candidates(client: object, skip: set[str]) -> list[str]:
    """Sonnet, then Haiku, from /v1/models. A list response is not a live probe."""
    try:
        page = client.models.list()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return []
    models = list(getattr(page, "data", None) or page or [])
    ranked: list[tuple[int, str]] = []
    for item in models:
        mid = str(getattr(item, "id", "") or "").strip()
        low = mid.lower()
        if not mid or mid in skip or "claude" not in low:
            continue
        if "latest" in low:
            rank = 5
        elif "sonnet-4" in low or "sonnet-4-5" in low:
            rank = 0
        elif "sonnet" in low:
            rank = 1
        elif "haiku" in low:
            rank = 2
        else:
            rank = 3
        ranked.append((rank, mid))
    ranked.sort(key=lambda pair: pair[0])
    return [mid for _rank, mid in ranked]


def _probe_anthropic() -> str:
    """POST /v1/messages. A 200 from /v1/models is not enough.

    ``claude-3-5-sonnet-latest`` answers the model list and 404s on messages.
    On that 404, try the configured alias and then ids from the model list.
    """
    global _RESOLVED_CLAUDE
    from anthropic import Anthropic

    import config as app_config

    client = Anthropic(api_key=app_config.ANTHROPIC_API_KEY)
    primary = (
        app_config.CLAUDE_MODEL
        or getattr(app_config, "SAFE_CLAUDE_MODEL", "")
        or "claude-sonnet-4-5"
    ).strip()
    safe = (getattr(app_config, "SAFE_CLAUDE_MODEL", "") or "claude-sonnet-4-5").strip()
    ordered: list[str] = []
    for model in (primary, safe):
        if model and model not in ordered:
            ordered.append(model)
    tried: set[str] = set()
    last: BaseException | None = None
    for model in ordered:
        tried.add(model)
        try:
            text = _claude_message(client, model)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if _message_not_found(exc):
                logger.info("anthropic messages 404 | model=%s", model)
                continue
            raise
        _RESOLVED_CLAUDE = model
        return text or "ok"
    for model in _claude_candidates(client, tried):
        try:
            text = _claude_message(client, model)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if _message_not_found(exc):
                logger.info("anthropic messages 404 | model=%s", model)
                continue
            raise
        _RESOLVED_CLAUDE = model
        logger.info("anthropic model resolved | %s", model)
        return text or "ok"
    if last is not None:
        raise last
    raise RuntimeError("anthropic messages probe failed")
