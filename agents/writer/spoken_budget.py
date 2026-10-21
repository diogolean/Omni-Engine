"""Compatibility shim. The implementation is production.writers.spoken_budget."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.writers.spoken_budget", run_name="__main__")
else:
    from production.writers import spoken_budget as _impl

    sys.modules[__name__] = _impl
