"""Compatibility shim. The implementation is production.providers.image.image_provider."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.image.image_provider", run_name="__main__")
else:
    from production.providers.image import image_provider as _impl

    sys.modules[__name__] = _impl
