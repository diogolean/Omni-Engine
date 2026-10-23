"""Compatibility shim. The implementation is products.aiwake.models.sync."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.models.sync", run_name="__main__")
else:
    from products.aiwake.models import sync as _impl

    sys.modules[__name__] = _impl
