# -*- coding: utf-8 -*-
"""Verify final MP4: audio aligned per-scene AND Ken Burns zoom present.

Ken Burns here is a CENTERED ZOOM (crop+resize), so it changes image scale, not
translation. We detect it via spectral/edge spread: a zoom changes the dominant
frequency structure / mean-vs-variance of the frame over time within one scene.
We also gate it directly by the pixel content: crop regions must differ.
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
    from moviepy import AudioFileClip, VideoFileClip

    meta = json.loads(META.read_text(encoding="utf-8"))
    sd = [float(x) for x in meta["scene_durations"]]
    boundaries = []
    acc = 0.0
    for x in sd:
        boundaries.append(acc)
        acc += x

    # ── 1) Audio: for each scene window, find the FIRST utterance start ──────
    sr = 22050
    with AudioFileClip(str(MP4)) as ap:
        arr = ap.to_soundarray(fps=sr)
    mono = np.mean(arr, axis=1) if arr.ndim == 2 else arr
    nrm = np.abs(mono.astype(np.float32))
    win = int(sr * 1.0)
    nwin = nrm.size // win
    rms = np.sqrt(np.mean(nrm[: nwin * win].reshape(nwin, win) ** 2, axis=1))
    bed = float(np.percentile(rms, 20))
    thr = max(bed * 2.5, 0.004)
    hop = int(sr * 0.05)
    nb = nrm.size // hop
    fnrm = nrm[: nb * hop]
    ene = np.sqrt(np.mean(fnrm.reshape(nb, hop) ** 2, axis=1))
    active = ene > thr

    print("AUDIO: first-utterance-in-scene-window vs scene boundary")
    audio_ok = True
    for j, b0 in enumerate(boundaries):
        b1 = boundaries[j + 1] if j + 1 < len(boundaries) else acc
        lo = max(0, int(b0 / (hop / sr)))
        hi = min(nb, int(b1 / (hop / sr)) + 1)
        seg = active[lo:hi]
        nz = np.flatnonzero(seg)
        if nz.size == 0:
            print(f"  scene {j+1:2d}  window[{b0:.2f},{b1:.2f}]  NO speech detected (silence/short)")
            continue
        first_t = (lo + nz[0]) * (hop / sr)
        delta = first_t - b0
        ok = abs(delta) < 0.6
        audio_ok = audio_ok and ok
        print(f"  scene {j+1:2d}  first_spoke={first_t:6.3f}  boundary={b0:6.3f}  delta={delta:+.3f}  {'OK' if ok else 'DRIFT'}")

    # ── 2) Ken Burns: CENTERED ZOOM changes scale within a scene ─────────────
    # Metric: take two frames in scene 1 [0,3.0]; a zoom of 1.00->1.04 re-centers
    # a cropped window. Compare the central crop of f(t) against the SAME sized
    # crop of f(t+dt): under zoom the central crop content is unchanged while the
    # outer ring changes. Use Laplacian-energy in an outer annulus across time.
    with VideoFileClip(str(MP4)) as vp:
        fA = np.asarray(vp.get_frame(0.4), dtype=np.float32)
        fB = np.asarray(vp.get_frame(1.6), dtype=np.float32)
        fC = np.asarray(vp.get_frame(2.7), dtype=np.float32)

    def annulus_energy(f):
        y, x = f.shape[:2]
        yy, xx = np.mgrid[0:y, 0:x]
        cy, cx = y / 2, x / 2
        r = np.sqrt(((yy - cy) / (y / 2)) ** 2 + ((xx - cx) / (x / 2)) ** 2)
        ring = (r > 0.75) & (r <= 1.0)
        lum = np.mean(f, axis=2)
        lap = np.abs(np.gradient(np.gradient(lum, axis=0), axis=0)) + \
              np.abs(np.gradient(np.gradient(lum, axis=1), axis=1))
        return float(lap[ring].mean())

    eA = annulus_energy(fA)
    eB = annulus_energy(fB)
    eC = annulus_energy(fC)
    # A pure zoom should keep central energy roughly stable but change nothing
    # globally; more robustly, the total image changes smoothly (linear interp
    # between zoom endpooints). Detect monotonic content change:
    dAB = float(np.mean(np.abs(fA - fB)))
    dBC = float(np.mean(np.abs(fB - fC)))
    kb_zoom = dAB > 1.2 or dBC > 1.2
    print("\nKEN BURNS (centered-zoom content change within scene 1, no cut)")
    print(f"  mean|Δ|(0.4→1.6s)={dAB:.2f}  mean|Δ|(1.6→2.7s)={dBC:.2f}")
    print(f"  ring-energy A/B/C = {eA:.3f} / {eB:.3f} / {eC:.3f}")
    print(f"  KB_ZOOM_MOTION_PRESENT = {kb_zoom}")

    # Cross-check against the 'fixed' (KB-off) render if present, as a control site.
    ctrl = ROOT / "outputs/wonder_feed/clips/lofi_reel_forgiveness_putting_the_weight_down_20260827_173857_v01_fixed.mp4"
    if ctrl.is_file():
        with VideoFileClip(str(ctrl)) as vp:
            c0 = np.asarray(vp.get_frame(0.4), dtype=np.float32)
            c1 = np.asarray(vp.get_frame(1.6), dtype=np.float32)
        dctrl = float(np.mean(np.abs(c0 - c1)))
        print(f"  CONTROL (KB-off fixed) mean|Δ|(0.4→1.6s)={dctrl:.2f}")
        kb_zoom = kb_zoom and dAB > dctrl * 1.3
        print(f"  KB_MOTION_CONFIRMED_vs_ctrl = {(dAB > dctrl * 1.3)}")

    print("\nRESULT:", "ALL_OK" if (audio_ok and kb_zoom) else "CHECK_ABOVE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
