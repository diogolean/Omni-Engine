"""Compatibility shim. The implementation is production.formats.wealth.fingerprint_engine."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.wealth.fingerprint_engine", run_name="__main__")
else:
    from production.formats.wealth import fingerprint_engine as _impl

    sys.modules[__name__] = _impl
