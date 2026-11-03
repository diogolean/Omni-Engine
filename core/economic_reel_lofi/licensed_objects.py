"""Compatibility shim. The implementation is production.formats.lofi.licensed_objects."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.lofi.licensed_objects", run_name="__main__")
else:
    from production.formats.lofi import licensed_objects as _impl

    sys.modules[__name__] = _impl
