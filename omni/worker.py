"""Compatibility shim. The worker lives in distribution.workers.runtime.

Compose and the Dockerfile call ``python -m distribution.workers``.
``python -m omni.worker`` stays as a fallback and runs the same ``main``.
"""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.workers.runtime", run_name="__main__")
else:
    from distribution.workers import runtime as _impl

    sys.modules[__name__] = _impl
