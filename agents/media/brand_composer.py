"""Compatibility shim. The implementation is production.media.brand_composer."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.media.brand_composer", run_name="__main__")
else:
    from production.media import brand_composer as _impl

    sys.modules[__name__] = _impl
