"""Compatibility shim. The Pinterest publisher lives in distribution.publishers.api.pinterest."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("distribution.publishers.api.pinterest", run_name="__main__")
else:
    from distribution.publishers.api import pinterest as _impl

    sys.modules[__name__] = _impl
