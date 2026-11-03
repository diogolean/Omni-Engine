"""Compatibility shim. The implementation is production.providers.image.free.models."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.image.free.models", run_name="__main__")
else:
    from production.providers.image.free import models as _impl

    sys.modules[__name__] = _impl
