"""Compatibility shim. The implementation is products.aiwake.tools.hold_seat_uploads."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.hold_seat_uploads", run_name="__main__")
else:
    from products.aiwake.tools import hold_seat_uploads as _impl

    sys.modules[__name__] = _impl
