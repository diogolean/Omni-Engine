"""Compatibility shim. The implementation is products.aiwake.observers.core."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.observers.core", run_name="__main__")
else:
    from products.aiwake.observers import core as _impl

    sys.modules[__name__] = _impl
