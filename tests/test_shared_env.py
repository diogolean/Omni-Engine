"""shared.env keeps the exact semantics of the helpers it replaced."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from shared import env


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("TRUE", True),
        (" on ", True),
        ("yes", True),
        ("0", False),
        ("off", False),
        ("", False),
    ],
)
def test_env_bool(monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv("X_FLAG", raw)
    assert env.env_bool("X_FLAG") is expected


def test_env_bool_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_FLAG", raising=False)
    assert env.env_bool("X_FLAG", True) is True


def test_as_bool() -> None:
    assert env.as_bool(None, default=True) is True
    assert env.as_bool("Yes") is True
    assert env.as_bool(0) is False
    assert env.as_bool(False, default=True) is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 2.5),
        ("", 2.5),
        ("  ", 2.5),
        ("abc", 2.5),
        ("3", 3.0),
        (" 4.5 ", 4.5),
    ],
)
def test_env_float(monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: float) -> None:
    if raw is None:
        monkeypatch.delenv("X_F", raising=False)
    else:
        monkeypatch.setenv("X_F", raw)
    assert env.env_float("X_F", 2.5) == expected


def test_parse_interval_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_H", "bad")
    assert env.parse_interval_hours("X_H", 3.0) == 3.0
    monkeypatch.setenv("X_H", " 7 ")
    assert env.parse_interval_hours("X_H", 3.0) == 7.0


def test_resolve_path(tmp_path: Path) -> None:
    assert env.resolve_path(None, tmp_path) == tmp_path
    assert env.resolve_path("~/x", tmp_path) == Path("~/x").expanduser()


def test_old_names_are_aliases() -> None:
    import production.providers.llm.model_router as mr
    import production.cost.google_guardrail as gg
    import production.formats.economic_reel.artefact.still_guard as sg

    assert mr._bool_env is env.env_bool
    assert gg._env_float is env.env_float
    assert sg._env_float is env.env_float


def test_skip_dotenv_leaves_the_file_unread(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SHARED_DOTENV_PROBE=from-file\n", encoding="utf-8")
    monkeypatch.delenv("SHARED_DOTENV_PROBE", raising=False)
    try:
        monkeypatch.setenv("OMNI_SKIP_DOTENV", "1")
        env.load_project_dotenv(env_file)
        assert "SHARED_DOTENV_PROBE" not in os.environ
        monkeypatch.delenv("OMNI_SKIP_DOTENV", raising=False)
        env.load_project_dotenv(env_file)
        assert os.environ["SHARED_DOTENV_PROBE"] == "from-file"
    finally:
        os.environ.pop("SHARED_DOTENV_PROBE", None)
