"""Compatibility shim. The implementation is production.writers.rag.pdf_loader."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.writers.rag.pdf_loader", run_name="__main__")
else:
    from production.writers.rag import pdf_loader as _impl

    sys.modules[__name__] = _impl
