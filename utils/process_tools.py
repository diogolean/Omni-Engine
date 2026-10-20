# -*- coding: utf-8 -*-
"""Compatibility shim. The implementation is :mod:`shared.ffmpeg`."""
from __future__ import annotations

import sys

from shared import ffmpeg as _ffmpeg

sys.modules[__name__] = _ffmpeg
