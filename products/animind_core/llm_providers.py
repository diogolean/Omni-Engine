"""Pluggable script writers with cached, provider-reported cost.

DeepSeek is the default OpenAI-compatible writer. Gemini Flash-Lite remains a
fallback. Secrets are read only at call time and are never included in cache
keys, logs, or public metadata.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

_LOG = logging.getLogger("animind_core")
DEEPSEEK_MODEL = "deepseek-flash"
GEMINI_MODEL = "gemini-3.5-flash-lite"


@dataclass(frozen=True)
class TextResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cached: bool = False


@dataclass(frozen=True)
class LLMResult:
    lines: list[dict[str, Any]]
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cached: bool = False

    def cost(self, status: str = "ok") -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "costUsd": round(self.cost_usd, 8),
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "cached": self.cached,
            "status": status,
        }


class LLMProvider(ABC):
    id: str
    model: str

    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def complete(self, prompt: str) -> LLMResult:
        raise NotImplementedError

    def complete_text(self, prompt: str) -> TextResult:
        result = self.complete(prompt)
        return TextResult(
            json.dumps({"lines": result.lines}, ensure_ascii=False),
            result.provider, result.model, result.input_tokens, result.output_tokens, result.cost_usd,
        )


def _json_lines(text: str) -> list[dict[str, Any]]:
    match = __import__("re").search(r"\{.*\}", text or "", __import__("re").S)
    if not match:
        raise ValueError("writer returned no JSON object")
    data = json.loads(match.group(0))
    return list(data.get("lines") or [])


class DeepSeekProvider(LLMProvider):
    id = "deepseek"

    def __init__(self) -> None:
        self.model = (os.getenv("DEEPSEEK_MODEL") or DEEPSEEK_MODEL).strip()

    def available(self) -> bool:
        return bool((os.getenv("DEEPSEEK_API_KEY") or "").strip())

    def complete_text(self, prompt: str) -> TextResult:
        key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        base = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        response = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "thinking": {"type": "disabled"},
                "temperature": 0.8,
                "stream": False,
            },
            timeout=90.0,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        cache_hit = int(usage.get("prompt_cache_hit_tokens") or 0)
        cache_miss = max(0, input_tokens - cache_hit)
        # Conservative peak rates, verified in the official price table on
        # 2026-11-02. Off-peak charges are one half of these values.
        cost = cache_miss * 0.30e-6 + cache_hit * 0.006e-6 + output_tokens * 1.20e-6
        return TextResult(text, self.id, self.model, input_tokens, output_tokens, cost)

    def complete(self, prompt: str) -> LLMResult:
        raw = self.complete_text(prompt)
        return LLMResult(
            _json_lines(raw.text), self.id, self.model, raw.input_tokens, raw.output_tokens, raw.cost_usd,
        )


class GeminiProvider(LLMProvider):
    id = "google"
    model = GEMINI_MODEL

    def __init__(
        self,
        generate: Callable[[str], list[dict[str, Any]]],
        *,
        allow_unavailable: bool = False,
        text_generate: Callable[[str], str] | None = None,
    ) -> None:
        self._generate = generate
        self._text_generate = text_generate
        self._allow_unavailable = allow_unavailable

    def available(self) -> bool:
        from . import settings  # local import avoids a settings/provider cycle

        return self._allow_unavailable or settings.gemini_available()

    def _usage(self, before: dict[str, Any], after: dict[str, Any]) -> tuple[int, int, float]:
        entries = after["entries"][len(before["entries"]):]
        return (
            sum(int(e.get("input_tokens", 0)) for e in entries),
            sum(int(e.get("output_tokens", 0)) for e in entries),
            sum(float(e.get("cost_usd", 0.0)) for e in entries),
        )

    def complete_text(self, prompt: str) -> TextResult:
        from production.cost.google_guardrail import get_google_cost_tracker

        tracker = get_google_cost_tracker()
        before = tracker.snapshot()
        if self._text_generate is not None:
            text = self._text_generate(prompt)
        else:
            text = json.dumps({"lines": self._generate(prompt)}, ensure_ascii=False)
        input_tokens, output_tokens, cost = self._usage(before, tracker.snapshot())
        return TextResult(text, self.id, self.model, input_tokens, output_tokens, cost)

    def complete(self, prompt: str) -> LLMResult:
        from production.cost.google_guardrail import get_google_cost_tracker

        tracker = get_google_cost_tracker()
        before = tracker.snapshot()
        lines = self._generate(prompt)
        input_tokens, output_tokens, cost = self._usage(before, tracker.snapshot())
        return LLMResult(lines, self.id, self.model, input_tokens, output_tokens, cost)


class OpenRouterProvider(LLMProvider):
    """Optional cheap text model. Used only when ``OPENROUTER_API_KEY`` is set."""

    id = "openrouter"

    def __init__(self) -> None:
        self.model = (os.getenv("OPENROUTER_TEXT_MODEL") or "deepseek/deepseek-v4.1-flash").strip()

    def available(self) -> bool:
        return bool((os.getenv("OPENROUTER_API_KEY") or "").strip())

    def complete_text(self, prompt: str) -> TextResult:
        from production.providers.llm import cheap_llm  # noqa: PLC0415

        key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
                "reasoning": {"enabled": False, "exclude": True},
                "usage": {"include": True},
            },
            timeout=90.0,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["choices"][0]["message"]["content"] or ""
        usage = payload.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        if usage.get("cost") is not None:
            cost = float(usage["cost"])
        else:
            cost = cheap_llm.estimate_usd(self.id, self.model, input_tokens, output_tokens)
        return TextResult(text, self.id, self.model, input_tokens, output_tokens, cost)

    def complete(self, prompt: str) -> LLMResult:
        raw = self.complete_text(prompt)
        return LLMResult(
            _json_lines(raw.text), self.id, self.model, raw.input_tokens, raw.output_tokens, raw.cost_usd,
        )


def provider_order(
    gemini_generate: Callable[[str], list[dict[str, Any]]],
    *,
    allow_unavailable_gemini: bool = False,
    gemini_text: Callable[[str], str] | None = None,
) -> list[LLMProvider]:
    """DeepSeek, then Gemini Flash-Lite, then OpenRouter when that key is set."""
    gemini = GeminiProvider(
        gemini_generate, allow_unavailable=allow_unavailable_gemini, text_generate=gemini_text,
    )
    providers: dict[str, LLMProvider] = {
        "deepseek": DeepSeekProvider(),
        "gemini": gemini,
        "google": gemini,
        "openrouter": OpenRouterProvider(),
    }
    first = (os.getenv("ANIMIND_SCRIPT_PROVIDER") or "deepseek").strip().lower()
    first = first if first in providers else "deepseek"
    order = [providers[first]]
    for name in ("deepseek", "gemini", "openrouter"):
        if name == first or name == "google":
            continue
        item = providers[name]
        if name == "openrouter" and not item.available():
            continue
        order.append(item)
    return order


def token_cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """List price from token counts. DeepSeek peak rates, verified 2026-11-02."""
    provider = (provider or "").lower()
    if provider == "deepseek":
        return max(0, input_tokens) * 0.30e-6 + max(0, output_tokens) * 1.20e-6
    if provider == "openrouter":
        from production.providers.llm import cheap_llm  # noqa: PLC0415

        return cheap_llm.estimate_usd(provider, model, input_tokens, output_tokens)
    return 0.0


def _cache_path(root: Path, provider: LLMProvider, prompt: str) -> Path:
    digest = hashlib.sha256(f"{provider.id}\n{provider.model}\n{prompt}".encode()).hexdigest()[:24]
    return root / f"{digest}.json"


def generate(
    prompt: str,
    cache_dir: Path,
    gemini_generate: Callable[[str], list[dict[str, Any]]],
    *,
    allow_unavailable_gemini: bool = False,
) -> tuple[LLMResult | None, list[str]]:
    """Try configured providers in order, returning failures for diagnostics."""
    failures: list[str] = []
    for provider in provider_order(
        gemini_generate, allow_unavailable_gemini=allow_unavailable_gemini,
    ):
        if not provider.available():
            continue
        from . import settings  # noqa: PLC0415

        cache_enabled = not (
            provider.id == "google"
            and allow_unavailable_gemini
            and not settings.gemini_available()
        )
        cached = _cache_path(cache_dir, provider, prompt)
        if cache_enabled and cached.is_file():
            raw = json.loads(cached.read_text(encoding="utf-8"))
            priced = token_cost_usd(
                str(raw.get("provider") or provider.id),
                str(raw.get("model") or provider.model),
                int(raw.get("input_tokens") or 0),
                int(raw.get("output_tokens") or 0),
            )
            return LLMResult(**(raw | {"cached": True, "cost_usd": priced or float(raw.get("cost_usd") or 0.0)})), failures
        try:
            result = provider.complete(prompt)
        except Exception as exc:  # noqa: BLE001 - fallback is the contract
            failures.append(f"{provider.id}: {exc}")
            _LOG.warning("%s script provider failed; trying fallback: %s", provider.id, exc)
            continue
        if cache_enabled:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(asdict(result), ensure_ascii=False), encoding="utf-8")
        return result, failures
    return None, failures


def _json_object(text: str) -> dict[str, Any]:
    match = __import__("re").search(r"\{.*\}", text or "", __import__("re").S)
    if not match:
        raise ValueError("writer returned no JSON object")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("writer JSON was not an object")
    return data


def generate_text(
    prompt: str,
    cache_dir: Path,
    gemini_text: Callable[[str], str],
    *,
    allow_unavailable_gemini: bool = False,
    only: str | None = None,
) -> tuple[TextResult | None, list[str]]:
    """Same provider order as :func:`generate`, returning the raw JSON text."""
    failures: list[str] = []
    order = provider_order(
        lambda _prompt: [],
        allow_unavailable_gemini=allow_unavailable_gemini,
        gemini_text=gemini_text,
    )
    if only:
        wanted = "google" if only in {"gemini", "google"} else only
        order = [item for item in order if item.id == wanted]
    for item in order:
        if not item.available():
            continue
        from . import settings  # noqa: PLC0415

        cache_enabled = not (
            item.id == "google"
            and allow_unavailable_gemini
            and not settings.gemini_available()
        )
        cached = _cache_path(cache_dir, item, prompt)
        if cache_enabled and cached.is_file():
            raw = json.loads(cached.read_text(encoding="utf-8"))
            priced = token_cost_usd(
                str(raw.get("provider") or item.id),
                str(raw.get("model") or item.model),
                int(raw.get("input_tokens") or 0),
                int(raw.get("output_tokens") or 0),
            )
            return TextResult(**(raw | {"cached": True, "cost_usd": priced or float(raw.get("cost_usd") or 0.0)})), failures
        try:
            result = item.complete_text(prompt)
        except Exception as exc:  # noqa: BLE001 - fallback is the contract
            failures.append(f"{item.id}: {exc}")
            _LOG.warning("%s script provider failed; trying fallback: %s", item.id, exc)
            continue
        if cache_enabled:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(asdict(result), ensure_ascii=False), encoding="utf-8")
        return result, failures
    return None, failures
