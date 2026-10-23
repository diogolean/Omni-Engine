"""Compatibility shim. The implementation is products.aiwake.tools.schedule_youtube."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("products.aiwake.tools.schedule_youtube", run_name="__main__")
else:
    from products.aiwake.tools import schedule_youtube as _impl

    sys.modules[__name__] = _impl
