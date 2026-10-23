# -*- coding: utf-8 -*-
"""Compatibility shim. The CLI is ``python tools/channel_cli.py --channel principles_of_wealth_finance_economics``.

``python channels_config/principles_of_wealth_finance_economics/wealth_main.py`` stays until M14.
"""
from __future__ import annotations

import sys
from pathlib import Path

_CHANNEL = "principles_of_wealth_finance_economics"
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.channel_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["--channel", _CHANNEL, *sys.argv[1:]]))
