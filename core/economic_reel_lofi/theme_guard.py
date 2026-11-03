"""Compatibility shim. The implementation is production.formats.lofi.theme_guard."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.lofi.theme_guard", run_name="__main__")
else:
    from production.formats.lofi import theme_guard as _impl

    sys.modules[__name__] = _impl
