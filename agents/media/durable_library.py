"""Compatibility shim. The implementation is production.library.durable_library."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.library.durable_library", run_name="__main__")
else:
    from production.library import durable_library as _impl

    sys.modules[__name__] = _impl
