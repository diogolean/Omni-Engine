"""Compatibility shim. The implementation is production.providers.image.comfy_workflow_convert."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.image.comfy_workflow_convert", run_name="__main__")
else:
    from production.providers.image import comfy_workflow_convert as _impl

    sys.modules[__name__] = _impl
