# -*- coding: utf-8 -*-
"""Compatibility shim. The package is :mod:`deploy.runpod.serverless` until M14."""
from __future__ import annotations

import sys

import deploy.runpod.serverless as _serverless

sys.modules[__name__] = _serverless
