"""Compatibility shim. The implementation is products.aiwake.tools.seed_supermemory."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.seed_supermemory", run_name="__main__")
else:
    from products.aiwake.tools import seed_supermemory as _impl

    sys.modules[__name__] = _impl
