"""Compatibility shim. Pin articles live in production.formats.pins.articles."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.pins.articles", run_name="__main__")
else:
    from production.formats.pins import articles as _impl

    sys.modules[__name__] = _impl
