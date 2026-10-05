# -*- coding: utf-8 -*-
"""Build the Aiwake PostPlanner Excel from ``content_library.json``.

Writes the official PostPlanner V2 workbook the other video channels import:
the humanized social caption in column B, public B2 HTTPS URL in column C.
LinkedIn copy stays on content_library.json as ``linkedin_caption``.

    {OUTPUT_PATH}/aiwake/automated_bulk_posts_import.xlsx
    {OUTPUT_PATH}/aiwake/postplanner/postplan_aiwake.xlsx

    python scripts/build_post_planner.py
    python scripts/build_post_planner.py --posts-per-day 2 --dry-run
"""
from __future__ import annotations

import sys
from pathlib import Path

_FACTORY = Path(__file__).resolve().parents[1]
if str(_FACTORY) not in sys.path:
    sys.path.insert(0, str(_FACTORY))

from channels_config.aiwake.tools.post_planner import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
