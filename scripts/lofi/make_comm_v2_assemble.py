# -*- coding: utf-8 -*-
"""Shim until M14. Implementation: tools/lofi/make_comm_v2_assemble.py."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TARGET = _ROOT / "tools/lofi/make_comm_v2_assemble.py"

if __name__ == "__main__":
    import runpy

    runpy.run_path(str(_TARGET), run_name="__main__")
else:
    import importlib

    sys.modules[__name__] = importlib.import_module("tools.lofi.make_comm_v2_assemble")
