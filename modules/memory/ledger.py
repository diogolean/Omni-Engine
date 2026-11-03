# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.memory.ledger`."""
from __future__ import annotations

import sys

import shared.memory.ledger as _ledger

sys.modules[__name__] = _ledger
