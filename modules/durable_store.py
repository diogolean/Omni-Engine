# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.store`."""
from __future__ import annotations

import sys

from shared import store as _store

sys.modules[__name__] = _store
