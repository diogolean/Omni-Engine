"""Compatibility shim. The implementation is production.qa.cta_beat."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.qa.cta_beat", run_name="__main__")
else:
    from production.qa import cta_beat as _impl

    sys.modules[__name__] = _impl
