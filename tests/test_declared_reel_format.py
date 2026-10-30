# -*- coding: utf-8 -*-
"""Page config declares the still and the reel. The CLI reads either format."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from production.formats.economic_reel.shot_planner.plan import (
    planner_enabled,
    planner_flag_name,
)
from production.pipeline.channel_context import (
    resolve_cli_post_format,
    resolve_default_format,
    resolve_reel_format,
)
from shared.channel_flags import value

_PAGE = "ancient_knowledge"


def test_ancient_knowledge_locks_both_formats() -> None:
    """The still stays the default. ECONOMIC_REEL uses the declared reel format."""
    assert value(_PAGE, "DEFAULT_FORMAT") == "IMAGE_BACKGROUND"
    assert value(_PAGE, "REEL_FORMAT") == "DYNAMIC_REEL"
    assert value(_PAGE, "REEL_SHOT_PLANNER") is False

    loaded = {
        "DEFAULT_FORMAT": value(_PAGE, "DEFAULT_FORMAT"),
        "REEL_FORMAT": value(_PAGE, "REEL_FORMAT"),
    }
    assert resolve_default_format(loaded) == "IMAGE_BACKGROUND"
    assert resolve_reel_format(loaded) == "DYNAMIC_REEL"
    assert resolve_cli_post_format(loaded, post_type="STANDARD_QUOTE") == "IMAGE_BACKGROUND"
    assert resolve_cli_post_format(loaded, post_type="ECONOMIC_REEL") == "DYNAMIC_REEL"
    # An explicit --format does not pull the reel back into a still bucket.
    assert (
        resolve_cli_post_format(
            loaded, post_type="ECONOMIC_REEL", explicit="IMAGE_BACKGROUND"
        )
        == "DYNAMIC_REEL"
    )
    assert (
        resolve_cli_post_format(
            loaded, post_type="STANDARD_QUOTE", explicit="TEXT_QUOTE"
        )
        == "TEXT_QUOTE"
    )
    assert resolve_cli_post_format(loaded, post_type="WAN_REEL") == "DYNAMIC_REEL"
    assert resolve_cli_post_format(loaded, post_type="ECONOMIC_REEL_LOFI") == "DYNAMIC_REEL"

    planner = json.loads(
        Path("channels_config/ancient_knowledge/shot_planner.json").read_text(
            encoding="utf-8"
        )
    )
    assert planner["enabled_flag"] == "REEL_SHOT_PLANNER"
    assert planner_flag_name(_PAGE) == planner["enabled_flag"]


def test_cli_reads_reel_format_from_any_page() -> None:
    """No channel list. The loaded page config is the only input."""
    other = {"DEFAULT_FORMAT": "IMAGE_AVATAR", "REEL_FORMAT": "SEQUENCE_REEL"}
    assert resolve_cli_post_format(other, post_type="ECONOMIC_REEL") == "SEQUENCE_REEL"
    assert resolve_cli_post_format(other, post_type="STANDARD_QUOTE") == "IMAGE_AVATAR"
    # A page that never declares a reel format keeps the historical reel path.
    bare = {"DEFAULT_FORMAT": "IMAGE_AVATAR"}
    assert resolve_reel_format(bare) == "DYNAMIC_REEL"
    assert resolve_cli_post_format(bare, post_type="ECONOMIC_REEL") == "DYNAMIC_REEL"
    # A still declared as the reel format is not accepted.
    assert resolve_reel_format({"REEL_FORMAT": "IMAGE_BACKGROUND"}) == "DYNAMIC_REEL"
    assert resolve_reel_format({}) == "DYNAMIC_REEL"
    assert resolve_cli_post_format(
        other, post_type="REFERENCE_BASED_REELS", explicit="IMAGE_AVATAR"
    ) == "REFERENCE_BASED_REELS"


def test_invalid_reel_format_logs_a_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        assert resolve_reel_format({"REEL_FORMAT": "not-a-reel"}) == "DYNAMIC_REEL"
    assert "REEL_FORMAT" in caplog.text
    assert "NOT-A-REEL" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert resolve_reel_format({}) == "DYNAMIC_REEL"
    assert caplog.text == ""


def test_cli_run_asks_the_page_for_the_format() -> None:
    text = Path("production/pipeline/cli_run.py").read_text(encoding="utf-8")
    assert "resolve_cli_post_format(" in text


def test_shot_planner_defaults_off_and_env_overrides_the_page(monkeypatch) -> None:
    monkeypatch.delenv("REEL_SHOT_PLANNER", raising=False)
    assert planner_enabled(_PAGE) is False

    def page_on(page_id: str, name: str, default: object = None) -> object:
        del page_id
        if name == "REEL_SHOT_PLANNER":
            return True
        return default

    monkeypatch.setattr(
        "production.formats.economic_reel.shot_planner.plan.value",
        page_on,
    )
    assert planner_enabled(_PAGE) is True
    monkeypatch.setenv("REEL_SHOT_PLANNER", "0")
    assert planner_enabled(_PAGE) is False
    monkeypatch.setenv("REEL_SHOT_PLANNER", "1")
    assert planner_enabled(_PAGE) is True
    assert planner_enabled("wonder_feed") is False
