# -*- coding: utf-8 -*-
"""The layer bridge loads a package submodule the way a from-import does."""
from __future__ import annotations

import pytest

from shared.layer_bridge import Port, Symbol


def test_symbol_loads_a_package_submodule():
    from channels_config.master_mei import page_config as mei

    loaded = Symbol("channels_config.master_mei", "page_config")
    assert loaded.SUBTITLE_FONTSIZE == mei.SUBTITLE_FONTSIZE
    assert Port("channels_config.master_mei").page_config is mei


def test_symbol_sees_a_patched_submodule_attribute(monkeypatch):
    from channels_config.master_mei import page_config as mei

    monkeypatch.setattr(mei, "SUBTITLE_FONTSIZE", 17)
    loaded = Symbol("channels_config.master_mei", "page_config")
    assert loaded.SUBTITLE_FONTSIZE == 17


def test_missing_bridge_attribute_raises():
    missing = Symbol("shared.layer_bridge", "no_such_name")
    with pytest.raises(AttributeError):
        missing.anything
    with pytest.raises(AttributeError):
        Port("shared.layer_bridge").no_such_name
