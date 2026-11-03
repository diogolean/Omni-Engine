"""Compatibility shim. The implementation is production.writers.freeform_writer."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.writers.freeform_writer", run_name="__main__")
else:
    from production.writers import freeform_writer as _impl

    sys.modules[__name__] = _impl
