# -*- coding: utf-8 -*-
"""Compatibility shim. The CLI is ``python tools/channel_cli.py --channel <id>``.

``python agents/posting/pinterest_main.py`` stays until M14.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.pinterest_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
