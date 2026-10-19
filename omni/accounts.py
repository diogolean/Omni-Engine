"""Temporary shim: moved to ``modules.accounts.tokens``. Removed in PR-O4."""
import sys as _sys

import modules.accounts.tokens as _mod

_sys.modules[__name__] = _mod
