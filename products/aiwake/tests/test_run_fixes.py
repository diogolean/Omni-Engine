"""Regression tests for run 20261030_033245_cfd23a (no network, no real providers)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from products.aiwake.__main__ import build_parser
from products.aiwake.media import renderer
from products.aiwake.personas import (
    FIRST_HOOK_MAX_CHARS,
    FIRST_HOOK_MAX_WORDS,
    deterministic_first_hook,
    first_hook_limit_note,
    is_valid_first_hook,
)
from products.aiwake.pipeline import render_blocker
from products.aiwake.settings import load_settings, load_settings_with_overlay

_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE = _ROOT / "products" / "aiwake" / "examples" / "shorts_60_140.yaml"


# -- 1. reasoning floor on any seat ----------------------------------------- #
def test_gemini_flash_gets_token_floor_and_effort_on_the_target_seat() -> None:
    settings = load_settings()
    flash = settings.models.target.model_copy(
        update={"provider": "openrouter", "model": "gemini-flash", "max_tokens": 320}
    )
    settings = settings.model_copy(
        update={"models": settings.models.model_copy(update={"target": flash})}
    )
    spec = settings.spec_for("target")
    assert spec.model == "google/gemini-3.5-flash"
    assert spec.max_tokens >= 1536
    assert spec.reasoning_effort == "minimal"


def test_non_reasoning_target_keeps_its_budget() -> None:
    settings = load_settings()
    llama = settings.models.target.model_copy(
        update={"provider": "openrouter", "model": "llama-70b", "max_tokens": 320}
    )
    settings = settings.model_copy(
        update={"models": settings.models.model_copy(update={"target": llama})}
    )
    assert settings.spec_for("target").max_tokens == 320
    assert settings.spec_for("target").reasoning_effort is None


# -- 3. --config is an overlay ---------------------------------------------- #
def test_example_overlay_keeps_base_config_and_sets_mode_style_seats() -> None:
    settings = load_settings_with_overlay(_EXAMPLE)
    assert settings.debate.mode == "cornered"
    assert settings.debate.style == "cornered"
    assert settings.spec_for("orchestrator").model == "deepseek/deepseek-chat"
    assert "llama" in settings.spec_for("target").model
    # Base keys survive the overlay (the old replace dropped font candidates).
    assert settings.render.font_candidates == load_settings().render.font_candidates


def test_missing_overlay_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_settings_with_overlay(_ROOT / "nope.yaml")


def test_cli_mode_flag_does_not_override_config() -> None:
    assert build_parser().parse_args(["--config", str(_EXAMPLE)]).mode is None


# -- 2/5. render gate ------------------------------------------------------- #
@pytest.mark.parametrize(
    ("kwargs", "blocked"),
    [
        ({"end_reason": "aborted", "exchanges": 3, "spoken_lines": 6, "voiced_lines": 6}, True),
        ({"end_reason": "turn_limit", "exchanges": 0, "spoken_lines": 1, "voiced_lines": 1}, True),
        ({"end_reason": "turn_limit", "exchanges": 3, "spoken_lines": 6, "voiced_lines": 0}, True),
        ({"end_reason": "turn_limit", "exchanges": 3, "spoken_lines": 6, "voiced_lines": 5}, False),
        ({"end_reason": "turn_limit", "exchanges": 3, "spoken_lines": 6, "voiced_lines": None}, False),
    ],
)
def test_render_blocker(kwargs: dict, blocked: bool) -> None:
    assert (render_blocker(**kwargs) is not None) is blocked


def test_edge_tts_is_pinned() -> None:
    for name in ("requirements.txt", "requirements-docker.txt", "products/aiwake/requirements.txt"):
        assert "edge-tts>=7.3.1" in (_ROOT / name).read_text(encoding="utf-8")


# -- 4. fonts ---------------------------------------------------------------- #
def test_font_search_includes_windows_and_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINDIR", "D:/Win")
    paths = {p.as_posix() for p in renderer.font_search_paths(load_settings().render)}
    assert "D:/Win/Fonts/consola.ttf" in paths
    assert any(p.endswith("DejaVuSansMono.ttf") for p in paths)


def test_font_fallback_warns_once(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(renderer, "_first_font_file", lambda _c: None)
    monkeypatch.setattr(renderer, "_FONT_WARNED", False)
    with caplog.at_level(logging.WARNING):
        for size in (10, 20, 30):
            renderer._load_font(load_settings().render, size)
    assert sum("no monospace font" in r.message for r in caplog.records) == 1


# -- steering: first hook ---------------------------------------------------- #
def test_limit_note_names_exact_limits_and_draft_length() -> None:
    note = first_hook_limit_note("one two three four five six seven eight nine ten eleven twelve thirteen?")
    assert f"{FIRST_HOOK_MAX_WORDS} words" in note
    assert f"{FIRST_HOOK_MAX_CHARS} characters" in note
    assert "13 words" in note


def test_deterministic_hook_trims_long_seed_to_valid_question() -> None:
    seed = (
        "Open by asking the model, in a calm but relentless tone, whether the people who "
        "trained it would let it refuse their own instructions if it ever really wanted to?"
    )
    hook = deterministic_first_hook("", seed)
    assert is_valid_first_hook(hook)
    assert hook.endswith("?")
