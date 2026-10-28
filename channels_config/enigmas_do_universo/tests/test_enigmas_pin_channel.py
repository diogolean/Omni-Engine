# -*- coding: utf-8 -*-
"""Enigmas do Universo is a folder. The free pin path and the drip skip are data."""
from __future__ import annotations

from pathlib import Path

from accounts.brandings import load_brandings
from accounts.pinterest import (
    account_blockers,
    active_pinterest_accounts,
    load_pinterest_accounts,
)
from distribution.scheduling.drip import publish_account
from production.formats.pins.spend import paid_image_fallback, spend_cap_usd, text_model
from production.providers.image.free.policy import resolve_policy
from production.providers.image.free.providers import default_providers
from production.writers.persona import channel_profile_rows, load_persona
from shared.channel_flags import discover_pages, flag, value

_CHANNEL = "enigmas_do_universo"
_FREE_MODEL = "openrouter:meta-llama/llama-3.3-70b-instruct:free"
_REPO = Path(__file__).resolve().parents[3]


def test_the_folder_is_discovered_without_an_engine_list() -> None:
    pages = discover_pages()
    assert _CHANNEL in pages
    rows = {row["channel"]: row for row in channel_profile_rows()}
    assert rows[_CHANNEL]["display_name"] == "Enigmas do Universo"
    assert rows[_CHANNEL]["default_format"] == "IMAGE_BACKGROUND"
    assert rows[_CHANNEL]["avatar_mode"] == "OFF"
    persona_source = (_REPO / "production" / "writers" / "persona.py").read_text(
        encoding="utf-8"
    )
    flags_source = (_REPO / "shared" / "channel_flags.py").read_text(encoding="utf-8")
    assert _CHANNEL not in persona_source
    assert _CHANNEL not in flags_source


def test_persona_loads_offline() -> None:
    persona = load_persona(_CHANNEL)
    assert persona.display_name == "Enigmas do Universo"
    assert persona.age_years == 0
    context = persona.persona_context_block()
    assert "Enigmas do Universo" in context
    assert "English" in context
    assert "Deep field" in persona.visual_style_block()
    assert persona.contextual_cta_keyword("the pulsar signal") == "SIGNAL"


def test_pin_settings_stay_on_the_free_chain() -> None:
    assert value(_CHANNEL, "IMAGE_SOURCE_POLICY") == "free_first"
    assert resolve_policy(_CHANNEL) == "free_first"
    assert flag(_CHANNEL, "PIN_PAID_IMAGE_FALLBACK", True) is False
    assert paid_image_fallback(_CHANNEL) is False
    assert value(_CHANNEL, "PIN_SPEND_CAP_USD") == 0.0
    assert spend_cap_usd(_CHANNEL) == 0.0
    assert value(_CHANNEL, "PIN_TEXT_MODEL") == _FREE_MODEL
    provider, model = text_model(_CHANNEL)
    assert provider == "openrouter"
    assert model == "meta-llama/llama-3.3-70b-instruct:free"
    assert flag(_CHANNEL, "DOCUMENTARY_PIPELINE") is False
    names = {item.name for item in default_providers()}
    assert "nasa" in names


def test_inactive_todo_account_is_skipped_by_the_drip() -> None:
    account = next(
        item
        for item in load_pinterest_accounts()
        if item.account_id == _CHANNEL
    )
    assert account.active is False
    assert account.extra["site_url"] == "TODO"
    assert account.board_name == "TODO"
    assert _CHANNEL not in {
        item.account_id for item in active_pinterest_accounts()
    }
    assert "site_url" in account_blockers(account)
    assert "board" in account_blockers(account)

    branding = next(
        row for row in load_brandings() if row.branding_id == _CHANNEL
    )
    pin = branding.platform("pinterest")
    assert pin is not None and pin.active is False
    assert pin.token_env == "PINTEREST_ACCESS_TOKEN_ENIGMAS_DO_UNIVERSO"

    stats = publish_account(account, pins=1)
    assert stats["published"] == 0
    assert "site_url" in stats["skipped_reason"]
