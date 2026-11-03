"""Compatibility shim. The implementation is production.qa.local_gate."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.qa.local_gate", run_name="__main__")
else:
    from production.qa import local_gate as _impl

    sys.modules[__name__] = _impl
