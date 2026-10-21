"""Compatibility shim. Pin copy lives in production.formats.pins.pin_copy."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.pins.pin_copy", run_name="__main__")
else:
    from production.formats.pins import pin_copy as _impl

    sys.modules[__name__] = _impl
