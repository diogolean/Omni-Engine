"""Compatibility shim. The implementation is production.formats.lofi.test_preview."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.lofi.test_preview", run_name="__main__")
else:
    from production.formats.lofi import test_preview as _impl

    sys.modules[__name__] = _impl
