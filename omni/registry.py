"""Temporary shim: moved to ``core.publishing.registry``. Removed in PR-O4."""
import sys as _sys

import core.publishing.registry as _mod

_sys.modules[__name__] = _mod
