# -*- coding: utf-8 -*-
"""Claude is opt-in. Retired 3.x ids and -latest aliases are never sent."""
from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RETIRED = "claude-3-5-sonnet-latest"
_OPT_IN_ENVS = ("USE_CLAUDE", "CLAUDE_MODEL", "CLAUDE_POLISH_MODEL", "LOFI_SCRIPT_MODEL")


def _clear_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _OPT_IN_ENVS:
        monkeypatch.delenv(name, raising=False)


def test_retired_ids_are_replaced_with_a_current_model(monkeypatch: pytest.MonkeyPatch) -> None:
    import config

    _clear_opt_in(monkeypatch)
    assert config.claude_opted_in() is False
    assert config.get_best_claude_model(object()) == ""
    for retired in (
        _RETIRED,
        "claude-3-5-sonnet-20241022",
        "claude-3-opus-20240229",
        "claude-3-haiku-20240307",
        "claude-haiku-latest",
    ):
        resolved = config.resolve_claude_model(retired)
        assert resolved == config.SAFE_CLAUDE_MODEL
        assert "latest" not in resolved
        assert "claude-3" not in resolved
    assert config.resolve_claude_model("claude-sonnet-4-5") == "claude-sonnet-4-5"
    monkeypatch.setenv("CLAUDE_MODEL", _RETIRED)
    assert config.claude_opted_in() is True
    assert config.get_best_claude_model(None) == config.SAFE_CLAUDE_MODEL
    monkeypatch.setenv("USE_CLAUDE", "0")
    assert config.claude_opted_in() is False


def test_health_probe_does_not_call_anthropic_unless_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config
    from modules.reel_artefact import provider_health as ph

    _clear_opt_in(monkeypatch)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", None, raising=False)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", None, raising=False)
    monkeypatch.setattr(config, "GEMINI_API_KEY", None, raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    probes = ph.default_text_probes()
    assert "anthropic" not in probes
    with pytest.raises(RuntimeError, match="not opted in"):
        ph._probe_anthropic()

    monkeypatch.setenv("USE_CLAUDE", "1")
    monkeypatch.setenv("CLAUDE_MODEL", _RETIRED)
    assert ph._anthropic_probe_model() == config.SAFE_CLAUDE_MODEL
    probes = ph.default_text_probes()
    assert "anthropic" in probes


def test_script_writer_does_not_call_claude_unless_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.mcp import text_model

    _clear_opt_in(monkeypatch)
    calls: list[str] = []

    def _claude(*_a: object, **_k: object) -> text_model.TextResult:
        calls.append("claude")
        raise AssertionError("Anthropic was called")

    def _deepseek(prompt: str, *, system: str | None = None) -> text_model.TextResult:
        del prompt, system
        calls.append("deepseek")
        return text_model.TextResult("ok", "deepseek", "deepseek-v4-flash", 1, 1, 0.0)

    monkeypatch.setattr(text_model, "_complete_claude", _claude)
    monkeypatch.setattr(text_model, "_complete_deepseek", _deepseek)
    monkeypatch.setattr(
        "core.cheap_llm.available",
        lambda kind="text": False,
    )
    result = text_model.complete_script("hello", provider="claude", kind="writer")
    assert calls == ["deepseek"]
    assert result.provider == "deepseek"

    monkeypatch.setenv("USE_CLAUDE", "1")

    def _opted_claude(*_a: object, **_k: object) -> text_model.TextResult:
        calls.append("claude")
        return text_model.TextResult("claude-text", "claude", "claude-sonnet-4-5", 1, 1, 0.0)

    monkeypatch.setitem(text_model._PROVIDERS, "claude", _opted_claude)
    opted = text_model.complete_script("hello", provider="claude", kind="writer")
    assert calls[-1] == "claude"
    assert opted.provider == "claude"
    assert opted.model == "claude-sonnet-4-5"


def test_caption_fallbacks_omit_anthropic_unless_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.writer.caption_engine import CaptionEngine

    _clear_opt_in(monkeypatch)
    engine = CaptionEngine.__new__(CaptionEngine)
    names = [name for name, _call in engine._text_fallbacks()]
    assert names == ["openrouter", "deepseek"]
    assert engine._claude_text_complete("prompt") == ""

    monkeypatch.setenv("USE_CLAUDE", "1")
    names = [name for name, _call in engine._text_fallbacks()]
    assert names[-1] == "anthropic"


def test_inventory_does_not_open_an_anthropic_client_unless_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.posting.pinterest_engine import config as cfg
    from distribution.scheduling.inventory import MasterInventory

    _clear_opt_in(monkeypatch)
    monkeypatch.setattr(cfg, "ANTHROPIC_API_KEY", "test-key", raising=False)

    import builtins

    real_import = builtins.__import__
    imported: list[str] = []

    def _guard(name: str, *args: object, **kwargs: object):
        if name == "anthropic" or name.startswith("anthropic."):
            imported.append(name)
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guard)
    client, model = MasterInventory._load_ai_client(None)  # type: ignore[arg-type]
    assert imported == []
    assert (client, model) == (None, None)


def test_writer_primary_is_not_a_retired_claude_id() -> None:
    paths = [
        _ROOT / "agents" / "media" / "master_dna.json",
        *(_ROOT / "channels_config").glob("*/master_dna.json"),
    ]
    seen = 0
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if "Writer_Primary" not in text:
            continue
        seen += 1
        assert _RETIRED not in text
        assert "claude-3" not in text
        assert "deepseek-ai/DeepSeek-V4-Flash" in text
    assert seen >= 6
