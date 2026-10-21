"""Compatibility shim. Pin photo checks live in production.formats.pins.photos."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.pins.photos", run_name="__main__")
else:
    from production.formats.pins import photos as _impl

    sys.modules[__name__] = _impl
