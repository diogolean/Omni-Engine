# -*- coding: utf-8 -*-
"""Pin copy uses Writer_Primary unless Claude is opted in. Templates are last."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from production.providers.llm.text_model import TextResult
from distribution.scheduling.inventory import (
    MasterInventory,
    build_caption_regex,
)

_OPT_IN = ("USE_CLAUDE", "CLAUDE_MODEL", "CLAUDE_POLISH_MODEL", "LOFI_SCRIPT_MODEL")
_TOPIC = "Oak Island"
_SOURCE = "The money pit is still open."
_WRITER_CAPTION = "Quarry stones still sit above the pit."


def _clear_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _OPT_IN:
        monkeypatch.delenv(name, raising=False)


def _library(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    (library / "post_oak.json").write_text(
        json.dumps(
            {
                "topic": _TOPIC,
                "subject_slug": "oak-island",
                "variant_index": 0,
                "humanized_caption": _SOURCE,
            }
        ),
        encoding="utf-8",
    )


def test_writer_primary_calls_deepinfra_not_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    from production.providers.llm import text_model
    from production.providers.llm.cheap_llm import CheapResult

    _clear_opt_in(monkeypatch)
    seen: dict = {}

    def _complete(prompt: str, **kwargs: object) -> CheapResult:
        seen["prompt"] = prompt
        seen["chain"] = kwargs.get("chain")
        return CheapResult("ok", "deepinfra", text_model.WRITER_PRIMARY_DEFAULT, 2, 2, 0.0)

    monkeypatch.setattr("core.cheap_llm.complete", _complete)
    result = text_model.complete_writer_primary("Write the pin.", system="sys")
    assert text_model.writer_primary_model() == "deepseek-ai/DeepSeek-V4-Flash"
    assert seen["chain"] == [("deepinfra", "deepseek-ai/DeepSeek-V4-Flash")]
    assert result.provider == "deepinfra"
    assert result.model == "deepseek-ai/DeepSeek-V4-Flash"
    assert seen["prompt"] == "Write the pin."


def test_pin_copy_calls_text_model_when_claude_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_opt_in(monkeypatch)
    _library(tmp_path)
    calls: list[str] = []

    def _writer(prompt: str, **_kwargs: object) -> TextResult:
        calls.append(prompt)
        body = json.dumps(
            {
                "pinterest_title": "Oak Island pit",
                "pinterest_caption": _WRITER_CAPTION,
                "visual_hook": "Who dug the pit",
            }
        )
        return TextResult(body, "deepinfra", "deepseek-ai/DeepSeek-V4-Flash", 1, 1, 0.0)

    def _claude(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError("Claude pin copy ran")

    monkeypatch.setattr("agents.mcp.text_model.complete_writer_primary", _writer)
    monkeypatch.setattr("distribution.scheduling.inventory._generate_ai", _claude)
    data = MasterInventory(outputs_dir=tmp_path).build(use_ai=True, dry_run=True)
    assert len(calls) == 1
    assert _TOPIC in calls[0]
    caption = data["entries"][0]["pinterest_caption"]
    assert _WRITER_CAPTION in caption


def test_pin_copy_uses_templates_when_text_model_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_opt_in(monkeypatch)
    _library(tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> TextResult:
        raise RuntimeError("writer down")

    monkeypatch.setattr("agents.mcp.text_model.complete_writer_primary", _boom)
    data = MasterInventory(outputs_dir=tmp_path).build(use_ai=True, dry_run=True)
    entry = data["entries"][0]
    assert entry["pinterest_caption"] == build_caption_regex(_SOURCE, 0)
    assert _WRITER_CAPTION not in entry["pinterest_caption"]
    assert entry["pinterest_title"]
    assert entry["visual_hook"]
