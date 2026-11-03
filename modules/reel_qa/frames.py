"""Compatibility shim. The implementation is production.qa.frames."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.qa.frames", run_name="__main__")
else:
    from production.qa import frames as _impl

    sys.modules[__name__] = _impl
