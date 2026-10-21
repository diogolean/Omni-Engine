"""Compatibility shim. The implementation is production.qa.config."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.qa.config", run_name="__main__")
else:
    from production.qa import config as _impl

    sys.modules[__name__] = _impl
