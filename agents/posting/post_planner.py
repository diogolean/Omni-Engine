"""Compatibility shim. Post Planner export lives in distribution.publishers.export.post_planner."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.publishers.export.post_planner", run_name="__main__")
else:
    from distribution.publishers.export import post_planner as _impl

    sys.modules[__name__] = _impl
