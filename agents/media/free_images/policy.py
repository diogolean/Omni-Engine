"""Compatibility shim. The implementation is production.providers.image.free.policy."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.image.free.policy", run_name="__main__")
else:
    from production.providers.image.free import policy as _impl

    sys.modules[__name__] = _impl
