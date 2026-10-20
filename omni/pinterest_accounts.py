"""Compatibility shim. Pinterest account loading lives in accounts.pinterest."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("accounts.pinterest", run_name="__main__")
else:
    from accounts import pinterest as _impl

    sys.modules[__name__] = _impl
