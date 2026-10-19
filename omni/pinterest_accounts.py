"""Temporary shim: moved to ``modules.accounts.pinterest_accounts``. Removed in PR-O4."""
import sys as _sys

import modules.accounts.pinterest_accounts as _mod

_sys.modules[__name__] = _mod
