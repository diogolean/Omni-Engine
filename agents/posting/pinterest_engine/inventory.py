"""Compatibility shim. Master inventory lives in distribution.scheduling.inventory."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.scheduling.inventory", run_name="__main__")
else:
    from distribution.scheduling import inventory as _impl

    sys.modules[__name__] = _impl
