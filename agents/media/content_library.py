"""Compatibility shim. The implementation is production.library.content_library."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.library.content_library", run_name="__main__")
else:
    from production.library import content_library as _impl

    sys.modules[__name__] = _impl
