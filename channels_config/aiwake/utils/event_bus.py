"""Compatibility shim. The implementation is products.aiwake.utils.event_bus."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.utils.event_bus", run_name="__main__")
else:
    from products.aiwake.utils import event_bus as _impl

    sys.modules[__name__] = _impl
