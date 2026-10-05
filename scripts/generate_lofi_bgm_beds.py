# -*- coding: utf-8 -*-
"""Generate ambient Rhodes lo-fi BGM beds (no drums / percussion)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from avatar_engine.audio_engine import generate_music_v2_bed

OUT_DIR = ROOT / "channels_config" / "wonder_feed" / "audio" / "bgm"

# User-locked brief (beautiful calm melody, starts from beginning, reflective).
_CORE = (
    "Ambient lo-fi background music, slow tempo around 70 BPM (calm but not "
    "dirge-slow), strictly no drums or percussion. Warm Rhodes electric piano "
    "playing melancholic, jazzy chords with a beautiful inspirational melody "
    "that starts immediately from the first second. Muffled, underwater sound "
    "with a low-pass filter. Subtle vinyl crackle and tape hiss in the background. "
    "Cinematic, nostalgic, reflective, and peaceful. Instrumental only, no vocals. "
    "Exclude: hard, aggressive, trap, hype, distorted, edm, drop, metallic, industrial, "
    "drums, percussion, beats."
)

PROMPTS = [
    ("lofi_bed_01", _CORE + " Soft major-to-minor Rhodes voicings, intimate and tender."),
    ("lofi_bed_02", _CORE + " Sparse Rhodes melody with gentle left-hand chords."),
    ("lofi_bed_03", _CORE + " Warm Rhodes with quiet jazzy seventh chords, hopeful undertone."),
    ("lofi_bed_04", _CORE + " Flowing Rhodes arpeggios, nostalgic evening mood."),
    ("lofi_bed_05", _CORE + " Simple repeating Rhodes motif, peaceful and cinematic."),
    ("lofi_bed_06", _CORE + " Soft Rhodes ballad feel, reflective and emotional."),
    ("lofi_bed_07", _CORE + " Rhodes with faint tape wobble, dreamy and calm."),
    ("lofi_bed_08", _CORE + " Melodic Rhodes phrases, inspirational but understated."),
]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Delete old beds before regen
    for old in OUT_DIR.glob("lofi_bed_*.mp3"):
        try:
            old.unlink()
            print(f"deleted {old.name}")
        except OSError as exc:
            print(f"warn delete {old.name}: {exc}")

    report = []
    for stem, prompt in PROMPTS:
        out = OUT_DIR / f"{stem}.mp3"
        print(f"\n=== Generating {stem} ===")
        print(f"prompt={prompt[:140]}...")
        path = generate_music_v2_bed(
            out,
            duration_seconds=45.0,
            music_prompt=prompt,
            topic="ambient rhodes lo-fi reflective relationships",
            style_profile="mystery",
            channel_name="wonder_feed",
        )
        ok = bool(path and Path(path).is_file() and Path(path).stat().st_size > 1000)
        entry = {
            "id": stem,
            "prompt": prompt,
            "path": str(path) if path else None,
            "ok": ok,
            "bytes": Path(path).stat().st_size if ok else 0,
        }
        report.append(entry)
        print(f" -> ok={ok} bytes={entry['bytes']}")

    meta = OUT_DIR / "lofi_bgm_manifest.json"
    meta.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nManifest -> {meta}")
    return 0 if all(r["ok"] for r in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
