"""Compatibility shim. The implementation is production.formats.economic_reel.artefact.events."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.formats.economic_reel.artefact.events", run_name="__main__")
else:
    from production.formats.economic_reel.artefact import events as _impl

    sys.modules[__name__] = _impl
