"""Compatibility shim. The implementation is production.writers.copywriter."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.writers.copywriter", run_name="__main__")
else:
    from production.writers import copywriter as _impl

    sys.modules[__name__] = _impl
