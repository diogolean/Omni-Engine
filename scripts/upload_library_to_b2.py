# -*- coding: utf-8 -*-
"""Upload a channel library to B2. Does not delete local files or B2 objects.

Local ``outputs/<channel>/library/`` is copied to ``<channel>/library/``.
JSON records and images that are already in the bucket are left as they are.

    python scripts/upload_library_to_b2.py --channel ancient_knowledge
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload a channel library to B2.")
    parser.add_argument("--channel", required=True)
    parser.add_argument(
        "--prefix",
        default="",
        help="B2 prefix. Default: <channel>/library/",
    )
    args = parser.parse_args()

    from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415
    from agents.posting.pinterest_engine.library_source import (  # noqa: PLC0415
        library_prefix,
        upload_library_tree,
    )

    cfg.configure(channel_id=args.channel)
    prefix = library_prefix(args.channel, args.prefix)
    uploaded = upload_library_tree(cfg.OUTPUTS_DIR, prefix=prefix)
    print(f"Prefix: {prefix}")
    print(f"Uploaded: {len(uploaded)}")
    for key in uploaded:
        print(f"  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
