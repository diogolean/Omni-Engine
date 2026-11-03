"""Compatibility shim. The implementation is products.aiwake.pipeline."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.pipeline", run_name="__main__")
else:
    from products.aiwake import pipeline as _impl

    sys.modules[__name__] = _impl
