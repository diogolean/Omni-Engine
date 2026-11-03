"""Compatibility shim. The implementation is production.providers.llm.text_model."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.llm.text_model", run_name="__main__")
else:
    from production.providers.llm import text_model as _impl

    sys.modules[__name__] = _impl
