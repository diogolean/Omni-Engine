# -*- coding: utf-8 -*-
"""Extract a frame at a given timestamp and save it for visual verification."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moviepy import VideoFileClip  # noqa: E402


def main() -> int:
    mp4 = sys.argv[1]
    ts = float(sys.argv[2])
    out = sys.argv[3]
    with VideoFileClip(str(mp4)) as clip:
        frame = clip.get_frame(ts)
    from PIL import Image  # noqa: E402

    Image.fromarray(frame).save(out)
    print("saved", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
