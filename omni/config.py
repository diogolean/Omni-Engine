"""Compatibility shim. Branding loading lives in accounts.brandings."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("accounts.brandings", run_name="__main__")
else:
    from accounts import brandings as _impl

    sys.modules[__name__] = _impl
