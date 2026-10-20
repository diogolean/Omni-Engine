"""Compatibility shim. The YouTube adapter lives in distribution.publishers.api.youtube."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.publishers.api.youtube", run_name="__main__")
else:
    from distribution.publishers.api import youtube as _impl

    sys.modules[__name__] = _impl
