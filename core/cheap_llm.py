# -*- coding: utf-8 -*-
"""Cheapest-first LLM calls for text and vision. Gemini is never in this chain.

Prices were measured with live one-call probes on 2026-10-19 (OpenRouter
``usage.cost`` and the DeepInfra/DeepSeek price lists). USD per 1M tokens:

Vision judge (one 960 px still + a short JSON reply, about $0.00003/call):
  openrouter google/gemma-3-12b-it         in 0.05   out 0.15
  deepinfra  google/gemma-3-12b-it         in 0.05   out 0.15
  openrouter qwen/qwen3.7-flash (no think) in 0.03   out 0.13
  openrouter z-ai/glm-5.3-flash            in 0.048  out 0.16
Text (script, captions, re-prompts, about $0.0005 per script call):
  deepinfra  deepseek-ai/DeepSeek-V4-Flash in 0.09   out 0.18
  openrouter deepseek/deepseek-v4.1-flash  in 0.0376 out 0.08 (reasons; slower)
  deepseek   deepseek-flash (thinking off) in 0.30   out 1.20 at peak

Rejected after the probe: ``qwen/qwen3.5-flash-02-23`` spent the whole
reply on reasoning and returned no text; ``typesafe/jev-router`` (Jev) cost
$0.0023 per call and also returned no text. Gemini 2.5 Flash with thinking
on cost about $0.0025 per judge call, eighty times Gemma.

Chains are ``provider:model`` lists and can be replaced with
``CHEAP_TEXT_CHAIN`` and ``CHEAP_VISION_CHAIN``. Every call is charged to
``core.spend_ledger`` and refused before the request when the reel cap is spent.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger(__name__)

BASES: dict[str, tuple[str, str]] = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "deepinfra": ("https://api.deepinfra.com/v1/openai", "DEEPINFRA_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
}

# USD per 1M tokens (input, output).
PRICES: dict[str, tuple[float, float]] = {
    "openrouter:google/gemma-3-12b-it": (0.05, 0.15),
    "deepinfra:google/gemma-3-12b-it": (0.05, 0.15),
    "openrouter:qwen/qwen3.7-flash": (0.03, 0.13),
    "openrouter:z-ai/glm-5.3-flash": (0.048, 0.16),
    "deepinfra:deepseek-ai/DeepSeek-V4-Flash": (0.09, 0.18),
    "openrouter:deepseek/deepseek-v4.1-flash": (0.0376, 0.08),
    "deepseek:deepseek-flash": (0.30, 1.20),
    "deepinfra:Qwen/Qwen3-VL-8B-Instruct": (0.18, 0.69),
    "openrouter:qwen/qwen3.5-flash-02-23": (0.065, 0.26),
}
_UNKNOWN_PRICE: tuple[float, float] = (0.30, 1.20)

DEFAULT_TEXT_CHAIN: tuple[str, ...] = (
    "deepinfra:deepseek-ai/DeepSeek-V4-Flash",
    "openrouter:deepseek/deepseek-v4.1-flash",
    "deepseek:deepseek-flash",
)
DEFAULT_VISION_CHAIN: tuple[str, ...] = (
    "openrouter:google/gemma-3-12b-it",
    "deepinfra:google/gemma-3-12b-it",
    "openrouter:qwen/qwen3.7-flash",
    "openrouter:z-ai/glm-5.3-flash",
)


class CheapLLMUnavailable(RuntimeError):
    """No configured cheap provider produced text."""


@dataclass
class CheapResult:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def _key(env_name: str) -> str:
    val = (os.getenv(env_name) or "").strip()
    if val:
        return val
    try:
        import config as app_config

        return str(getattr(app_config, env_name, "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _chain(env_name: str, default: Sequence[str]) -> list[tuple[str, str]]:
    raw = (os.getenv(env_name) or "").strip()
    items = [p.strip() for p in raw.split(",") if p.strip()] if raw else list(default)
    out: list[tuple[str, str]] = []
    for item in items:
        provider, _, model = item.partition(":")
        provider = provider.strip().lower()
        model = model.strip()
        if provider in BASES and model and (provider, model) not in out:
            out.append((provider, model))
    return out


def text_chain() -> list[tuple[str, str]]:
    return _chain("CHEAP_TEXT_CHAIN", DEFAULT_TEXT_CHAIN)


def vision_chain() -> list[tuple[str, str]]:
    return _chain("CHEAP_VISION_CHAIN", DEFAULT_VISION_CHAIN)


def available(kind: str = "text") -> bool:
    chain = vision_chain() if kind == "vision" else text_chain()
    return any(_key(BASES[p][1]) for p, _m in chain)


def price(provider: str, model: str) -> tuple[float, float]:
    return PRICES.get(f"{provider}:{model}", _UNKNOWN_PRICE)


def estimate_usd(provider: str, model: str, in_tokens: int, out_tokens: int) -> float:
    pin, pout = price(provider, model)
    return (max(0, in_tokens) * pin + max(0, out_tokens) * pout) / 1_000_000.0


def _extra_body(provider: str, model: str) -> dict[str, Any]:
    if provider == "openrouter":
        # Reasoning tokens are billed and were the empty-reply failure mode.
        return {"reasoning": {"enabled": False, "exclude": True}, "usage": {"include": True}}
    if provider == "deepseek":
        return {"thinking": {"type": "disabled"}}
    return {}


def _post_json(url: str, key: str, body: dict, timeout: float) -> dict:
    """One HTTPS POST. Tests replace this function."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://omni-engine.local",
            "X-Title": "omni-engine-cheap-llm",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _image_part(path: Path) -> dict | None:
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    mime = "image/png" if Path(path).suffix.lower() == ".png" else "image/jpeg"
    data = base64.b64encode(raw).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def _strip_fence(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]
    return raw.strip()


def _billing(status: int, text: str) -> bool:
    low = (text or "").lower()
    return status in (401, 402, 403) or "insufficient" in low or "balance" in low


def complete(
    prompt: str,
    *,
    system: str | None = None,
    images: Sequence[Path] = (),
    labels: Sequence[str] | None = None,
    json_mode: bool = False,
    max_tokens: int = 1024,
    temperature: float = 0.3,
    timeout: float = 45.0,
    source: str = "",
    chain: Sequence[tuple[str, str]] | None = None,
) -> CheapResult:
    """Run the cheapest provider that answers. Raises CheapLLMUnavailable."""
    from core import spend_ledger
    from modules.reel_artefact.provider_health import mark_dead, provider_dead

    kind = "vision" if images else "text"
    order = list(chain) if chain is not None else (vision_chain() if images else text_chain())
    content: Any
    if images:
        parts: list[dict] = [{"type": "text", "text": prompt}]
        for i, path in enumerate(images):
            part = _image_part(Path(path))
            if part is None:
                continue
            label = labels[i] if labels and i < len(labels) and labels[i] else f"FRAME {i + 1}"
            parts.append({"type": "text", "text": f"{label}:"})
            parts.append(part)
        content = parts
    else:
        content = prompt
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    in_est = max(1, (len(prompt) + len(system or "")) // 4) + 1100 * len(images)
    errors: list[str] = []
    for provider, model in order:
        base, key_env = BASES[provider]
        key = _key(key_env)
        tag = f"cheap-{provider}"
        if not key or provider_dead(tag):
            continue
        est = estimate_usd(provider, model, in_est, max_tokens)
        spend_ledger.preflight(est, provider=provider, model=model, source=source)
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        body.update(_extra_body(provider, model))
        started = time.time()
        try:
            data = _post_json(base + "/chat/completions", key, body, timeout)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "ignore")[:200]
            except Exception:  # noqa: BLE001
                detail = ""
            if json_mode and exc.code == 400 and "response_format" in detail.lower():
                body.pop("response_format", None)
                try:
                    data = _post_json(base + "/chat/completions", key, body, timeout)
                except Exception as exc2:  # noqa: BLE001
                    errors.append(f"{provider}:{model}:{type(exc2).__name__}")
                    continue
            else:
                if _billing(exc.code, detail):
                    mark_dead(tag, f"HTTP {exc.code}")
                errors.append(f"{provider}:{model}:HTTP{exc.code}")
                logger.warning("CHEAP_LLM fail | %s:%s | HTTP %s", provider, model, exc.code)
                continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{provider}:{model}:{type(exc).__name__}")
            logger.warning("CHEAP_LLM fail | %s:%s | %s", provider, model, type(exc).__name__)
            continue
        try:
            text = str(data["choices"][0]["message"].get("content") or "")
        except (KeyError, IndexError, TypeError, AttributeError):
            text = ""
        usage = (data.get("usage") if isinstance(data, dict) else None) or {}
        in_tok = int(usage.get("prompt_tokens") or in_est)
        out_tok = int(usage.get("completion_tokens") or max(1, len(text) // 4))
        billed = usage.get("cost", usage.get("estimated_cost"))
        if isinstance(billed, (int, float)):
            cost = float(billed)
        else:
            cost = estimate_usd(provider, model, in_tok, out_tok)
        spend_ledger.record(
            provider=provider, model=model, cost_usd=cost, source=source, kind=kind,
            input_tokens=in_tok, output_tokens=out_tok, images=len(images),
            status="ok" if text.strip() else "empty",
        )
        if not text.strip():
            errors.append(f"{provider}:{model}:empty")
            continue
        if json_mode:
            text = _strip_fence(text)
        logger.info(
            "CHEAP_LLM ok | %s:%s | %s | %.1fs | $%.6f",
            provider, model, kind, time.time() - started, cost,
        )
        return CheapResult(
            text=text.strip(), provider=provider, model=model,
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
        )
    detail = "; ".join(errors) or "no key"
    raise CheapLLMUnavailable(f"no cheap {kind} provider answered ({detail})")
