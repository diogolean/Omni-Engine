# -*- coding: utf-8 -*-
"""Cross-correlate each source VO clip against the rendered audio to confirm
the actual placement offset vs the scene boundary (frame-locked verification).

This is a direct, exact check (vs my energy detector which can skip quiet
leading syllables).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MP4 = ROOT / "outputs/wonder_feed/clips/lofi_reel_forgiveness_putting_the_weight_down_20260827_173857_v02_final.mp4"
META = ROOT / "outputs/wonder_feed/clips/lofi_reel_forgiveness_putting_the_weight_down_20260827_173857_v01.json"


def main() -> int:
    from moviepy import AudioFileClip

    meta = json.loads(META.read_text(encoding="utf-8"))
    sd = [float(x) for x in meta["scene_durations"]]
    boundaries = []
    acc = 0.0
    for x in sd:
        boundaries.append(acc)
        acc += x

    sr = 22050
    with AudioFileClip(str(MP4)) as ap:
        mix = ap.to_soundarray(fps=sr)
    mix = np.mean(mix, axis=1).astype(np.float32) if mix.ndim == 2 else mix.astype(np.float32)
    mix_len = mix.size if mix is not None else 0

    print("VO cross-correlation vs rendered audio (peak lag = true placement):")
    print("  scene  boundary  best_lag(s)  delta(s)  OK")
    all_ok = True
    for i, vp in enumerate(meta["voice_paths"]):
        if not vp or not Path(vp).is_file():
            print(f"  {i+1:3d}  {boundaries[i]:7.3f}  (no vo file)")
            continue
        try:
            vclip = AudioFileClip(str(vp))
            varr = vclip.to_soundarray(fps=sr)
            vclip.close()
        except Exception as exc:  # noqa: BLE001
            print(f"  {i+1:3d}  (vo load failed: {exc})")
            continue
        vmono = np.mean(varr, axis=1).astype(np.float32) if varr.ndim == 2 else varr.astype(np.float32)
        # Search around the scene boundary ±1.2s for the best windowed correlation
        b0 = boundaries[i]
        n = vmono.size
        maxshift = int(1.5 * sr)
        lo = max(0, int((b0 - maxshift) * sr))
        hi = min(mix_len, int((b0 + maxshift) * sr) + n)
        if hi <= lo + n or n <= sr * 0.02:
            print(f"  {i+1:3d}  {b0:7.3f}  (skip)")
            continue
        seg = mix[lo:hi]
        corr = np.convolve(seg, vmono[::-1], mode="valid")
        pl = int(np.argmax(corr))
        est = (lo + pl) / float(sr)
        delta = est - b0
        ok = abs(delta) < 0.20
        all_ok = all_ok and ok
        print(f"  {i+1:3d}  {b0:7.3f}  {est:10.3f}  {delta:+6.3f}  {'OK' if ok else 'DRIFT'}")
    print("\nRESULT:", "ALL_OK" if all_ok else "CHECK_ABOVE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
