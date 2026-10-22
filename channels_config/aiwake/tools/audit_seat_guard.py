"""Compatibility shim. The implementation is products.aiwake.tools.audit_seat_guard."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.audit_seat_guard", run_name="__main__")
else:
    from products.aiwake.tools import audit_seat_guard as _impl

    sys.modules[__name__] = _impl
