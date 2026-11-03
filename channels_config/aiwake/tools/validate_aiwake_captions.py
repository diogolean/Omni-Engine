"""Compatibility shim. The implementation is products.aiwake.tools.validate_aiwake_captions."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.validate_aiwake_captions", run_name="__main__")
else:
    from products.aiwake.tools import validate_aiwake_captions as _impl

    sys.modules[__name__] = _impl
