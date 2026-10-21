"""Compatibility shim. The implementation is production.qa.relevance."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.qa.relevance", run_name="__main__")
else:
    from production.qa import relevance as _impl

    sys.modules[__name__] = _impl
