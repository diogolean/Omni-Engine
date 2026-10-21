"""Compatibility shim. The implementation is production.providers.image.together_image."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.image.together_image", run_name="__main__")
else:
    from production.providers.image import together_image as _impl

    sys.modules[__name__] = _impl
