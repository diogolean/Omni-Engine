"""Compatibility shim. The implementation is production.formats.lofi.output_hygiene."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.lofi.output_hygiene", run_name="__main__")
else:
    from production.formats.lofi import output_hygiene as _impl

    sys.modules[__name__] = _impl
