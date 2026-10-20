"""Compatibility shim. The publisher contract lives in distribution.publishers.base."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.publishers.base", run_name="__main__")
else:
    from distribution.publishers import base as _impl

    sys.modules[__name__] = _impl
