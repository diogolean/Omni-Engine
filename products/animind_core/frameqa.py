"""Frame-by-frame mouth QA for rendered videos.

The scene records, per frame, every mouth layer it drew (``Scene.trace``) with a reference measurement
of that one mouth taken on the stage right after drawing it. ``check`` decodes every video frame,
re-measures the mouth where the trace puts it, and fails frames that show two mouth layers, a blended
camera dissolve, more stacked mouth cavities than the drawn sprite has, or an opening well above the
drawn sprite's own (or above ``CAV_ABS``).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

import numpy as np
from PIL import Image
from scipy import ndimage

CAV_ABS = 0.62  # cavity height / mouth width, any viseme
CAV_GROW = 1.3  # x the drawn sprite's own cavity, plus CAV_SLACK, before a frame counts as over-open
CAV_SLACK = 0.08
NORM_W = 64  # mouth crops are measured at this width


def measure(img: np.ndarray, box: Any) -> dict[str, Any] | None:
    """Cavity rows per mouth width and stacked-cavity count inside box (x0, y0, x1, y1) of an RGB array."""
    x0, y0, x1, y1 = (int(v) for v in box)
    H, W = img.shape[:2]
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
        return None  # mouth partly off-frame: not measurable
    if x1 - x0 < 6 or y1 - y0 < 6:
        return None
    # one scale for every measurement (stage reference at 2x supersampling, frames at any zoom)
    crop = Image.fromarray(np.ascontiguousarray(img[y0:y1, x0:x1]))
    c = np.asarray(crop.resize((NORM_W, max(6, round(NORM_W * (y1 - y0) / (x1 - x0)))), Image.LANCZOS)).astype(np.float32)
    x0, y0, x1, y1 = 0, 0, c.shape[1], c.shape[0]
    lum = c @ np.array([0.299, 0.587, 0.114], np.float32)
    border = np.concatenate([lum[0], lum[-1], lum[:, 0], lum[:, -1]])
    dark = lum < 0.55 * float(np.median(border))
    dark = ndimage.binary_opening(dark, iterations=1)
    lab, n = ndimage.label(dark)
    if not n:
        return {"cav": 0.0, "blobs": 0}
    areas = ndimage.sum(dark, lab, range(1, n + 1))
    sl = ndimage.find_objects(lab)
    bh, bw = dark.shape
    def inner(i):  # jaw lines, hair and nose strokes enter from the crop edge; a mouth cavity does not
        ys, xs = sl[i]
        cx = (xs.start + xs.stop) / 2
        return ys.start > 0 and ys.stop < bh and xs.start > 0 and xs.stop < bw and 0.15 * bw < cx < 0.85 * bw
    keep = [i for i in range(n) if inner(i)]
    if not keep:
        return {"cav": 0.0, "blobs": 0}
    amax = max(areas[i] for i in keep)
    big = [i + 1 for i in keep if areas[i] >= max(6, 0.12 * amax)]
    rows = np.zeros(dark.shape[0], bool)
    spans = []
    for i in big:
        ys, xs = sl[i - 1]
        rows[ys] = True
        spans.append((ys.start, ys.stop, xs.start, xs.stop))
    # stacked = two blobs overlapping horizontally but separated vertically by a gap
    stacked = 0
    for a in range(len(spans)):
        for b in range(a + 1, len(spans)):
            p, q = sorted([spans[a], spans[b]])
            ov = min(p[3], q[3]) - max(p[2], q[2])
            wide = min(p[3] - p[2], q[3] - q[2]) >= 0.25 * bw
            if wide and ov > 0.5 * min(p[3] - p[2], q[3] - q[2]) and q[0] - p[1] > 1:
                stacked += 1
    return {"cav": float(rows.sum()) / (x1 - x0), "blobs": 1 + stacked}



def _frames(mp4: Path, w: int, h: int) -> Iterator[np.ndarray]:
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(mp4), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = w * h * 3
    try:
        while (b := p.stdout.read(n)) and len(b) == n:  # type: ignore[union-attr]
            yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    finally:
        p.kill()
        p.wait()


def check(mp4: Path, trace: dict[str, Any]) -> dict[str, Any]:
    w, h = trace["size"]
    bad: list[tuple[int, str]] = []
    measured, worst = 0, 0.0
    for i, img in enumerate(_frames(mp4, w, h)):
        if i >= len(trace["frames"]):
            break
        f = trace["frames"][i]
        if f["takes"] > 1 and f["blur"] == 0:
            bad.append((i, "dissolve: two framings blended"))
        for m in f["mouths"]:
            who = f"char {m['char']} {m['view']} {m['v']}"
            if m["layers"] != 1:
                bad.append((i, f"{who}: {m['layers']} mouth layers"))
            ref = m.get("ref")
            r = measure(img, m["box"]) if f["blur"] == 0 else None
            if not r or not ref:
                continue
            measured += 1
            worst = max(worst, r["cav"])
            if r["cav"] > CAV_ABS:
                bad.append((i, f"{who}: open {r['cav']:.2f} > {CAV_ABS}"))
            elif r["cav"] > CAV_GROW * ref["cav"] + CAV_SLACK:
                bad.append((i, f"{who}: open {r['cav']:.2f} vs drawn {ref['cav']:.2f}"))
            if r["blobs"] >= 2 and r["blobs"] > ref["blobs"]:
                bad.append((i, f"{who}: {r['blobs']} stacked mouths vs drawn {ref['blobs']}"))
    return {"frames": len(trace["frames"]), "mouthsMeasured": measured, "badFrames": len({b[0] for b in bad}),
            "pass": not bad, "maxOpen": round(worst, 3), "examples": bad[:12]}


if __name__ == "__main__":  # python -m products.animind_core.frameqa OUT.mp4 [frames.json]
    video = Path(sys.argv[1])
    tr = Path(sys.argv[2]) if len(sys.argv) > 2 else video.with_suffix(".frames.json")
    out = check(video, json.loads(tr.read_text()))
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0 if out["pass"] else 1)
