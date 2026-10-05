# -*- coding: utf-8 -*-
"""Sanitize Aiwake catalog copy and patch live YouTube Shorts in place.

    python scripts/sync_youtube_live_metadata.py --dry-run
    python scripts/sync_youtube_live_metadata.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_FACTORY = Path(__file__).resolve().parents[1]
if str(_FACTORY) not in sys.path:
    sys.path.insert(0, str(_FACTORY))

from channels_config.aiwake.tools.sync_youtube_live_metadata import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
