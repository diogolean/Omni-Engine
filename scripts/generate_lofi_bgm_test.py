# -*- coding: utf-8 -*-
"""Delete old LOFI BGM beds and generate ONE Whispers test track for manual approval."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from avatar_engine.audio_engine import generate_music_v2_bed
from core_engine.economic_reel_lofi import config as lofi_cfg

OUT_DIR = ROOT / "channels_config" / "wonder_feed" / "audio" / "bgm"
TEST_STEM = "lofi_bed_test_01"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Delete previous beds (including older tests)
    for old in list(OUT_DIR.glob("lofi_bed_*.mp3")) + list(OUT_DIR.glob("lofi_bed_test*.mp3")):
        try:
            old.unlink()
            print(f"deleted {old.name}")
        except OSError as exc:
            print(f"warn delete {old.name}: {exc}")

    prompt = str(getattr(lofi_cfg, "BGM_TEST_PROMPT", "") or "").strip()
    out = OUT_DIR / f"{TEST_STEM}.mp3"
    print(f"\n=== Generating TEST bed {TEST_STEM} ===")
    print(f"prompt={prompt[:160]}...")
    path = generate_music_v2_bed(
        out,
        duration_seconds=45.0,
        music_prompt=prompt,
        topic="dark ambient melancholic lo-fi",
        style_profile="mystery",
        channel_name="wonder_feed",
    )
    ok = bool(path and Path(path).is_file() and Path(path).stat().st_size > 1000)
    report = {
        "id": TEST_STEM,
        "status": "pending_manual_approval",
        "prompt": prompt,
        "path": str(path) if path else None,
        "ok": ok,
        "bytes": Path(path).stat().st_size if ok else 0,
        "note": "Do not bulk-replace pipeline beds until this test is approved.",
    }
    meta = OUT_DIR / "lofi_bgm_manifest.json"
    meta.write_text(json.dumps([report], indent=2, ensure_ascii=False), encoding="utf-8")
    print(f" -> ok={ok} bytes={report['bytes']}")
    print(f"Manifest -> {meta}")
    print(f"\nLISTEN BEFORE BULK: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
