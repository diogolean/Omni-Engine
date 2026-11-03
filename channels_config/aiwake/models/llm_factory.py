"""Compatibility shim. The implementation is products.aiwake.models.llm_factory."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.models.llm_factory", run_name="__main__")
else:
    from products.aiwake.models import llm_factory as _impl

    sys.modules[__name__] = _impl
