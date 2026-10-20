"""Compatibility shim. The registry lives in distribution.publishers.registry."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.publishers.registry", run_name="__main__")
else:
    from distribution.publishers import registry as _impl

    sys.modules[__name__] = _impl
