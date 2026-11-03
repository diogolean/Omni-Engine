"""Compatibility shim. The implementation is products.aiwake.tools.supermemory_bridge."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.supermemory_bridge", run_name="__main__")
else:
    from products.aiwake.tools import supermemory_bridge as _impl

    sys.modules[__name__] = _impl
