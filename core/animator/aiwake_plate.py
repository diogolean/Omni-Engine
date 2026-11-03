"""Compatibility shim. The implementation is products.aiwake.animator.plate."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.animator.plate", run_name="__main__")
else:
    from products.aiwake.animator import plate as _impl

    sys.modules[__name__] = _impl
