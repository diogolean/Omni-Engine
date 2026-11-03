"""Compatibility shim. The implementation is products.aiwake.models.offline."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.models.offline", run_name="__main__")
else:
    from products.aiwake.models import offline as _impl

    sys.modules[__name__] = _impl
