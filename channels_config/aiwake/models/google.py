"""Compatibility shim. The implementation is products.aiwake.models.google."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.models.google", run_name="__main__")
else:
    from products.aiwake.models import google as _impl

    sys.modules[__name__] = _impl
