"""utils.env keeps the exact semantics of the helpers it replaced."""
from __future__ import annotations

from pathlib import Path

import pytest

from utils import env


@pytest.mark.parametrize("raw,expected", [("1", True), ("TRUE", True), (" on ", True), ("yes", True), ("0", False), ("off", False), ("", False)])
def test_env_bool(monkeypatch, raw, expected):
    monkeypatch.setenv("X_FLAG", raw)
    assert env.env_bool("X_FLAG") is expected


def test_env_bool_default(monkeypatch):
    monkeypatch.delenv("X_FLAG", raising=False)
    assert env.env_bool("X_FLAG", True) is True


def test_as_bool():
    assert env.as_bool(None, default=True) is True
    assert env.as_bool("Yes") is True
    assert env.as_bool(0) is False
    assert env.as_bool(False, default=True) is False


@pytest.mark.parametrize("raw,expected", [(None, 2.5), ("", 2.5), ("  ", 2.5), ("abc", 2.5), ("3", 3.0), (" 4.5 ", 4.5)])
def test_env_float(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("X_F", raising=False)
    else:
        monkeypatch.setenv("X_F", raw)
    assert env.env_float("X_F", 2.5) == expected


def test_parse_interval_hours(monkeypatch):
    monkeypatch.setenv("X_H", "bad")
    assert env.parse_interval_hours("X_H", 3.0) == 3.0
    monkeypatch.setenv("X_H", " 7 ")
    assert env.parse_interval_hours("X_H", 3.0) == 7.0


def test_resolve_path(tmp_path):
    assert env.resolve_path(None, tmp_path) == tmp_path
    assert env.resolve_path("~/x", tmp_path) == Path("~/x").expanduser()


def test_old_names_are_aliases():
    import agents.media.providers.model_router as mr
    import core.google_guardrail as gg
    import modules.reel_artefact.still_guard as sg

    assert mr._bool_env is env.env_bool
    assert gg._env_float is env.env_float
    assert sg._env_float is env.env_float
