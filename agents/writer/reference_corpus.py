"""Compatibility shim. The implementation is production.writers.reference_corpus."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.writers.reference_corpus", run_name="__main__")
else:
    from production.writers import reference_corpus as _impl

    sys.modules[__name__] = _impl
