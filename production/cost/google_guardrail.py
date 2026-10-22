# -*- coding: utf-8 -*-
"""
Emergency Google / Gemini billing circuit-breaker.

Wraps every billed Google API call with:

1. ``ALLOW_GOOGLE_API`` kill-switch (false → hard abort, no request is sent).
2. Per-process cost tracker with a hard USD cap (``MAX_GOOGLE_COST_PER_RUN_USD``).
3. Strict retry budget (``MAX_GOOGLE_RETRIES``, default 2) — no unbounded backoff.

Pricing used for estimates (USD per 1M tokens, Gemini paid tier, Oct 2026).
Thinking tokens are billed as output and are now counted:

* ``gemini-2.5-flash``: $0.30 in, $2.50 out (the old $0.075/$0.30 was the
  1.5-era price and under-reported spend about eight times)
* ``gemini-2.5-flash-lite`` / ``flash-lite-latest``: $0.10 in, $0.40 out
* ``gemini-2.5-pro``: $1.25 in, $10 out
* Nano Banana images: ``modules.reel_artefact.pricing``

Every text call gets ``thinking_budget=0`` on the 2.5 Flash family and a
``max_output_tokens`` cap (``GEMINI_MAX_OUTPUT_TOKENS``, default 4096)
unless the caller set them.

Environment
-----------
ALLOW_GOOGLE_API=true|false          default true if unset (``.env`` may force false)
MAX_GOOGLE_COST_PER_RUN_USD=0.05     hard abort once this run's estimate is exceeded.
                                     Values above $0.05 are clamped unless
                                     GOOGLE_COST_ALLOW_ABOVE_CEILING=1.
MAX_GOOGLE_RETRIES=2                 additional attempts after the first failure
"""
from __future__ import annotations

import inspect
import logging
import os
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from shared.env import env_float

logger = logging.getLogger(__name__)

# A reel binds its page once for the whole process. A .env ACTIVE_PAGE of
# anna_protocol must not relabel ancient_knowledge calls, including work
# that runs on another thread after the planner clears its own override.
_COST_CHANNEL = threading.local()
_RUN_CHANNEL = ""
_RUN_LOCK = threading.Lock()


def bind_run_channel(channel: str | None) -> None:
    """Process-wide page for every Google cost line."""
    global _RUN_CHANNEL
    name = (channel or "").strip()
    with _RUN_LOCK:
        _RUN_CHANNEL = name


def bind_cost_channel(channel: str | None) -> None:
    """Thread-local override. None clears it and leaves the run channel."""
    name = (channel or "").strip()
    _COST_CHANNEL.name = name or None

# Gemini 2.5 Flash list prices (USD / 1M tokens). Thinking bills as output.
FLASH_INPUT_USD_PER_1M: float = 0.30
FLASH_OUTPUT_USD_PER_1M: float = 2.50

# (input, output) USD per 1M tokens, matched by substring, first hit wins.
TEXT_PRICES_PER_1M: tuple[tuple[str, float, float], ...] = (
    ("2.5-pro", 1.25, 10.0),
    ("3-pro", 2.0, 12.0),
    ("flash-lite", 0.10, 0.40),
    ("3.1-flash-lite", 0.25, 1.50),
    ("3.5-flash-lite", 0.30, 2.50),
    ("3.6-flash", 0.75, 3.75),
    ("3.7-flash", 0.75, 3.75),
    ("3.8-flash", 0.75, 3.75),
    ("3-flash", 0.50, 3.00),
    ("robotics", 1.0, 5.0),
    ("2.0-flash", 0.10, 0.40),
    ("flash", FLASH_INPUT_USD_PER_1M, FLASH_OUTPUT_USD_PER_1M),
)
GOOGLE_COST_CEILING_USD: float = 0.05
DEFAULT_MAX_OUTPUT_TOKENS: int = 4096

# Official Gemini / Imagen image list prices (USD / image) — 1K is the factory default
IMAGE_FLASH_1K_USD: float = 0.039
IMAGE_1K_USD: float = 0.03          # Pro / Imagen at 1K
IMAGE_2K_USD: float = 0.134         # Pro 2K (opt-in only; never the default)

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


class GoogleAPIBlockedError(RuntimeError):
    """Raised when ``ALLOW_GOOGLE_API`` is false — no request is sent."""


class GoogleBudgetExceededError(RuntimeError):
    """Raised when this process would exceed ``MAX_GOOGLE_COST_PER_RUN_USD``."""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in _TRUE:
        return True
    if val in _FALSE:
        return False
    return default


_env_float = env_float


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def is_google_api_allowed() -> bool:
    """Live read of ``ALLOW_GOOGLE_API``. Unset → True (tests / CI stay unblocked)."""
    return _env_bool("ALLOW_GOOGLE_API", True)


def max_google_cost_usd() -> float:
    """Per-process Gemini cap. A .env value above $0.05 is clamped to it."""
    cap = max(0.0, _env_float("MAX_GOOGLE_COST_PER_RUN_USD", GOOGLE_COST_CEILING_USD))
    if _env_bool("GOOGLE_COST_ALLOW_ABOVE_CEILING", False):
        return cap
    return min(cap, GOOGLE_COST_CEILING_USD)


def _text_rates(model_id: str | None) -> tuple[float, float]:
    low = (model_id or "").lower()
    for needle, pin, pout in TEXT_PRICES_PER_1M:
        if needle in low:
            return pin, pout
    return FLASH_INPUT_USD_PER_1M, FLASH_OUTPUT_USD_PER_1M


def _wants_zero_thinking(model_id: str | None) -> bool:
    """2.5 Flash and Flash-Lite accept a budget of 0. Pro and 3.x do not."""
    low = (model_id or "").lower()
    if "image" in low or "pro" in low:
        return False
    return "2.5-flash" in low or "flash-lite" in low


def cheap_text_config(config: Any, model_id: str | None) -> Any:
    """Add ``thinking_budget=0`` and an output cap unless the caller set them."""
    if is_image_model(model_id):
        return config
    cap = max(64, _env_int("GEMINI_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS))
    budget = _env_int("GEMINI_THINKING_BUDGET", 0)
    zero = _wants_zero_thinking(model_id)
    if isinstance(config, dict):
        out = dict(config)
        out.setdefault("max_output_tokens", cap)
        if zero and out.get("thinking_config") is None:
            out["thinking_config"] = {"thinking_budget": budget}
        return out
    try:
        from google.genai import types
    except Exception:  # noqa: BLE001
        return config
    thinking_cls = getattr(types, "ThinkingConfig", None)
    if config is None:
        kwargs: dict[str, Any] = {"max_output_tokens": cap}
        if zero and thinking_cls is not None:
            kwargs["thinking_config"] = thinking_cls(thinking_budget=budget)
        try:
            return types.GenerateContentConfig(**kwargs)
        except Exception:  # noqa: BLE001
            return None
    update: dict[str, Any] = {}
    if getattr(config, "max_output_tokens", None) is None:
        update["max_output_tokens"] = cap
    if zero and thinking_cls is not None and getattr(config, "thinking_config", None) is None:
        update["thinking_config"] = thinking_cls(thinking_budget=budget)
    if not update:
        return config
    copier = getattr(config, "model_copy", None)
    if callable(copier):
        try:
            return copier(update=update)
        except Exception:  # noqa: BLE001
            return config
    return config


def max_google_retries() -> int:
    """Additional attempts after the first failure (default 2 → 3 total tries)."""
    return max(0, _env_int("MAX_GOOGLE_RETRIES", 2))


def max_google_attempts() -> int:
    """Total tries = 1 initial + ``MAX_GOOGLE_RETRIES``."""
    return 1 + max_google_retries()


def active_channel() -> str:
    override = getattr(_COST_CHANNEL, "name", None)
    if override:
        return str(override)
    with _RUN_LOCK:
        run = _RUN_CHANNEL
    if run:
        return run
    env = (os.getenv("ACTIVE_PAGE") or "").strip()
    if env:
        return env
    try:
        import config as app_config

        return str(getattr(app_config, "ACTIVE_PAGE", "") or "unknown")
    except Exception:
        return "unknown"


def assert_google_allowed(*, context: str = "") -> None:
    """Hard kill-switch. Call before any Google client construction or request."""
    if is_google_api_allowed():
        return
    where = context or _caller_name()
    channel = active_channel()
    msg = (
        f"GOOGLE API KILL-SWITCH | ALLOW_GOOGLE_API=false | "
        f"channel={channel} | src={where} | no request sent"
    )
    logger.error(msg)
    raise GoogleAPIBlockedError(msg)


def _caller_name(depth: int = 2) -> str:
    try:
        frame = inspect.stack()[depth]
        mod = frame.filename.replace("\\", "/").rsplit("/", 1)[-1]
        return f"{mod}:{frame.function}"
    except Exception:
        return "unknown"


def classify_model(model_id: str | None) -> str:
    low = (model_id or "").lower()
    if "imagen" in low:
        return "imagen"
    if "nano-banana" in low or "nanobanana" in low:
        return "gemini-flash-image"
    if "image" in low and "pro" in low:
        return "gemini-pro-image"
    if "image" in low:
        return "gemini-flash-image"
    if "pro" in low and "flash" not in low:
        return "gemini-pro-text"
    return "gemini-flash-text"


def is_image_model(model_id: str | None) -> bool:
    kind = classify_model(model_id)
    return kind in {"imagen", "gemini-pro-image", "gemini-flash-image"}


def _configured_image_size() -> str:
    raw = (os.getenv("GEMINI_IMAGE_SIZE") or "").strip().upper()
    if raw in {"1K", "2K"}:
        return raw
    try:
        import config as app_config

        val = str(getattr(app_config, "GEMINI_IMAGE_SIZE", "1K") or "1K").strip().upper()
        return val if val in {"1K", "2K"} else "1K"
    except Exception:
        return "1K"


def image_unit_cost_usd(model_id: str | None) -> float:
    kind = classify_model(model_id)
    size = _configured_image_size()
    if kind == "gemini-pro-image":
        # Nano Banana Pro bills $0.134 at 1K and 2K alike.
        return IMAGE_2K_USD
    if kind == "imagen":
        return IMAGE_2K_USD if size == "2K" else IMAGE_1K_USD
    if kind == "gemini-flash-image":
        return _flash_image_usd(model_id)
    return 0.0


def _flash_image_usd(model_id: str | None) -> float:
    """Nano Banana list price. Unknown flash-image ids use the 1K default."""
    try:
        from production.formats.economic_reel.artefact.pricing import NANOBANANA_USD
    except Exception:
        return IMAGE_FLASH_1K_USD
    key = (model_id or "").strip()
    bare = key.split("/")[-1]
    if key in NANOBANANA_USD:
        return float(NANOBANANA_USD[key])
    if bare in NANOBANANA_USD:
        return float(NANOBANANA_USD[bare])
    return IMAGE_FLASH_1K_USD


def text_cost_usd(
    input_tokens: int,
    output_tokens: int,
    *,
    model_id: str | None = None,
) -> float:
    pin, pout = _text_rates(model_id)
    inp = max(0, int(input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    return (inp * pin + out * pout) / 1_000_000.0


def estimate_call_cost_usd(
    model_id: str | None,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    images: int = 0,
    kind: str | None = None,
) -> float:
    resolved = (kind or "").strip().lower()
    if not resolved:
        resolved = "image" if is_image_model(model_id) else "text"
    if resolved in {"image", "vision-image"}:
        n = max(1, int(images or 1))
        return image_unit_cost_usd(model_id) * n
    return text_cost_usd(input_tokens, output_tokens, model_id=model_id)


def estimate_contents_tokens(contents: Any) -> int:
    """Rough input-token estimate (chars / 4) for preflight reserves."""
    return max(1, _contents_char_count(contents) // 4)


def _contents_char_count(contents: Any) -> int:
    if contents is None:
        return 0
    if isinstance(contents, str):
        return len(contents)
    if isinstance(contents, (bytes, bytearray)):
        return len(contents)
    if isinstance(contents, dict):
        return sum(_contents_char_count(v) for v in contents.values())
    if isinstance(contents, (list, tuple)):
        return sum(_contents_char_count(item) for item in contents)
    text = getattr(contents, "text", None)
    if isinstance(text, str):
        return len(text)
    if callable(text):
        try:
            val = text()
            if isinstance(val, str):
                return len(val)
        except Exception:
            pass
    try:
        return len(str(contents))
    except Exception:
        return 0


def extract_usage_tokens(
    response: Any,
    *,
    fallback_input: int = 0,
    fallback_output: int = 0,
) -> tuple[int, int]:
    """Read ``usage_metadata``; fall back to char/4 estimates."""
    prompt_tok = 0
    completion_tok = 0
    um = getattr(response, "usage_metadata", None)
    if um is None and isinstance(response, dict):
        um = response.get("usage_metadata")
    thoughts = 0
    if isinstance(um, dict):
        prompt_tok = int(float(um.get("prompt_token_count") or 0))
        completion_tok = int(float(um.get("candidates_token_count") or 0))
        if completion_tok <= 0:
            completion_tok = int(float(um.get("output_token_count") or 0))
        thoughts = int(float(um.get("thoughts_token_count") or 0))
    elif um is not None:
        prompt_tok = int(float(getattr(um, "prompt_token_count", 0) or 0))
        completion_tok = int(float(getattr(um, "candidates_token_count", 0) or 0))
        if completion_tok <= 0:
            completion_tok = int(float(getattr(um, "output_token_count", 0) or 0))
        try:
            thoughts = int(float(getattr(um, "thoughts_token_count", 0) or 0))
        except (TypeError, ValueError):
            thoughts = 0
    if prompt_tok <= 0:
        prompt_tok = max(0, int(fallback_input or 0))
    if completion_tok <= 0:
        text_attr = getattr(response, "text", None) if response is not None else None
        raw = ""
        if callable(text_attr):
            try:
                raw = str(text_attr() or "")
            except Exception:
                raw = ""
        elif text_attr:
            raw = str(text_attr)
        completion_tok = max(0, int(fallback_output or 0))
        if completion_tok <= 0 and raw:
            completion_tok = max(1, len(raw) // 4)
    # Thinking tokens are billed at the output rate.
    return prompt_tok, completion_tok + max(0, thoughts)


@dataclass
class GoogleCostRecord:
    ts: str
    channel: str
    source: str
    model: str
    kind: str
    input_tokens: int
    output_tokens: int
    images: int
    retries: int
    cost_usd: float
    status: str


@dataclass
class GoogleCostTracker:
    """Process-wide singleton ledger. Thread-safe."""

    _total_usd: float = 0.0
    _calls: int = 0
    _retries: int = 0
    _images: int = 0
    _input_tokens: int = 0
    _output_tokens: int = 0
    _entries: list[GoogleCostRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def total_usd(self) -> float:
        with self._lock:
            return self._total_usd

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "channel": active_channel(),
                "total_usd": round(self._total_usd, 6),
                "cap_usd": max_google_cost_usd(),
                "calls": self._calls,
                "retries": self._retries,
                "images": self._images,
                "input_tokens": self._input_tokens,
                "output_tokens": self._output_tokens,
                "entries": [e.__dict__ for e in self._entries],
            }

    def reset(self) -> None:
        with self._lock:
            self._total_usd = 0.0
            self._calls = 0
            self._retries = 0
            self._images = 0
            self._input_tokens = 0
            self._output_tokens = 0
            self._entries.clear()

    def preflight(
        self,
        estimated_usd: float,
        *,
        model: str = "",
        source: str = "",
        kind: str = "text",
    ) -> None:
        """Abort *before* the HTTP request if this call would breach the cap."""
        assert_google_allowed(context=source or "google_guardrail.preflight")
        cap = max_google_cost_usd()
        extra = max(0.0, float(estimated_usd or 0.0))
        with self._lock:
            projected = self._total_usd + extra
            current = self._total_usd
        if projected > cap + 1e-12:
            self._raise_budget(current, extra, cap, model=model, source=source, kind=kind)
        try:
            from production.cost.spend_ledger import SpendCapExceeded
            from production.cost.spend_ledger import preflight as _reel_preflight
        except Exception:  # noqa: BLE001
            return
        try:
            _reel_preflight(extra, provider="gemini", model=model, source=source)
        except SpendCapExceeded as exc:
            raise GoogleBudgetExceededError(str(exc)) from exc

    def record(
        self,
        *,
        model: str,
        kind: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        images: int = 0,
        retries: int = 0,
        source: str = "",
        status: str = "ok",
        cost_usd: float | None = None,
    ) -> float:
        """Charge the ledger *after* a request (or a billed failed attempt)."""
        cost = (
            float(cost_usd)
            if cost_usd is not None
            else estimate_call_cost_usd(
                model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                images=images,
                kind=kind,
            )
        )
        cost = max(0.0, cost)
        cap = max_google_cost_usd()
        rec = GoogleCostRecord(
            ts=datetime.now(timezone.utc).isoformat(),
            channel=active_channel(),
            source=source or _caller_name(),
            model=str(model or "unknown"),
            kind=str(kind or "text"),
            input_tokens=max(0, int(input_tokens or 0)),
            output_tokens=max(0, int(output_tokens or 0)),
            images=max(0, int(images or 0)),
            retries=max(0, int(retries or 0)),
            cost_usd=round(cost, 8),
            status=status,
        )
        with self._lock:
            self._entries.append(rec)
            self._total_usd += cost
            self._calls += 1
            self._retries += rec.retries
            self._images += rec.images
            self._input_tokens += rec.input_tokens
            self._output_tokens += rec.output_tokens
            total = self._total_usd
        try:
            from production.cost.spend_ledger import record as _ledger_record

            _ledger_record(
                provider="gemini", model=rec.model, cost_usd=rec.cost_usd,
                source=rec.source, kind=rec.kind, input_tokens=rec.input_tokens,
                output_tokens=rec.output_tokens, images=rec.images, status=rec.status,
            )
        except Exception:  # noqa: BLE001
            pass
        logger.info(
            "GOOGLE_API | channel=%s | src=%s | model=%s | kind=%s | "
            "calls=1 | retries=%d | in=%d | out=%d | images=%d | "
            "cost=$%.6f | run_total=$%.6f | cap=$%.2f | status=%s",
            rec.channel,
            rec.source,
            rec.model,
            rec.kind,
            rec.retries,
            rec.input_tokens,
            rec.output_tokens,
            rec.images,
            rec.cost_usd,
            total,
            cap,
            rec.status,
        )
        if total > cap + 1e-12:
            self._raise_budget(total, 0.0, cap, model=model, source=source, kind=kind)
        return cost

    def _raise_budget(
        self,
        current: float,
        extra: float,
        cap: float,
        *,
        model: str,
        source: str,
        kind: str,
    ) -> None:
        stack = "".join(traceback.format_stack(limit=12))
        msg = (
            f"GOOGLE BUDGET HARD CAP | channel={active_channel()} | "
            f"src={source or _caller_name()} | model={model} | kind={kind} | "
            f"spent=${current:.6f} | next=${extra:.6f} | cap=${cap:.2f} USD | "
            f"ABORTING to prevent further Gemini billing"
        )
        logger.error(msg)
        logger.error("GOOGLE BUDGET STACK:\n%s", stack)
        raise GoogleBudgetExceededError(msg)


_TRACKER = GoogleCostTracker()
_TRACKER_LOCK = threading.Lock()


def get_google_cost_tracker() -> GoogleCostTracker:
    return _TRACKER


def reset_google_cost_tracker() -> None:
    """Test / new-process helper."""
    _TRACKER.reset()


def make_guarded_gemini_client(api_key: str, **kwargs: Any) -> Any:
    """``genai.Client`` factory that honours the kill-switch."""
    assert_google_allowed(context="make_guarded_gemini_client")
    from google import genai

    return genai.Client(api_key=api_key, **kwargs)


def guarded_generate_content(
    client: Any,
    model: str | None = None,
    contents: Any = None,
    config: Any = None,
    *,
    kind: str | None = None,
    source: str = "",
    **kwargs: Any,
) -> Any:
    """
    Single billed ``generate_content`` with kill-switch, preflight cap, and
    a hard retry ceiling. Prefer this over ``client.models.generate_content``.
    """
    src = source or _caller_name()
    model_id = str(model or kwargs.pop("model", "") or "unknown")
    body = contents if contents is not None else kwargs.pop("contents", None)
    cfg = config if config is not None else kwargs.pop("config", None)
    resolved_kind = kind or ("image" if is_image_model(model_id) else "text")
    if resolved_kind == "text":
        cfg = cheap_text_config(cfg, model_id)
    tracker = get_google_cost_tracker()
    in_est = estimate_contents_tokens(body)
    out_reserve = 0 if resolved_kind == "image" else 512
    img_n = 1 if resolved_kind == "image" else 0
    est = estimate_call_cost_usd(
        model_id,
        input_tokens=in_est,
        output_tokens=out_reserve,
        images=img_n,
        kind=resolved_kind,
    )
    last_exc: BaseException | None = None
    attempts = max_google_attempts()
    for attempt in range(attempts):
        tracker.preflight(est, model=model_id, source=src, kind=resolved_kind)
        try:
            call_kwargs: dict[str, Any] = {"model": model_id, "contents": body}
            if cfg is not None:
                call_kwargs["config"] = cfg
            call_kwargs.update(kwargs)
            response = client.models.generate_content(**call_kwargs)
            in_tok, out_tok = extract_usage_tokens(response, fallback_input=in_est)
            tracker.record(
                model=model_id,
                kind=resolved_kind,
                input_tokens=in_tok,
                output_tokens=out_tok,
                images=img_n,
                retries=attempt,
                source=src,
                status="ok",
            )
            return response
        except (GoogleAPIBlockedError, GoogleBudgetExceededError):
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            from production.formats.economic_reel.artefact.provider_health import (  # noqa: PLC0415
                is_billing_or_auth,
                mark_dead,
            )

            if is_billing_or_auth(exc):
                mark_dead("gemini", str(exc))
                logger.error(
                    "GOOGLE_API billing | src=%s | model=%s | no retry | %s",
                    src, model_id, exc,
                )
                raise
            if attempt + 1 >= attempts:
                logger.error(
                    "GOOGLE_API FAIL | src=%s | model=%s | retries_exhausted=%d | %s",
                    src, model_id, attempt, exc,
                )
                raise
            logger.warning(
                "GOOGLE_API retry %d/%d | src=%s | model=%s | %s",
                attempt + 1, max_google_retries(), src, model_id, exc,
            )
    if last_exc:
        raise last_exc
    raise RuntimeError("guarded_generate_content: no response and no exception")
