# -*- coding: utf-8 -*-
"""Verify the raw positioned VO sidecar (VO isolated from BGM) aligns per-scene."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SIDE = ROOT / "outputs/wonder_feed/clips/lofi_reel_forgiveness_putting_the_weight_down_20260827_173857_v02_final_vo_concat.mp3"
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
    with AudioFileClip(str(SIDE)) as ap:
        arr = ap.to_soundarray(fps=sr)
    mono = np.mean(arr, axis=1).astype(np.float32) if arr.ndim == 2 else arr.astype(np.float32)
    nrm = np.abs(mono)
    hop = int(sr * 0.02)  # 20ms bins
    nb = nrm.size // hop
    ene = np.sqrt(np.mean(nrm[: nb * hop].reshape(nb, hop) ** 2, axis=1))
    # VO-only track: threshold just above digital silence
    thr = max(float(np.percentile(ene, 25)) * 2.0, 1e-4)

    print("POSITIONED VO sidecar: per-scene window first-speech vs boundary (VO isolated from BGM)")
    all_ok = True
    for j, b0 in enumerate(boundaries):
        b1 = boundaries[j + 1] if j + 1 < len(boundaries) else acc
        lo = max(0, int(b0 / (hop / sr)))
        hi = min(nb, int(b1 / (hop / sr)) + 2)
        seg = ene[lo:hi]
        nz = np.flatnonzero(seg > thr)
        if nz.size == 0:
            print(f"  scene {j+1:2d}  window[{b0:.2f},{b1:.2f}]  no speech (empty?)")
            all_ok = False
            continue
        first_t = (lo + nz[0]) * (hop / sr)
        delta = first_t - b0
        ok = 0.0 <= delta < 0.35
        all_ok = all_ok and ok
        print(f"  scene {j+1:2d}  first_spoke={first_t:6.3f}  boundary={b0:7.3f}  delta={delta:+.3f}  {'OK' if ok else 'DRIFT'}")
    print("\nRESULT:", "ALL_OK" if all_ok else "CHECK_ABOVE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
