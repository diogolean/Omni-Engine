"""Compatibility shim. The pin card renderer lives in production.formats.pins.image_transformer."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.pins.image_transformer", run_name="__main__")
else:
    from production.formats.pins import image_transformer as _impl

    sys.modules[__name__] = _impl
