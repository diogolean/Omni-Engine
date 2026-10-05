# -*- coding: utf-8 -*-
"""Reassemble existing trust LOFI stills with approved grading-off path + TTS + BGM."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.pipeline_paths import page_outputs_dir

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from avatar_engine.audio_engine import generate_voiceover
from channels_config.wonder_feed import page_config as page_cfg
from core_engine.economic_reel_lofi import config as lofi_cfg
from core_engine.economic_reel_lofi.assembler import assemble_lofi_reel

RUN = page_outputs_dir("wonder_feed") / "assets" / "lofi_run_20260816_031601_01"
META = page_outputs_dir("wonder_feed") / "clips" / "lofi_reel_trust_20260816_031601_v01.json"
OUT = page_outputs_dir("wonder_feed") / "clips" / "lofi_reel_trust_20260816_031601_v07.mp4"
VOICE_ID = getattr(page_cfg, "ELEVENLABS_VOICE_ID_LOFI", None) or lofi_cfg.ELEVENLABS_VOICE_ID


def main() -> int:
    meta = json.loads(META.read_text(encoding="utf-8"))
    lines = meta["script"]["lines"]
    scenes = sorted(RUN.glob("scene_*.png"))
    if len(scenes) != len(lines):
        raise SystemExit(f"scene count mismatch: {len(scenes)} vs {len(lines)}")

    captions = [str(r.get("text") or "") for r in lines]
    moods = [r.get("mood_palette") or r.get("lighting_mood") for r in lines]
    voice_paths: list[Path | None] = []
    print(f"TTS voice_id={VOICE_ID}")
    for i, cap in enumerate(captions):
        vp = RUN / f"voice_{i + 1:02d}.mp3"
        # Always regenerate so the requested ElevenLabs voice is applied.
        generate_voiceover(
            cap,
            vp,
            voice_id=VOICE_ID,
            model_id=page_cfg.ELEVENLABS_MODEL,
            speed=1.0,
            force_elevenlabs=True,
        )
        voice_paths.append(vp if vp.is_file() else None)
        print(f"voice {i + 1}: {vp.name} exists={vp.is_file()} bytes={vp.stat().st_size if vp.is_file() else 0}")

    # Always pick a bed from assets (never skip BGM).
    bgm = None
    assemble_lofi_reel(
        scenes,
        captions,
        OUT,
        engine_root=ROOT,
        page_id="wonder_feed",
        scene_duration_s=lofi_cfg.SCENE_DURATION_S,
        moods=moods,
        caption_style=lofi_cfg.DEFAULT_CAPTION_STYLE,
        voice_paths=voice_paths,
        scene_durations=None,
        bgm_path=bgm,  # None → assembler pick_lofi_bgm (required)
    )
    print(f"DONE -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
