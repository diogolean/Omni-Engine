"""Compatibility shim. The implementation is production.writers.claude_polish."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.writers.claude_polish", run_name="__main__")
else:
    from production.writers import claude_polish as _impl

    sys.modules[__name__] = _impl
