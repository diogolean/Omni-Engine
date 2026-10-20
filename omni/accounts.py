"""Compatibility shim. Token resolution lives in accounts.tokens."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("accounts.tokens", run_name="__main__")
else:
    from accounts import tokens as _impl

    sys.modules[__name__] = _impl
