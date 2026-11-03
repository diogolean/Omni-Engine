"""Compatibility shim. Pin channel config lives in production.formats.pins.config."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.pins.config", run_name="__main__")
else:
    from production.formats.pins import config as _impl

    sys.modules[__name__] = _impl
