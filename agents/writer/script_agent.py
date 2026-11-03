"""Compatibility shim. The implementation is production.writers.script_agent."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.writers.script_agent", run_name="__main__")
else:
    from production.writers import script_agent as _impl

    sys.modules[__name__] = _impl
