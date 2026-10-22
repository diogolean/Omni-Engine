"""Compatibility shim. The implementation is production.formats.wealth.fingerprint."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.wealth.fingerprint", run_name="__main__")
else:
    from production.formats.wealth import fingerprint as _impl

    sys.modules[__name__] = _impl
