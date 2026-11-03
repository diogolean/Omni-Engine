"""Compatibility shim. The implementation is production.providers.image.remote_gpu_manager."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.image.remote_gpu_manager", run_name="__main__")
else:
    from production.providers.image import remote_gpu_manager as _impl

    sys.modules[__name__] = _impl
