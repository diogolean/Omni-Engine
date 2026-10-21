"""Compatibility shim. The implementation is production.writers.persona_dna."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.writers.persona_dna", run_name="__main__")
else:
    from production.writers import persona_dna as _impl

    sys.modules[__name__] = _impl
