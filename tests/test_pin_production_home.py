# -*- coding: utf-8 -*-
"""Pin production lives in production.formats.pins. The old paths are the same objects."""
from __future__ import annotations

from pathlib import Path

import agents.posting.pinterest_engine.articles as shim_articles
import agents.posting.pinterest_engine.config as shim_config
import agents.posting.pinterest_engine.flux_photo as shim_flux
import agents.posting.pinterest_engine.image_transformer as shim_cards
import agents.posting.pinterest_engine.photos as shim_photos
import agents.posting.pinterest_engine.pin_copy as shim_copy
import production.formats.pins.articles as articles
import production.formats.pins.config as config
import production.formats.pins.flux_photo as flux_photo
import production.formats.pins.image_transformer as cards
import production.formats.pins.photos as photos
import production.formats.pins.pin_copy as pin_copy

_REPO = Path(__file__).resolve().parents[1]


def test_pin_shims_are_the_production_modules() -> None:
    assert shim_articles is articles
    assert shim_config is config
    assert shim_flux is flux_photo
    assert shim_cards is cards
    assert shim_photos is photos
    assert shim_copy is pin_copy
    assert shim_articles.Article is articles.Article
    assert shim_cards.PinTransformer is cards.PinTransformer
    assert shim_photos.is_text_graphic is photos.is_text_graphic
    assert shim_flux.generate_flux_photo is flux_photo.generate_flux_photo
    assert shim_copy.build_pin_description is pin_copy.build_pin_description


def test_pin_config_root_is_unchanged() -> None:
    """The move keeps the same parents depth, so channel packs still resolve."""
    assert config.ENGINE_ROOT == _REPO
    assert config.CHANNELS_CONFIG_DIR == _REPO / "channels_config"
    assert config.DOTENV_PATH == _REPO / ".env"
