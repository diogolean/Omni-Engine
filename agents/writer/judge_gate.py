"""Compatibility shim. The implementation is production.qa.judges.judge_gate."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    import runpy

    runpy.run_module("production.qa.judges.judge_gate", run_name="__main__")
else:
    from production.qa.judges import judge_gate as _impl

    sys.modules[__name__] = _impl
