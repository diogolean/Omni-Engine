"""Compatibility shim. The implementation is production.providers.image.prompt_alignment."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.image.prompt_alignment", run_name="__main__")
else:
    from production.providers.image import prompt_alignment as _impl

    sys.modules[__name__] = _impl
