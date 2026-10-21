"""Compatibility shim. The implementation is production.cost.run_ledger."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.cost.run_ledger", run_name="__main__")
else:
    from production.cost import run_ledger as _impl

    sys.modules[__name__] = _impl
