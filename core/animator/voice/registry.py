"""Compatibility shim. The implementation is products.aiwake.animator.voice.registry."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.animator.voice.registry", run_name="__main__")
else:
    from products.aiwake.animator.voice import registry as _impl

    sys.modules[__name__] = _impl
