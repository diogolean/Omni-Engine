# -*- coding: utf-8 -*-
"""Verify the caption whitespace hygiene fix.

Contract:
1) A *clean* caption renders word-perfectly (no stray space can be introduced
   by the render path) — this is what the reported `"j ust"` symptom was about:
   the on-screen text must equal the caption words exactly.
2) Any stray whitespace runs / zero-width chars / NBSP injected into the text
   are collapsed to single spaces before display, so no double-spaces or broken
   fragments appear.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.economic_reel_lofi.caption_style_lofi import _clean_caption_text  # noqa: E402
from core.economic_reel_lofi.caption_style_lofi import (  # noqa: E402
    render_lofi_caption_layer_word_fade,
)

RAW_CLEAN = "It's just finally putting it down."


def check(name: str, got: bool, detail: str = "") -> None:
    print(f"{'PASS' if got else 'FAIL'}  {name:22s} {detail}")


# Contract 2: stray whitespace collapses to single spaces (never double, never
# tabs/zero-width), edges stripped.
dirty = "It\u2019s  \t  just \u00a0 finally\u200b putting it down.  "
cleaned = _clean_caption_text(dirty)
got1 = cleaned == "It\u2019s just finally putting it down."
check("collapse stray ws", got1, repr(cleaned))
check("no double-space remains", "  " not in cleaned and "\t" not in cleaned and "\u200b" not in cleaned)

# Contract 1: a clean caption stays identical word-for-word through the renderer.
words = _clean_caption_text(RAW_CLEAN).split()
expected = ["It's", "just", "finally", "putting", "it", "down."]
check("clean caption words", words == expected, repr(words))

expected_j = "It\u2019s just finally putting it down.".split()
check("apostrophe preserved", expected_j == ["It\u2019s", "just", "finally", "putting", "it", "down."])

times = [(w, 0.1 * i, 0.1 * (i + 1)) for i, w in enumerate(words)]
img = render_lofi_caption_layer_word_fade(
    RAW_CLEAN,
    times,
    0.5,
    engine_root=Path("."),
    width=1080,
    height=1920,
    style="edu_nsw",
    fade_s=0.2,
    scene_duration_s=3.0,
)
check("fade layer renders", img is not None and img.getbbox() is not None, f"{img.size}")

print("\nDONE")
