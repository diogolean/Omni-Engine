# -*- coding: utf-8 -*-
"""Verify the fixed MP4's audio aligns to scene/caption boundaries.

Loads the fixed MP4, extracts the audio, and for each of the 9 scenes measures
the start of audible speech. Scenes are separated by >= ~0.4s of near-silence
(inter-slot VO tails + BGM duck), so utterance starts should cluster at the
scene boundaries (cumsum of scene_durations).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MP4 = ROOT / "outputs/wonder_feed/clips/lofi_reel_forgiveness_putting_the_weight_down_20260827_173857_v01_fixed.mp4"
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
    expected = boundaries[: len(sd)]

    sr = 22050
    with AudioFileClip(str(MP4)) as ap:
        arr = ap.to_soundarray(fps=sr)
    if arr.ndim == 2:
        mono = np.mean(arr, axis=1)
    else:
        mono = arr
    nrm = np.abs(mono.astype(np.float32))

    # Background (BGM bed) level as the 20th percentile of RMS in 1s windows
    win = int(sr * 1.0)
    nwin = nrm.size // win
    if nwin == 0:
        nwin = 1
    frames = nrm[: nwin * win].reshape(nwin, win)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    bed = float(np.percentile(rms, 20))
    thr = max(bed * 3.0, 0.004)

    # Utterance starts: bins > thr after a gap of >=0.4s
    hop = int(sr * 0.05)  # 50ms bin
    nb = nrm.size // hop
    if nb == 0:
        nb = 1
    bins = nrm[: nb * hop].reshape(nb, hop)
    active = np.sqrt(np.mean(bins ** 2, axis=1)) > thr
    gap_bins = max(1, int(round((0.35 * sr) / hop)))
    starts = []
    in_run = False
    for i, a in enumerate(active):
        if a and not in_run:
            if not starts or (i - starts[-1][1] * hop / sr) >= 0.35:
                # require prior silence gap
                if not starts or not any(active[max(0, i - gap_bins):i]):
                    starts.append((float(i * hop) / sr, i))
            in_run = True
        elif not a:
            in_run = False
            if starts and i > starts[-1][1]:
                starts[-1] = (starts[-1][0], i)

    print("bed_rms=%.4f  voice_threshold=%.4f  utterances=%d" % (bed, thr, len(starts)))
    print("\nidxp  utterance_start  expected_scene_start  delta_s")
    print("-" * 60)
    # Match each utterance to the nearest expected boundary
    used = [False] * len(expected)
    row = 0
    for ustart, uend in starts:
        deltas = [abs(ustart - e) for e in expected]
        j = int(np.argmin(deltas))
        best = deltas[j]
        if best > 1.2:
            print(f"   {ustart:7.3f}   (no scene match)")
            continue
        row += 1
        print(f"{row:4d} {ustart:8.3f}    {expected[j]:10.3f}     {ustart - expected[j]:+.3f}")
        used[j] = True
    missed = [j + 1 for j, u in enumerate(used) if not u]
    print("\nscenes without detected start:", missed or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
