"""omni/ is a temporary alias of the moved modules (removed in PR-O4)."""
from __future__ import annotations

import importlib

import pytest

PAIRS = [
    ("omni.models", "core.publishing.models"),
    ("omni.publisher", "core.publishing.publisher"),
    ("omni.registry", "core.publishing.registry"),
    ("omni.pinterest_drip", "core.publishing.pinterest_drip"),
    ("omni.config", "core.publishing.brandings"),
    ("omni.accounts", "modules.accounts.tokens"),
    ("omni.pinterest_accounts", "modules.accounts.pinterest_accounts"),
    ("omni.adapters.pinterest", "core.publishing.adapters.pinterest"),
    ("omni.adapters.youtube", "core.publishing.adapters.youtube"),
    ("omni.worker", "core.worker"),
]


@pytest.mark.parametrize("old,new", PAIRS)
def test_shim_is_same_module(old, new):
    assert importlib.import_module(old) is importlib.import_module(new)


def test_ledger_and_repo_root_unchanged():
    from pathlib import Path

    from core.publishing import brandings, publisher

    root = Path(__file__).resolve().parents[1]
    assert brandings.repo_root() == root
    assert publisher.default_ledger_path().parts[-2:] == ("_omni", "publish_ledger.json")
