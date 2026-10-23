"""Compatibility shim. The implementation is products.aiwake.animator.asset_generator."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.animator.asset_generator", run_name="__main__")
else:
    from products.aiwake.animator import asset_generator as _impl

    sys.modules[__name__] = _impl
