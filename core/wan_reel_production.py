"""Compatibility shim. The implementation is production.providers.image.wan_reel_production."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.image.wan_reel_production", run_name="__main__")
else:
    from production.providers.image import wan_reel_production as _impl

    sys.modules[__name__] = _impl
