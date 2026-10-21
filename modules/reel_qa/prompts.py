"""Compatibility shim. The implementation is production.qa.prompts."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.qa.prompts", run_name="__main__")
else:
    from production.qa import prompts as _impl

    sys.modules[__name__] = _impl
