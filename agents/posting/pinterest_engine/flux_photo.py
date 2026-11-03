"""Compatibility shim. Flux pin photos live in production.formats.pins.flux_photo."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.pins.flux_photo", run_name="__main__")
else:
    from production.formats.pins import flux_photo as _impl

    sys.modules[__name__] = _impl
