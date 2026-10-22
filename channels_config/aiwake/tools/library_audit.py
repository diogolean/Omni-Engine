"""Compatibility shim. The implementation is products.aiwake.tools.library_audit."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.library_audit", run_name="__main__")
else:
    from products.aiwake.tools import library_audit as _impl

    sys.modules[__name__] = _impl
