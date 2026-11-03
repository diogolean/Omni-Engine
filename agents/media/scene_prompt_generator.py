"""Compatibility shim. The implementation is production.providers.image.scene_prompt_generator."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.providers.image.scene_prompt_generator", run_name="__main__")
else:
    from production.providers.image import scene_prompt_generator as _impl

    sys.modules[__name__] = _impl
