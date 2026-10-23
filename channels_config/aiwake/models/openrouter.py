"""Compatibility shim. The implementation is products.aiwake.models.openrouter."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.models.openrouter", run_name="__main__")
else:
    from products.aiwake.models import openrouter as _impl

    sys.modules[__name__] = _impl
