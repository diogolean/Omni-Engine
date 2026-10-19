"""Temporary shim: moved to ``core.publishing.models``. Removed in PR-O4."""
import sys as _sys

import core.publishing.models as _mod

_sys.modules[__name__] = _mod
