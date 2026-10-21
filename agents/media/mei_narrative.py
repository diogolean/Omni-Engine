"""Compatibility shim. The implementation is production.writers.mei_narrative."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.writers.mei_narrative", run_name="__main__")
else:
    from production.writers import mei_narrative as _impl

    sys.modules[__name__] = _impl
