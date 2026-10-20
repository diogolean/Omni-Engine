"""Compatibility shim. The Pinterest scheduler lives in distribution.scheduling.scheduler."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.scheduling.scheduler", run_name="__main__")
else:
    from distribution.scheduling import scheduler as _impl

    sys.modules[__name__] = _impl
