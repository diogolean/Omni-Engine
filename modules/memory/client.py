# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.memory.client`."""
from __future__ import annotations

import sys

import shared.memory.client as _client

sys.modules[__name__] = _client
