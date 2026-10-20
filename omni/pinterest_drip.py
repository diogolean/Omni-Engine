"""Compatibility shim. The per-account drip lives in distribution.scheduling.drip."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.scheduling.drip", run_name="__main__")
else:
    from distribution.scheduling import drip as _impl

    sys.modules[__name__] = _impl
