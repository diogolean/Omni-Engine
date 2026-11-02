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
GEMINI_MODEL = "gemini-2.5-flash-lite"


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

    def complete(self, prompt: str) -> LLMResult:
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
        return LLMResult(
            _json_lines(text), self.id, self.model, input_tokens, output_tokens, cost,
        )


class GeminiProvider(LLMProvider):
    id = "google"
    model = GEMINI_MODEL

    def __init__(self, generate: Callable[[str], list[dict[str, Any]]], *, allow_unavailable: bool = False) -> None:
        self._generate = generate
        self._allow_unavailable = allow_unavailable

    def available(self) -> bool:
        from . import settings  # local import avoids a settings/provider cycle

        return self._allow_unavailable or settings.gemini_available()

    def complete(self, prompt: str) -> LLMResult:
        from production.cost.google_guardrail import get_google_cost_tracker

        tracker = get_google_cost_tracker()
        before = tracker.snapshot()
        lines = self._generate(prompt)
        after = tracker.snapshot()
        entries = after["entries"][len(before["entries"]):]
        return LLMResult(
            lines,
            self.id,
            self.model,
            sum(int(e.get("input_tokens", 0)) for e in entries),
            sum(int(e.get("output_tokens", 0)) for e in entries),
            sum(float(e.get("cost_usd", 0.0)) for e in entries),
        )


def provider_order(
    gemini_generate: Callable[[str], list[dict[str, Any]]],
    *,
    allow_unavailable_gemini: bool = False,
) -> list[LLMProvider]:
    """Configured first choice followed by the other provider as fallback."""
    providers: dict[str, LLMProvider] = {
        "deepseek": DeepSeekProvider(),
        "gemini": GeminiProvider(gemini_generate, allow_unavailable=allow_unavailable_gemini),
        "google": GeminiProvider(gemini_generate, allow_unavailable=allow_unavailable_gemini),
    }
    first = (os.getenv("ANIMIND_SCRIPT_PROVIDER") or "deepseek").strip().lower()
    first = first if first in providers else "deepseek"
    second = "gemini" if first == "deepseek" else "deepseek"
    return [providers[first], providers[second]]


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
            return LLMResult(**(raw | {"cached": True, "cost_usd": 0.0})), failures
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
