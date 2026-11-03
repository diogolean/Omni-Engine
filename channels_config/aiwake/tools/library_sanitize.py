"""Compatibility shim. The implementation is products.aiwake.tools.library_sanitize."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.library_sanitize", run_name="__main__")
else:
    from products.aiwake.tools import library_sanitize as _impl

    sys.modules[__name__] = _impl
