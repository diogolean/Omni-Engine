# -*- coding: utf-8 -*-
"""Re-assemble a LOFI reel's MP4 WITHOUT regenerating stills/VO.

Reuses the exact scene images, voice clips, captions, scene durations and
render preset from an existing meta JSON, then calls the assembler so the
audio/caption timeline is rebuilt frame-locked (the desync fix).

Usage:
    python scripts/lofi/_reassemble_fix.py <meta.json> <output.mp4>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("LOFI_SKIP_COHERENCE_LLM", "1")


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: _reassemble_fix.py <meta.json> <output.mp4>", file=sys.stderr)
        return 2
    meta_path = Path(sys.argv[1])
    out_mp4 = Path(sys.argv[2])
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    scene_images = [Path(p) for p in (meta.get("scene_images") or [])]
    captions = [str(r.get("text") or "") for r in (meta.get("caption_timing") or [])]
    scene_durations = [float(x) for x in (meta.get("scene_durations") or [])]
    voice_paths = [Path(p) if p else None for p in (meta.get("voice_paths") or [])]
    caption_style = str(meta.get("caption_style") or "edu_nsw")

    if not captions:
        captions = [str(r.get("text") or "") for r in (meta.get("script") or {}).get("lines") or []]

    n = len(scene_images)
    if not scene_durations or len(scene_durations) < n:
        scene_durations = [float(meta.get("duration_requested_s") or 27) / n] * n
    scene_durations = scene_durations[:n]

    if len(captions) < n:
        captions = (captions + [""] * n)[:n]

    # Word-fade timings: spread each caption's words across its VO's ORIGINAL
    # file length so the assembler's remap maps them onto normalized audio
    # without double-shifting. Files that fail to open fall back to None
    # (static caption for that scene — still scene-synced).
    from core.economic_reel_lofi import config as lofi_cfg

    word_timings_per_scene = []
    for i, cap in enumerate(captions):
        vp = voice_paths[i] if i < len(voice_paths) else None
        dur = 0.0
        if vp is not None and Path(vp).is_file():
            try:
                from core.economic_reel_lofi.assembler import normalize_vo_pcm

                _a, _sr, vmeta = normalize_vo_pcm(vp)
                dur = float(vmeta.get("file_duration_s") or 0.0)
            except Exception:  # noqa: BLE001
                dur = 0.0
        if dur <= 0.05 or not (cap or "").strip():
            word_timings_per_scene.append(None)
            continue
        from agents.media.audio_engine import approximate_word_timings

        word_timings_per_scene.append(approximate_word_timings(cap, dur))

    from core.economic_reel_lofi.assembler import assemble_lofi_reel

    print(
        f"[reassemble] images={n} captions={len(captions)} "
        f"durations={[round(x, 2) for x in scene_durations]} "
        f"style={caption_style} out={out_mp4}"
    )

    result = assemble_lofi_reel(
        scene_images,
        captions,
        out_mp4,
        engine_root=ROOT,
        page_id=str(meta.get("page") or "wonder_feed"),
        scene_duration_s=float(lofi_cfg.SCENE_DURATION_S),
        scene_durations=scene_durations,
        caption_style=caption_style,
        voice_paths=voice_paths,
        word_timings_per_scene=word_timings_per_scene,
        render_effects=None,  # production preset (grain/vignette/fade ON, KB/pulse OFF)
    )
    print(f"[reassemble] DONE -> {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
