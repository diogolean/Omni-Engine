"""Compatibility shim. The implementation is production.formats.economic_reel.artefact.crop."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.economic_reel.artefact.crop", run_name="__main__")
else:
    from production.formats.economic_reel.artefact import crop as _impl

    sys.modules[__name__] = _impl
