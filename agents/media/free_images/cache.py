"""Compatibility shim. The implementation is production.providers.image.free.cache."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.image.free.cache", run_name="__main__")
else:
    from production.providers.image.free import cache as _impl

    sys.modules[__name__] = _impl
