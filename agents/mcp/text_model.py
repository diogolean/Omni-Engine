# -*- coding: utf-8 -*-
"""Swappable text-model interface for script generation.

Writers call :func:`complete_script` (or a :class:`TextModel` implementation).
They do not instantiate Anthropic / Gemini / DeepSeek clients themselves.

The emotional LOFI writer uses one ``models/gemini-3.8-flash`` call.
The older provider chain remains available to the strict archived writer.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

_LOG = logging.getLogger(__name__)

_CLAUDE_SONNET_INPUT_USD_PER_TOKEN: float = 3.0 / 1_000_000
_CLAUDE_SONNET_OUTPUT_USD_PER_TOKEN: float = 15.0 / 1_000_000

_CALL_LOG: list[dict[str, Any]] = []


@dataclass
class TextResult:
    text: str
    provider: str
    model: str
    input_tokens_est: int
    output_tokens_est: int
    cost_usd_est: float


class TextModel(Protocol):
    """One-shot completion. Implementations must not live inside writer prompt logic."""

    def complete(self, prompt: str, *, system: str | None = None) -> TextResult:
        ...


def reset_call_log() -> None:
    _CALL_LOG.clear()


def get_call_log() -> list[dict[str, Any]]:
    return list(_CALL_LOG)


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def estimate_cost_usd(provider: str, input_tokens: int, output_tokens: int) -> float:
    if provider == "claude":
        return (
            input_tokens * _CLAUDE_SONNET_INPUT_USD_PER_TOKEN
            + output_tokens * _CLAUDE_SONNET_OUTPUT_USD_PER_TOKEN
        )
    if provider == "deepseek":
        return (input_tokens + output_tokens) * 0.28 / 1_000_000.0
    from core.cost_tracker import GEMINI_FLASH_USD_PER_1M_TOKENS

    return (input_tokens + output_tokens) * GEMINI_FLASH_USD_PER_1M_TOKENS / 1_000_000.0


def log_text_call(
    *,
    provider: str,
    model: str,
    prompt: str = "",
    output: str = "",
    kind: str = "llm",
    input_tokens_est: int | None = None,
    output_tokens_est: int | None = None,
    cost_usd_est: float | None = None,
) -> TextResult:
    in_tok = int(input_tokens_est) if input_tokens_est is not None else estimate_tokens(prompt)
    out_tok = int(output_tokens_est) if output_tokens_est is not None else estimate_tokens(output)
    cost = round(
        float(cost_usd_est)
        if cost_usd_est is not None
        else estimate_cost_usd(provider, in_tok, out_tok),
        6,
    )
    entry = {
        "kind": kind,
        "provider": provider,
        "model": model,
        "input_tokens_est": in_tok,
        "output_tokens_est": out_tok,
        "cost_usd_est": cost,
    }
    _CALL_LOG.append(entry)
    print(
        f"[script cost] kind={kind} provider={provider} model={model} "
        f"in_tok={in_tok} out_tok={out_tok} usd={cost}"
    )
    return TextResult(
        text=output,
        provider=provider,
        model=model,
        input_tokens_est=in_tok,
        output_tokens_est=out_tok,
        cost_usd_est=cost,
    )


def _complete_without_claude(prompt: str, *, system: str | None = None) -> TextResult:
    """DeepInfra / OpenRouter / DeepSeek, then Gemini. No Anthropic call."""
    try:
        from core.spend_ledger import SpendCapExceeded
    except Exception:  # noqa: BLE001
        SpendCapExceeded = ()  # type: ignore[misc, assignment]
    try:
        from core import cheap_llm

        if cheap_llm.available("text"):
            result = cheap_llm.complete(
                prompt,
                system=system,
                max_tokens=4096,
                temperature=0.7,
                source="script",
            )
            return log_text_call(
                provider=result.provider,
                model=result.model,
                prompt=prompt,
                output=result.text,
                input_tokens_est=result.input_tokens,
                output_tokens_est=result.output_tokens,
                cost_usd_est=result.cost_usd,
            )
    except SpendCapExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("cheap text stack failed (%s) — DeepSeek then Gemini", exc)
    try:
        return _complete_deepseek(prompt, system=system)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("DeepSeek script writer failed (%s) — falling back to Gemini", exc)
        print(f"[LOFI script] WARN DeepSeek failed ({exc}); Gemini fallback")
        return _complete_gemini(prompt, system=system)


def _complete_claude(prompt: str, *, system: str | None = None) -> TextResult:
    import config as app_config

    if not app_config.claude_opted_in():
        raise RuntimeError("Claude is not opted in — refusing Anthropic script writer")
    api_key = getattr(app_config, "ANTHROPIC_API_KEY", None)
    if not api_key or not str(api_key).strip():
        raise RuntimeError("ANTHROPIC_API_KEY missing — cannot run Claude script writer")
    import anthropic  # noqa: PLC0415

    client = anthropic.Anthropic(api_key=str(api_key))
    model = app_config.resolve_claude_model()
    print(f"[LOFI script] writer=claude model={model}")
    sys_prompt = system or (
        "You write continuous first-person spoken-word scripts for short vertical reels. "
        "Every line must follow causally from the previous one. Output STRICT JSON only."
    )
    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=6000,
        system=sys_prompt,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        message = client.messages.create(**kwargs, temperature=0.82)
    except Exception as exc:  # noqa: BLE001
        if "temperature" not in str(exc).lower():
            raise
        print("[LOFI script] retry Claude without temperature (model rejects it)")
        message = client.messages.create(**kwargs)
    chunks = [getattr(b, "text", None) for b in (getattr(message, "content", []) or [])]
    text = "\n".join(c for c in chunks if c).strip()
    if not text:
        raise RuntimeError("Claude returned empty script")
    return log_text_call(provider="claude", model=model, prompt=prompt, output=text)


def _complete_gemini(
    prompt: str,
    *,
    system: str | None = None,
    model_chain: list[str] | None = None,
    generation_config: Any | None = None,
) -> TextResult:
    import config as app_config
    from agents.media.providers.gemini_utils import (
        generate_content_with_model_fallback,
        make_gemini_client_with_fallback,
    )
    from agents.media.providers.model_router import CHEAP_TEXT_CHAIN, CHEAP_TEXT_PRIMARY
    from core.google_guardrail import extract_usage_tokens

    api_key = getattr(app_config, "GEMINI_API_KEY", None)
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY missing — cannot fall back for ScriptGeneratorAgent")
    client = make_gemini_client_with_fallback(str(api_key))
    chain = list(model_chain or [CHEAP_TEXT_PRIMARY, *CHEAP_TEXT_CHAIN])
    blob = prompt if not system else f"{system}\n\n{prompt}"
    response = generate_content_with_model_fallback(
        client,
        chain,
        contents=[blob],
        config=generation_config,
    )
    text = getattr(response, "text", None) or ""
    if not text and getattr(response, "candidates", None):
        try:
            text = response.candidates[0].content.parts[0].text
        except Exception:  # noqa: BLE001
            text = ""
    text = str(text or "")
    in_tokens, out_tokens = extract_usage_tokens(
        response,
        fallback_input=estimate_tokens(blob),
    )
    return log_text_call(
        provider="gemini",
        model=(chain[0] if chain else "gemini"),
        prompt=blob,
        output=text,
        input_tokens_est=in_tokens,
        output_tokens_est=out_tokens,
    )


def _complete_gemini_flash(prompt: str, *, system: str | None = None) -> TextResult:
    """LOFI emotional writer: one requested model, one application-level call."""
    from core.economic_reel_lofi import config as lofi_cfg

    model = str(
        os.environ.get("LOFI_EMOTIONAL_WRITER_MODEL")
        or lofi_cfg.LOFI_EMOTIONAL_WRITER_MODEL
        or "models/gemini-3.8-flash"
    ).strip()
    generation_config = None
    try:
        from google.genai import types

        kwargs: dict[str, Any] = {
            "response_mime_type": "application/json",
            "temperature": 0.35,
            "top_p": 0.85,
            "max_output_tokens": 768,
            "response_json_schema": {
                "type": "object",
                "propertyOrdering": [
                    "location_anchor",
                    "human_situation",
                    "structure",
                    "closing_tool",
                    "beats",
                ],
                "properties": {
                    "location_anchor": {"type": "string", "maxLength": 56},
                    "human_situation": {"type": "string", "maxLength": 32},
                    "structure": {"type": "string", "maxLength": 32},
                    "closing_tool": {"type": "string", "maxLength": 32},
                    "beats": {
                        "type": "array",
                        "minItems": 8,
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "propertyOrdering": ["scene", "text", "visual_concept"],
                            "properties": {
                                "scene": {"type": "integer"},
                                "text": {"type": "string", "maxLength": 64},
                                "visual_concept": {"type": "string", "maxLength": 36},
                            },
                            "required": ["scene", "text", "visual_concept"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "location_anchor",
                    "human_situation",
                    "structure",
                    "closing_tool",
                    "beats",
                ],
                "additionalProperties": False,
            },
        }
        thinking = getattr(types, "ThinkingConfig", None)
        if thinking is not None:
            kwargs["thinking_config"] = thinking(thinking_budget=0)
        generation_config = types.GenerateContentConfig(**kwargs)
    except Exception:  # SDK compatibility: strict JSON remains prompt-enforced
        generation_config = None
    return _complete_gemini(
        prompt,
        system=system,
        model_chain=[model],
        generation_config=generation_config,
    )


_complete_gemini37 = _complete_gemini_flash


def _complete_deepseek(prompt: str, *, system: str | None = None) -> TextResult:
    import config as app_config
    from openai import OpenAI

    api_key = getattr(app_config, "DEEPSEEK_API_KEY", None)
    if not api_key or not str(api_key).strip():
        raise RuntimeError("DEEPSEEK_API_KEY missing — cannot run DeepSeek script writer")
    base_url = str(
        getattr(app_config, "DEEPSEEK_BASE_URL", None) or "https://api.deepseek.com/v1"
    ).strip()
    model = str(
        getattr(app_config, "DEEPSEEK_FLASH_MODEL", None)
        or getattr(app_config, "DEEPSEEK_MODEL", None)
        or "deepseek-v4-flash"
    ).strip()
    if model.lower() in {"deepseek-chat", "deepseek-coder", "deepseek-chat-v3"}:
        model = "deepseek-v4-flash"
    print(f"[LOFI script] writer=deepseek model={model}")
    client = OpenAI(api_key=str(api_key), base_url=base_url)
    sys_prompt = system or (
        "You write continuous spoken-word scripts for short vertical reels. "
        "Output STRICT JSON only."
    )
    temp = float(os.getenv("LOFI_DEEPSEEK_TEMPERATURE", "1.15") or 1.15)
    print(f"[LOFI script] deepseek temperature={temp}")
    resp = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        temperature=temp,
        extra_body={"thinking": {"type": "disabled"}},
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt},
        ],
    )
    choice = resp.choices[0]
    text = str((choice.message.content or "")).strip()
    if not text:
        raise RuntimeError(
            f"DeepSeek returned empty script (finish_reason={choice.finish_reason!r})"
        )
    return log_text_call(provider="deepseek", model=model, prompt=prompt, output=text)


WRITER_PRIMARY_DEFAULT = "deepseek-ai/DeepSeek-V4-Flash"
_WRITER_PRIMARY_PROVIDER = "deepinfra"


def writer_primary_model() -> str:
    """``Model_Preferences.Writer_Primary`` from the shared DNA.

    A Claude id, a ``-latest`` alias, or a missing value is not a writer.
    The default is DeepSeek V4 Flash.
    """
    path = Path(__file__).resolve().parents[1] / "media" / "master_dna.json"
    model = ""
    try:
        prefs = json.loads(path.read_text(encoding="utf-8")).get("Model_Preferences") or {}
        if isinstance(prefs, dict):
            model = str(prefs.get("Writer_Primary") or "").strip()
    except (OSError, ValueError, TypeError):
        model = ""
    low = model.lower()
    if not model or "claude" in low or "latest" in low or "20241022" in low:
        return WRITER_PRIMARY_DEFAULT
    return model


def complete_writer_primary(
    prompt: str,
    *,
    system: str | None = None,
    json_mode: bool = False,
    max_tokens: int = 900,
    temperature: float = 0.4,
) -> TextResult:
    """Writer_Primary on DeepInfra. This function does not call Anthropic."""
    from core import cheap_llm

    model = writer_primary_model()
    result = cheap_llm.complete(
        prompt,
        system=system,
        json_mode=json_mode,
        max_tokens=max_tokens,
        temperature=temperature,
        source="pinterest_pin_copy",
        chain=[(_WRITER_PRIMARY_PROVIDER, model)],
    )
    return log_text_call(
        provider=result.provider or _WRITER_PRIMARY_PROVIDER,
        model=result.model or model,
        prompt=prompt,
        output=result.text,
        kind="pin_copy",
        input_tokens_est=result.input_tokens,
        output_tokens_est=result.output_tokens,
        cost_usd_est=result.cost_usd,
    )


_PROVIDERS = {
    "claude": _complete_claude,
    "gemini": _complete_gemini,
    "gemini37": _complete_gemini_flash,
    "gemini38": _complete_gemini_flash,
    "gemini-3.7-flash": _complete_gemini_flash,
    "models/gemini-3.7-flash": _complete_gemini_flash,
    "gemini-3.8-flash": _complete_gemini_flash,
    "models/gemini-3.8-flash": _complete_gemini_flash,
    "deepseek": _complete_deepseek,
}


def complete_script(
    prompt: str,
    *,
    system: str | None = None,
    provider: str | None = None,
    kind: str = "writer",
) -> TextResult:
    """Run one completion. Claude is allowed only for writer/compose."""
    kind_l = (kind or "writer").strip().lower() or "writer"
    name = (provider or os.getenv("LOFI_SCRIPT_MODEL") or "").strip().lower()
    claude_ok = kind_l in {"writer", "compose"}
    if name == "claude":
        import config as app_config

        if not claude_ok or not app_config.claude_opted_in():
            print(f"[LOFI llm] Claude off for kind={kind_l} — cheap text stack")
            name = ""
    if name in _PROVIDERS:
        return _PROVIDERS[name](prompt, system=system)
    if claude_ok:
        return _complete_without_claude(prompt, system=system)
    return _complete_gemini(prompt, system=system)
