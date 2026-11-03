"""Compatibility shim. The implementation is production.cost.cost_tracker."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.cost.cost_tracker", run_name="__main__")
else:
    from production.cost import cost_tracker as _impl

    sys.modules[__name__] = _impl
