# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.memory.transport`."""
from __future__ import annotations

import sys

import shared.memory.transport as _transport

sys.modules[__name__] = _transport
