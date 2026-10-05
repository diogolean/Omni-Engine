# -*- coding: utf-8 -*-
"""Repo-root entry point for the Aiwake caption validator."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from channels_config.aiwake.tools.validate_aiwake_captions import main

if __name__ == "__main__":
    raise SystemExit(main())
