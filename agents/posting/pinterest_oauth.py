"""Compatibility shim. Pinterest OAuth lives in distribution.publishers.api.pinterest_oauth."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.publishers.api.pinterest_oauth", run_name="__main__")
else:
    from distribution.publishers.api import pinterest_oauth as _impl

    sys.modules[__name__] = _impl
