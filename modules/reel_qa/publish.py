"""Compatibility shim. The implementation is production.qa.publish."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.qa.publish", run_name="__main__")
else:
    from production.qa import publish as _impl

    sys.modules[__name__] = _impl
