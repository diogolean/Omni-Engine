"""Compatibility shim. The implementation is production.writers.writer_brief."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.writers.writer_brief", run_name="__main__")
else:
    from production.writers import writer_brief as _impl

    sys.modules[__name__] = _impl
