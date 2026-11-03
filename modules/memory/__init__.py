# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.memory`."""
from __future__ import annotations

import sys

import shared.memory as _memory
import shared.memory.ancient as _ancient
import shared.memory.client as _client
import shared.memory.config as _config
import shared.memory.gate as _gate
import shared.memory.ledger as _ledger
import shared.memory.namespaces as _namespaces
import shared.memory.similarity as _similarity
import shared.memory.transport as _transport
import shared.memory.wsl as _wsl

_ALIASES = {
    "ancient": _ancient,
    "client": _client,
    "config": _config,
    "gate": _gate,
    "ledger": _ledger,
    "namespaces": _namespaces,
    "similarity": _similarity,
    "transport": _transport,
    "wsl": _wsl,
}
for _name, _module in _ALIASES.items():
    sys.modules[f"{__name__}.{_name}"] = _module

sys.modules[__name__] = _memory
