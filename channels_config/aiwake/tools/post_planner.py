"""Compatibility shim. The implementation is products.aiwake.tools.post_planner."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.post_planner", run_name="__main__")
else:
    from products.aiwake.tools import post_planner as _impl

    sys.modules[__name__] = _impl
