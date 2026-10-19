"""Temporary shim: moved to ``core.publishing.brandings``. Removed in PR-O4."""
import sys as _sys

import core.publishing.brandings as _mod

_sys.modules[__name__] = _mod
