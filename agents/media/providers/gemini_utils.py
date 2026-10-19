# -*- coding: utf-8 -*-
"""
Gemini model utilities -- dynamic discovery, validated chains, resilient generation.

Architecture
------------
* Handshake-first: get_active_model_id() queries models.list() BEFORE any
  generateContent call and returns only live, confirmed model IDs.
* Endpoint fallback: make_gemini_client_with_fallback() tries v1beta then v1.
* Validated chains: build_model_chain() includes ONLY models confirmed by
  models.list(). If a model is absent from the live list, it is skipped and the
  available options are logged. Hardcoded fallbacks are a last-resort only when
  models.list() returns nothing at all (e.g. network failure at init time).
* Retry policy: 503/UNAVAILABLE -> exponential backoff (2^n s, max 2 retries).
  404/NOT_FOUND -> advance chain immediately.
  All generateContent calls honour ``google_guardrail`` (kill-switch + USD cap).

REST base (v1beta default):
  https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Literal

logger = logging.getLogger(__name__)

Capability = Literal["text", "image"]

# Validated GA defaults -- used ONLY when models.list() returns nothing.
# Cost-first: flash / flash-image only. Pro SKUs are NEVER last-resort defaults.
_LAST_RESORT_TEXT = [
    "models/gemini-3.5-flash-lite",
    "models/gemini-2.5-flash",
    "models/gemini-2.0-flash",
    "models/gemini-flash-latest",
    "models/gemini-1.5-flash-latest",
    "models/gemini-1.5-flash-002",
]

_LAST_RESORT_IMAGE = [
    "models/gemini-3.1-flash-image",
    "models/gemini-2.5-flash-image",
]

# Keep these for config-level references.
STABLE_TEXT_MODEL = "models/gemini-2.5-flash"
UNIVERSAL_TEXT_FALLBACKS = _LAST_RESORT_TEXT
UNIVERSAL_IMAGE_FALLBACKS = _LAST_RESORT_IMAGE

# Per-model 503 retry budget (additional attempts after the first failure).
# Hard-capped by google_guardrail.MAX_GOOGLE_RETRIES (default 2).
MAX_503_RETRIES = 2


# ---------------------------------------------------------------------------
# Client factory -- v1beta with v1 fallback
# ---------------------------------------------------------------------------

def make_gemini_client(api_key: str, *, api_version: str = "v1beta") -> genai.Client:
    """
    Return a google-genai Client pinned to ``api_version``.
    The SDK sends ``x-goog-api-key`` automatically.
    Honours ``ALLOW_GOOGLE_API`` — raises ``GoogleAPIBlockedError`` when false.
    """
    from core.google_guardrail import assert_google_allowed, make_guarded_gemini_client

    assert_google_allowed(context="gemini_utils.make_gemini_client")
    return make_guarded_gemini_client(
        api_key,
        http_options={"api_version": api_version},
    )


def make_gemini_client_with_fallback(api_key: str) -> genai.Client:
    """
    Try v1beta first (preferred). If models.list() returns nothing or raises,
    silently fall back to the stable v1 endpoint.
    """
    for version in ("v1beta", "v1"):
        try:
            client = make_gemini_client(api_key, api_version=version)
            rows = list(client.models.list())
            if rows:
                logger.info(
                    "Gemini client connected via %s (%d models available).", version, len(rows)
                )
                return client
            logger.warning("Gemini %s: models.list() returned empty; trying next version.", version)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini %s endpoint failed (%s); trying next version.", version, exc)
    # Both failed -- return v1beta anyway and let the caller surface the error naturally.
    logger.error("Both v1beta and v1 Gemini endpoints failed; returning v1beta client anyway.")
    return make_gemini_client(api_key, api_version="v1beta")


# ---------------------------------------------------------------------------
# Model ID helpers
# ---------------------------------------------------------------------------

def _strip_model_id(name: str | None) -> str:
    """Return the bare slug (e.g. ``gemini-1.5-flash-latest``) for deduplication."""
    if not name:
        return ""
    raw = str(name).strip().removeprefix("models/")
    if "/" in raw:
        raw = raw.rsplit("/", maxsplit=1)[-1]
    return raw.strip()


def _full_model_id(slug_or_full: str) -> str:
    """Return ``models/<slug>`` -- the fully-qualified REST path segment."""
    slug = _strip_model_id(slug_or_full)
    return f"models/{slug}" if slug else slug


# ---------------------------------------------------------------------------
# Model introspection helpers
# ---------------------------------------------------------------------------

def _supports_generate_content(model: Any) -> bool:
    actions = getattr(model, "supported_actions", None)
    if not actions:
        return True  # assume capable if field is absent
    for a in actions:
        if str(a).lower().replace("-", "").replace("_", "") == "generatecontent":
            return True
    return False


def _list_models(client: genai.Client) -> list[Any]:
    rows: list[Any] = []
    try:
        for m in client.models.list():
            rows.append(m)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Gemini models.list() failed (%s).", exc)
    return rows


def _parse_version_score(model_id: str) -> float:
    low = model_id.lower()
    m = re.search(r"gemini-(\d+)\.(\d+)", low)
    if m:
        return float(m.group(1)) + int(m.group(2)) / 10.0
    m = re.search(r"gemini[_-]?(\d+)[_-](\d+)", low)
    if m:
        try:
            return float(m.group(1)) + int(m.group(2)) / 10.0
        except ValueError:
            pass
    m = re.search(r"imagen[_-]?(\d+)\.?(\d+)?", low)
    if m:
        minor = int(m.group(2) or "0") if m.lastindex and m.group(2) else 0
        return float(m.group(1)) + minor / 10.0
    return 0.0


def _is_text_candidate(model_id: str) -> bool:
    low = model_id.lower()
    if not low.startswith("gemini"):
        return False
    if "embed" in low or "embedding" in low:
        return False
    if "imagen" in low:
        return False
    if re.search(r"gemini\b.*-image|gemini\b.*image-preview|imagegeneration", low):
        return False
    return True


def _is_image_candidate(model_id: str) -> bool:
    low = model_id.lower()
    # Imagen SKUs 404 on this API project — never select them
    if "imagen" in low:
        return False
    return "gemini" in low and "image" in low


def _text_sort_key(model_id: str) -> tuple[float, float, str]:
    """Higher score = preferred. Cost-first: flash-lite > flash >> pro."""
    low = model_id.lower()
    tier = 1.5
    if "flash-lite" in low:
        tier = 5.0
    elif "flash" in low and "pro" not in low:
        tier = 4.0
    elif "pro" in low:
        tier = 0.5  # deprioritise premium unless explicitly requested
    bonus = 0.05 if "latest" in low else 0.0
    return (tier + bonus, _parse_version_score(model_id), low)


def _image_sort_key(model_id: str) -> tuple[float, float, str]:
    """Higher score = preferred. Cost-first: flash-image >> pro-image."""
    low = model_id.lower()
    pref = 1.0
    if "gemini-3.1-flash-image" in low and "preview" not in low and "lite" not in low:
        pref = 5.0
    elif "gemini-2.5-flash-image" in low and "preview" not in low:
        pref = 4.5
    elif "flash" in low and "image" in low and "pro" not in low:
        pref = 4.0
    elif "gemini-3-pro-image" in low and "preview" not in low:
        pref = 1.0  # premium — lowest preference for auto-discovery
    elif "gemini-3" in low and "image" in low:
        pref = 2.5
    elif "gemini-2" in low and "image" in low:
        pref = 2.0
    if "preview" in low:
        pref -= 0.5
    if "lite" in low and "image" in low:
        pref -= 0.3
    return (pref, _parse_version_score(model_id), low)


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def _should_try_next_model(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    if code == 404:
        return True
    status = str(getattr(exc, "status", "") or "")
    if "404" in status or "NOT_FOUND" in status.upper():
        return True
    msg = str(getattr(exc, "message", None) or exc).lower()
    return "not found" in msg or "not_found" in msg or "no longer available" in msg or "404" in msg


def _is_retryable_503(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    if code == 503:
        return True
    status = str(getattr(exc, "status", "") or "").upper()
    if "503" in status or "UNAVAILABLE" in status:
        return True
    msg = str(exc).lower()
    return "503" in msg or "unavailable" in msg or "high demand" in msg or "overloaded" in msg


def _is_retryable_429(exc: BaseException) -> bool:
    """True for quota / rate-limit / RESOURCE_EXHAUSTED errors."""
    code = getattr(exc, "code", None)
    if code in (429, "429"):
        return True
    status = str(getattr(exc, "status", "") or "").upper()
    if "429" in status or "RESOURCE_EXHAUSTED" in status or "RATE_LIMIT" in status:
        return True
    msg = str(getattr(exc, "message", None) or exc).upper()
    return (
        "429" in msg
        or "RESOURCE_EXHAUSTED" in msg
        or "RATE LIMIT" in msg
        or "RATE_LIMIT" in msg
        or "QUOTA" in msg
        or "TOO MANY REQUESTS" in msg
    )


def is_model_not_found_error(exc: BaseException) -> bool:
    return _should_try_next_model(exc)


# ---------------------------------------------------------------------------
# Handshake: get_active_model_id
# ---------------------------------------------------------------------------

def get_active_model_id(
    client: genai.Client,
    *,
    preference: str | list[str] = "2.5-flash",
    capability_type: Capability = "text",
) -> str:
    """
    Query models.list() live and return the best model matching ``preference``.

    Cost-first: default preference is flash. Pro SKUs are never auto-selected
    unless ``preference`` explicitly names them (premium mode).
    """
    raw_list = _list_models(client)

    candidates: list[str] = []
    for m in raw_list:
        if not _supports_generate_content(m):
            continue
        mid = _strip_model_id(getattr(m, "name", None))
        if not mid:
            continue
        if capability_type == "text" and not _is_text_candidate(mid):
            continue
        if capability_type == "image" and not _is_image_candidate(mid):
            continue
        candidates.append(mid)

    if not candidates:
        all_names = [str(getattr(m, "name", "?")) for m in raw_list]
        logger.error(
            "GEMINI HANDSHAKE FAILED: no generateContent-capable %s models found.\n"
            "All models returned by API:\n%s",
            capability_type,
            "\n".join(all_names) if all_names else "  (empty list -- bad API key or network?)",
        )
        raise RuntimeError(
            f"Gemini models.list() returned no {capability_type} models supporting generateContent. "
            f"Available: {all_names}"
        )

    prefs = [preference] if isinstance(preference, str) else list(preference)
    # Cheap-mode guard: if no preference explicitly asks for pro, drop pro SKUs
    prefs_want_pro = any("pro" in p.lower() for p in prefs)
    if not prefs_want_pro:
        cheap_only = [
            c for c in candidates
            if "pro" not in c.lower() or "flash" in c.lower()
        ]
        if cheap_only:
            candidates = cheap_only

    for pref in prefs:
        pref_lower = pref.lower()
        for mid in candidates:
            if pref_lower in mid.lower():
                result = _full_model_id(mid)
                logger.info(
                    "GEMINI HANDSHAKE OK: selected '%s' (preference='%s').", result, pref
                )
                return result

    # No preference matched -- fall back to best scored candidate (cost-first sort).
    sort_key = _text_sort_key if capability_type == "text" else _image_sort_key
    best = sorted(candidates, key=sort_key, reverse=True)[0]
    result = _full_model_id(best)
    logger.warning(
        "GEMINI HANDSHAKE: none of %s found in live list; "
        "using best available '%s'. Live candidates: %s",
        prefs,
        result,
        [_full_model_id(c) for c in candidates],
    )
    return result


# ---------------------------------------------------------------------------
# Validated model chain builder
# ---------------------------------------------------------------------------

def build_model_chain(
    client: genai.Client,
    *,
    capability_type: Capability,
    preferred: str | None,
) -> list[str]:
    """
    Build a prioritised model chain containing ONLY models confirmed by models.list().

    If models.list() returns nothing (network failure at init), falls back to
    the _LAST_RESORT_* lists with a loud warning -- these are NOT validated.
    """
    preferred_slug = _strip_model_id(preferred) if preferred else ""
    chain: list[str] = []
    seen: set[str] = set()

    def add(slug: str) -> None:
        s = _strip_model_id(slug)
        if not s or s in seen:
            return
        seen.add(s)
        chain.append(_full_model_id(s))

    raw_list = _list_models(client)
    live_slugs: set[str] = set()
    scored: list[tuple[tuple[float, float, str], str]] = []

    for m in raw_list:
        if not _supports_generate_content(m):
            continue
        mid = _strip_model_id(getattr(m, "name", None))
        if not mid:
            continue
        live_slugs.add(mid)
        if capability_type == "text" and not _is_text_candidate(mid):
            continue
        if capability_type == "image" and not _is_image_candidate(mid):
            continue
        key = _text_sort_key(mid) if capability_type == "text" else _image_sort_key(mid)
        scored.append((key, mid))

    scored.sort(key=lambda kv: kv[0], reverse=True)

    # Preferred model goes first -- only if it is confirmed live.
    if preferred_slug:
        if preferred_slug in live_slugs:
            add(preferred_slug)
        else:
            logger.warning(
                "Preferred model '%s' is NOT in the live models.list(); skipping. "
                "Live models: %s",
                _full_model_id(preferred_slug),
                [_full_model_id(s) for s in sorted(live_slugs)],
            )

    # All live candidates, best first.
    for _, mid in scored:
        add(mid)

    if chain:
        return chain

    # models.list() returned nothing -- last resort.
    last_resort = _LAST_RESORT_TEXT if capability_type == "text" else _LAST_RESORT_IMAGE
    logger.warning(
        "models.list() returned no usable models (network issue?). "
        "Falling back to hardcoded list (NOT validated against live API): %s",
        last_resort,
    )
    for mid in last_resort:
        add(mid)
    return chain


# ---------------------------------------------------------------------------
# Resilient content generation: 503 backoff + 404 rotation + chain abort
# ---------------------------------------------------------------------------

def generate_content_with_model_fallback(
    client: genai.Client,
    model_chain: list[str],
    *,
    contents: Any,
    config: Any | None = None,
) -> Any:
    """
    Generate content down the chain with two resilience layers:

    * 503 / UNAVAILABLE -> exponential backoff (2^n s) up to MAX_503_RETRIES.
    * 404 / NOT_FOUND   -> log available models and advance chain immediately.

    If the chain is exhausted, re-raises the last exception.
    """
    from core.google_guardrail import (
        GoogleAPIBlockedError,
        GoogleBudgetExceededError,
        estimate_call_cost_usd,
        estimate_contents_tokens,
        extract_usage_tokens,
        get_google_cost_tracker,
        is_image_model,
        max_google_attempts,
        max_google_retries,
    )

    if not model_chain:
        raise RuntimeError("generate_content_with_model_fallback: model_chain is empty.")

    cheap = _cheap_text_first(model_chain, contents, config)
    if cheap is not None:
        return cheap

    from modules.reel_artefact.provider_health import dead_reason, provider_dead

    if provider_dead("gemini"):
        raise RuntimeError(f"gemini dead: {dead_reason('gemini') or 'billing'}")

    last: BaseException | None = None
    total = len(model_chain)
    retry_budget = min(MAX_503_RETRIES, max_google_retries())
    attempts = min(1 + MAX_503_RETRIES, max_google_attempts())
    tracker = get_google_cost_tracker()
    in_est = estimate_contents_tokens(contents)

    for i, model_id in enumerate(model_chain):
        next_id = model_chain[i + 1] if i + 1 < total else None
        kind = "image" if is_image_model(model_id) else "text"
        img_n = 1 if kind == "image" else 0
        est = estimate_call_cost_usd(
            model_id,
            input_tokens=in_est,
            output_tokens=0 if kind == "image" else 512,
            images=img_n,
            kind=kind,
        )

        if kind == "text":
            from core.google_guardrail import cheap_text_config

            model_config = cheap_text_config(config, str(model_id))
        else:
            model_config = config

        for attempt in range(attempts):
            from google.genai import errors as _g_errors  # lazy — resolve exception class on demand

            try:
                tracker.preflight(
                    est,
                    model=str(model_id),
                    source="gemini_utils.generate_content_with_model_fallback",
                    kind=kind,
                )
                try:
                    import config as _app_cfg  # noqa: PLC0415

                    _app_cfg.note_gemini_flash_call(str(model_id))
                except Exception:
                    pass
                if model_config is not None:
                    response = client.models.generate_content(
                        model=model_id, contents=contents, config=model_config
                    )
                else:
                    response = client.models.generate_content(
                        model=model_id, contents=contents
                    )
                in_tok, out_tok = extract_usage_tokens(response, fallback_input=in_est)
                tracker.record(
                    model=str(model_id),
                    kind=kind,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    images=img_n,
                    retries=attempt,
                    source="gemini_utils.generate_content_with_model_fallback",
                    status="ok",
                )
                logger.info("Gemini OK | model=%s | attempt=%d", model_id, attempt)
                return response

            except (GoogleAPIBlockedError, GoogleBudgetExceededError):
                raise
            except (_g_errors.APIError, Exception) as exc:  # noqa: BLE001
                last = exc
                from modules.reel_artefact.provider_health import (  # noqa: PLC0415
                    is_billing_or_auth,
                    mark_dead,
                    provider_dead,
                )

                if provider_dead("gemini") or is_billing_or_auth(exc):
                    if is_billing_or_auth(exc):
                        mark_dead("gemini", str(exc))
                    logger.error(
                        "Gemini billing | model=%s | no retry | %s", model_id, exc,
                    )
                    raise

                if _is_retryable_503(exc) or _is_retryable_429(exc):
                    if attempt < attempts - 1:
                        wait = 2 ** (attempt + 1)
                        logger.warning(
                            "Gemini 503/429 on '%s' (attempt %d/%d); backing off %ds.",
                            model_id, attempt + 1, retry_budget, wait,
                        )
                        time.sleep(wait)
                        continue
                    logger.warning(
                        "Gemini 503/429 on '%s' -- all %d retry attempts exhausted. Advancing chain.",
                        model_id, retry_budget,
                    )
                    break

                if _should_try_next_model(exc):
                    if next_id:
                        logger.warning(
                            "Gemini 404/NOT_FOUND on '%s'; advancing to '%s'.",
                            model_id, next_id,
                        )
                    else:
                        logger.error(
                            "Gemini 404/NOT_FOUND on '%s' -- no remaining models in chain.",
                            model_id,
                        )
                    break

                raise  # non-retryable, non-404 error

    if last:
        raise last
    raise RuntimeError("generate_content_with_model_fallback: chain exhausted with no response.")


# ---------------------------------------------------------------------------
# Near-zero cost: text-only requests go to the cheap chain before Gemini
# ---------------------------------------------------------------------------

_OFF = {"0", "false", "no", "off"}


class _CheapPart:
    def __init__(self, text: str) -> None:
        self.text = text
        self.inline_data = None


class _CheapContent:
    def __init__(self, text: str) -> None:
        self.parts = [_CheapPart(text)]
        self.role = "model"


class _CheapCandidate:
    def __init__(self, text: str) -> None:
        self.content = _CheapContent(text)
        self.finish_reason = "STOP"


class _CheapUsage:
    def __init__(self, prompt: int, output: int) -> None:
        self.prompt_token_count = prompt
        self.candidates_token_count = output
        self.thoughts_token_count = 0
        self.total_token_count = prompt + output


class CheapTextResponse:
    """Duck-types the parts of a Gemini response that callers read."""

    def __init__(self, text: str, *, provider: str, model: str, prompt: int, output: int) -> None:
        self.text = text
        self.candidates = [_CheapCandidate(text)]
        self.usage_metadata = _CheapUsage(prompt, output)
        self.model_version = f"{provider}:{model}"
        self.provider = provider
        self.parsed = None


def _text_only_blob(contents: Any) -> str | None:
    """Joined prompt text, or None when any part is an image, file or bytes."""
    if contents is None:
        return None
    if isinstance(contents, str):
        return contents
    if isinstance(contents, (bytes, bytearray)):
        return None
    if isinstance(contents, (list, tuple)):
        chunks: list[str] = []
        for item in contents:
            sub = _text_only_blob(item)
            if sub is None:
                return None
            chunks.append(sub)
        return "\n\n".join(c for c in chunks if c)
    if isinstance(contents, dict):
        if "parts" in contents:
            return _text_only_blob(contents.get("parts"))
        if set(contents) <= {"text"}:
            return str(contents.get("text") or "")
        return None
    if getattr(contents, "inline_data", None) is not None or getattr(contents, "file_data", None) is not None:
        return None
    parts = getattr(contents, "parts", None)
    if parts is not None:
        return _text_only_blob(list(parts))
    text = getattr(contents, "text", None)
    if isinstance(text, str):
        return text
    return None


def _config_value(config: Any, name: str) -> Any:
    if config is None:
        return None
    if isinstance(config, dict):
        return config.get(name)
    return getattr(config, name, None)


def _system_text(config: Any) -> str | None:
    raw = _config_value(config, "system_instruction")
    if raw is None:
        return None
    blob = _text_only_blob(raw)
    return blob or None


def _cheap_text_first(model_chain: list[str], contents: Any, config: Any) -> Any | None:
    """Answer a text-only request from the cheap chain. None means use Gemini."""
    mode = (os.getenv("GEMINI_TEXT_CHEAP_FIRST") or "1").strip().lower()
    if mode in _OFF:
        return None
    if os.getenv("PYTEST_CURRENT_TEST") and mode != "force":
        return None
    from core.google_guardrail import is_image_model

    # Only the head model says what the caller wants; the live fallback tail
    # can hold an image model and must not turn a text call back to Gemini.
    if is_image_model(str(model_chain[0])):
        return None
    if _config_value(config, "response_modalities"):
        return None
    if _config_value(config, "tools"):
        return None
    blob = _text_only_blob(contents)
    if not blob or not blob.strip():
        return None
    from core import cheap_llm

    if not cheap_llm.available("text"):
        return None
    json_mode = str(_config_value(config, "response_mime_type") or "").lower() == "application/json"
    schema = _config_value(config, "response_json_schema") or _config_value(config, "response_schema")
    prompt = blob
    if json_mode:
        hint = "Reply with one JSON value only, no prose and no code fence."
        if isinstance(schema, dict):
            try:
                import json as _json

                hint += " It must match this JSON schema: " + _json.dumps(schema)[:4000]
            except Exception:  # noqa: BLE001
                pass
        prompt = f"{blob}\n\n{hint}"
    max_tokens = _config_value(config, "max_output_tokens") or 4096
    temperature = _config_value(config, "temperature")
    try:
        result = cheap_llm.complete(
            prompt,
            system=_system_text(config),
            json_mode=json_mode,
            max_tokens=int(max_tokens),
            temperature=float(temperature) if temperature is not None else 0.4,
            source="gemini_utils.cheap_text_first",
        )
    except cheap_llm.CheapLLMUnavailable as exc:
        logger.warning("cheap text chain unavailable, using Gemini | %s", exc)
        return None
    logger.info(
        "Gemini text request served by %s:%s (wanted %s)",
        result.provider, result.model, model_chain[0],
    )
    return CheapTextResponse(
        result.text,
        provider=result.provider,
        model=result.model,
        prompt=result.input_tokens,
        output=result.output_tokens,
    )


def _log_fallback_chain_step(failed_model: str, exc: BaseException, next_model: str | None) -> None:
    if next_model:
        logger.warning("GEMINI_ALERT: '%s' failed (%s); trying '%s'.", failed_model, exc, next_model)
    else:
        logger.warning("GEMINI_ALERT: '%s' failed (%s); no remaining models.", failed_model, exc)


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def get_best_available_model(
    client: genai.Client,
    *,
    capability_type: Capability = "text",
) -> str | None:
    """Pick the single best model ID (fully qualified) for this key."""
    models_list = _list_models(client)
    scored: list[tuple[tuple[float, float, str], str]] = []
    for m in models_list:
        if not _supports_generate_content(m):
            continue
        mid = _strip_model_id(getattr(m, "name", None))
        if not mid:
            continue
        if capability_type == "text" and not _is_text_candidate(mid):
            continue
        if capability_type == "image" and not _is_image_candidate(mid):
            continue
        key = _text_sort_key(mid) if capability_type == "text" else _image_sort_key(mid)
        scored.append((key, mid))
    if not scored:
        return None
    scored.sort(key=lambda kv: kv[0], reverse=True)
    return _full_model_id(scored[0][1])


def get_latest_model(client: genai.Client, *, kind: Capability = "text") -> str:
    try:
        found = get_best_available_model(client, capability_type=kind)
        if found:
            return found
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini model discovery raised %s; using safe default.", exc)
    import config as app_cfg
    fb = app_cfg.SAFE_GEMINI_IMAGE_MODEL if kind == "image" else app_cfg.SAFE_GEMINI_TEXT_MODEL
    logger.warning("GEMINI_ALERT: discovery failed (kind=%s); using '%s'.", kind, fb)
    return fb


def generate_text_with_client_chain(
    *,
    api_key: str,
    preferred_model: str | None,
    contents: Any,
) -> Any:
    client = make_gemini_client_with_fallback(api_key)
    chain = build_model_chain(client, capability_type="text", preferred=preferred_model)
    return generate_content_with_model_fallback(client, chain, contents=contents)


def normalize_model_id(model_id: str | None) -> str:
    return _strip_model_id(model_id)


def chain_with_preferred_first(base_chain: list[str], preferred: str | None) -> list[str]:
    """Prepend preferred model (full models/<slug> form) without duplicating."""
    pref_slug = _strip_model_id(preferred)
    if not pref_slug:
        return list(base_chain)
    pref_full = _full_model_id(pref_slug)
    rest = [x for x in base_chain if _strip_model_id(x) != pref_slug]
    return [pref_full] + rest
