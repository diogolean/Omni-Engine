"""Automatic QA gates for a generated rig, plus the stored score and review sheet.

Each gate returns ``{"pass": bool, "value": number, "limit": ...}``; :func:`score` folds them
into one 0-100 number saved in ``<skin>/qa.json`` next to ``<skin>/qa_sheet.png``. The rig
builder re-draws an asset whose gate fails while the Gemini cap still has room for it."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

IMAGE_USD = 0.039
LIMITS = {"skinDeltaE": 6.0, "residualMouth": 0.04, "frontOffset": 0.05, "landmarkDrift": 0.08,
          "hardEdge": 0.0, "mouthRange": 10.0, "faceWidthPx": 200}
WEIGHTS = {"skin": 20, "mouthless": 20, "mouths": 10, "front": 15, "landmarks": 15, "edges": 15, "resolution": 5}


def _lab(rgb: np.ndarray) -> np.ndarray:
    import cv2  # noqa: PLC0415
    lab = cv2.cvtColor(rgb.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    return np.stack([lab[:, 0] * 100 / 255, lab[:, 1] - 128, lab[:, 2] - 128], axis=1)


def skin_delta_e(view: Path) -> dict[str, Any]:
    """Median CIE76 dE between each mouth sprite's soft rim and the head right under it."""
    v = json.loads((view / "view.json").read_text())
    canvas = Image.new("RGBA", (v["canvas"]["w"], v["canvas"]["h"]), (0, 0, 0, 0))
    for layer in sorted(v["layers"], key=lambda l: l["z"]):
        canvas.alpha_composite(Image.open(view / layer["url"]).convert("RGBA"), (layer["x"], layer["y"]))
    worst = 0.0
    for name, m in v["visemes"].items():
        p = view / m["url"]
        spr = np.asarray(Image.open(p).convert("RGBA")).astype(np.int16)
        under = np.asarray(canvas.crop((m["x"], m["y"], m["x"] + spr.shape[1], m["y"] + spr.shape[0]))).astype(np.int16)
        rim = (spr[..., 3] > 40) & (spr[..., 3] < 200) & (under[..., 3] > 200)
        if rim.sum() < 10:
            continue
        de = np.linalg.norm(_lab(spr[rim][:, :3]) - _lab(under[rim][:, :3]), axis=1)
        worst = max(worst, float(np.median(de)))
    return {"pass": worst <= LIMITS["skinDeltaE"], "value": round(worst, 2), "limit": LIMITS["skinDeltaE"]}


def residual_mouth(view: Path, mouth_box: list[int]) -> dict[str, Any]:
    """Share of lip-red / open-mouth-dark pixels left in the mouthless head under the lips area."""
    v = json.loads((view / "view.json").read_text())
    head = next(l for l in v["layers"] if l["name"] == "head")
    img = np.asarray(Image.open(view / head["url"]).convert("RGBA")).astype(int)
    x0, y0, x1, y1 = (mouth_box[0] - head["x"], mouth_box[1] - head["y"], mouth_box[2] - head["x"], mouth_box[3] - head["y"])
    w, h = x1 - x0, y1 - y0
    cx0, cy0, cx1, cy1 = max(0, x0 + w // 4), max(0, y0 + h // 3), max(0, x1 - w // 4), max(0, y1)
    patch = img[cy0:cy1, cx0:cx1]
    if patch.size == 0:
        return {"pass": True, "value": 0.0, "limit": LIMITS["residualMouth"]}
    r, g, b, a = (patch[..., i] for i in range(4))
    # relative to this character's own skin (anime skin is often pink): median redness of the head
    hr = img[..., 0] - img[..., 1]
    ring = img[max(0, y0 - h):max(0, y0), max(0, x0):max(0, x1)]
    ring = ring[ring[..., 3] > 128]
    skin_red = float(np.median(ring[:, 0] - ring[:, 1])) if len(ring) else float(np.median(hr[img[..., 3] > 128]))
    lips = (r - g > skin_red + 30) & (r > b + 25) & (a > 128)
    # saturated dark red only: a brown beard drawn over the closed mouth is not a residual cavity
    cavity = ((r + g + b) / 3 < 70) & (r > g + 30) & (r - b > 40) & (a > 128)
    share = float((lips | cavity).mean())
    return {"pass": share <= LIMITS["residualMouth"], "value": round(share, 3), "limit": LIMITS["residualMouth"]}


def front_symmetry(front_boxes: dict[str, list[int]] | None) -> dict[str, Any]:
    from .rig import frontality  # noqa: PLC0415
    if not front_boxes:
        return {"pass": False, "value": None, "limit": LIMITS["frontOffset"], "note": "no front view"}
    f = frontality({k: tuple(v) for k, v in front_boxes.items()})
    off = max(f["eyesOffset"], f["mouthOffset"])
    return {"pass": bool(f["frontal"]), "value": round(float(off), 3), "limit": LIMITS["frontOffset"]}


def _rel(boxes: dict[str, list[int]]) -> np.ndarray:
    fx0, fy0, fx1, fy1 = boxes["face"]
    fh = max(1, fy1 - fy0)
    ys = [((boxes[k][1] + boxes[k][3]) / 2 - fy0) / fh for k in ("left_eye", "right_eye", "mouth")]
    return np.array([(ys[0] + ys[1]) / 2, ys[2]])


def landmark_alignment(side: dict[str, list[int]], front: dict[str, list[int]] | None) -> dict[str, Any]:
    """Eye line and mouth height (share of face height) must agree between side and front art."""
    if not front:
        return {"pass": True, "value": 0.0, "limit": LIMITS["landmarkDrift"], "note": "front reuses side art"}
    d = float(np.abs(_rel(side) - _rel(front)).max())
    return {"pass": d <= LIMITS["landmarkDrift"], "value": round(d, 3), "limit": LIMITS["landmarkDrift"]}


def mouth_range(view: Path) -> dict[str, Any]:
    """The visemes must actually move: the widest shape D changes the mouth sprite by >= ``mouthRange``
    (mean colour change over the sprite vs. rest X) and at least 4 of A-H change by >= a third of that.
    Catches warped or mis-assigned mouths that barely open."""
    v = json.loads((view / "view.json").read_text())
    vis = v.get("visemes", {})
    if "X" not in vis or not (view / vis["X"]["url"]).exists():
        return {"pass": True, "value": None, "limit": LIMITS["mouthRange"], "note": "no sprites"}

    def rgba(name: str) -> np.ndarray:
        return np.asarray(Image.open(view / vis[name]["url"]).convert("RGBA")).astype(np.float32)

    x = rgba("X")
    diffs: dict[str, float] = {}
    for k in "ABCDEFGH":
        if k in vis and (view / vis[k]["url"]).exists():
            y = rgba(k)
            if y.shape != x.shape:
                continue
            core = np.maximum(x[..., 3], y[..., 3]) > 128
            d = np.abs(x[..., :3] * x[..., 3:] / 255 - y[..., :3] * y[..., 3:] / 255).mean(axis=2)
            diffs[k] = round(float(d[core].mean()) if core.any() else 0.0, 1)
    lim = LIMITS["mouthRange"]
    moving = sum(d >= lim / 3 for d in diffs.values())
    ok = diffs.get("D", 0.0) >= lim and moving >= 4
    return {"pass": ok, "value": diffs.get("D", 0.0), "limit": lim, "moving": moving, "diffs": diffs}


def alpha_edges(view: Path) -> dict[str, Any]:
    """Mouth sprites and eye layers must fade to 0 before their bounding box (no rectangles)."""
    v = json.loads((view / "view.json").read_text())
    bad = []
    items = [(n, m) for n, m in v["visemes"].items()] + [(l["name"], l) for l in v["layers"] if l["name"] in ("eyes_closed", "eyes_half")]
    for name, m in items:
        p = view / m["url"]
        if not p.exists():
            continue
        a = np.asarray(Image.open(p).convert("RGBA").getchannel("A")).astype(int)
        border = max(a[0].max(), a[-1].max(), a[:, 0].max(), a[:, -1].max())
        if border > 200 and a.shape[0] > 4:
            bad.append(name)
    return {"pass": not bad, "value": len(bad), "limit": 0, "bad": bad}


def resolution(boxes: dict[str, list[int]]) -> dict[str, Any]:
    w = boxes["face"][2] - boxes["face"][0]
    return {"pass": w >= LIMITS["faceWidthPx"], "value": int(w), "limit": LIMITS["faceWidthPx"]}


def run(skin: Path) -> dict[str, Any]:
    rig = json.loads((skin / "rig.json").read_text())
    front = json.loads((skin / "rig_front.json").read_text()) if (skin / "rig_front.json").exists() else None
    left = skin / "views" / "left"
    gates = {
        "skin": skin_delta_e(left),
        "mouthless": residual_mouth(left, rig["boxes"]["mouth"]),
        "front": front_symmetry(front["boxes"] if front else None),
        "landmarks": landmark_alignment(rig["boxes"], front["boxes"] if front else None),
        "edges": alpha_edges(left),
        "mouths": mouth_range(left),
        "resolution": resolution(rig["boxes"]),
    }
    if front and (skin / "views" / "front" / "view.json").exists():
        fs = skin_delta_e(skin / "views" / "front")
        gates["skin"] = fs if fs["value"] > gates["skin"]["value"] else gates["skin"]
        fm = mouth_range(skin / "views" / "front")
        if not fm["pass"]:
            gates["mouths"] = fm | {"view": "front"}
        fe = alpha_edges(skin / "views" / "front")  # rectangles on the front sprites fail the same gate
        if not fe["pass"]:
            gates["edges"] = fe | {"view": "front"}
    return gates


def score(gates: dict[str, Any]) -> int:
    return int(round(sum(WEIGHTS[k] for k, g in gates.items() if g["pass"])))


def review_sheet(skin: Path, gates: dict[str, Any], total: int, cost: float, dest: Path) -> Path:
    """Assembled views (closed and open mouth, eyes closed), the 9 mouths and the gate table."""
    rows = []
    for vname in ("left", "front"):
        d = skin / "views" / vname
        if not (d / "view.json").exists():
            continue
        v = json.loads((d / "view.json").read_text())
        base = Image.new("RGBA", (v["canvas"]["w"], v["canvas"]["h"]), (60, 64, 72, 255))
        layers = sorted(v["layers"], key=lambda l: l["z"])
        def comp(vis: str, closed: bool) -> Image.Image:
            im = base.copy()
            for l in layers:
                if l["name"] in ("eye_socket",) or (l["name"] == "eyes_closed") != closed and l["name"] in ("eyes", "eyes_closed") \
                        or l["name"] == "eyes_half" or (closed and l["name"] in ("eye_white", "iris")):
                    continue
                im.alpha_composite(Image.open(d / l["url"]).convert("RGBA"), (l["x"], l["y"]))
            m = v["visemes"][vis]
            im.alpha_composite(Image.open(d / m["url"]).convert("RGBA"), (m["x"], m["y"]))
            return im
        fb = json.loads((skin / ("rig.json" if vname == "left" else "rig_front.json")).read_text())["boxes"]["face"]
        fw = fb[2] - fb[0]
        box = (max(0, fb[0] - fw // 3), max(0, fb[1] - fw // 4), fb[2] + fw // 3, fb[3] + fw // 3)
        tiles = [comp("X", False).crop(box), comp("D", False).crop(box), comp("X", True).crop(box)]
        tiles += [comp(k, False).crop((v["visemes"]["X"]["x"] - 10, v["visemes"]["X"]["y"] - 10,
                                       v["visemes"]["X"]["x"] + Image.open(d / v["visemes"]["X"]["url"]).width + 10,
                                       v["visemes"]["X"]["y"] + Image.open(d / v["visemes"]["X"]["url"]).height + 10)) for k in "XABCDEFGH"]
        row = Image.new("RGB", (260 * 3 + 120 * 9, 300), (30, 30, 34))
        for i, t in enumerate(tiles):
            t = t.convert("RGB"); t.thumbnail((250, 290) if i < 3 else (116, 116))
            row.paste(t, (i * 260, 5) if i < 3 else (780 + (i - 3) * 120, 90))
        ImageDraw.Draw(row).text((4, 284), f"{vname}: closed / open D / eyes closed | mouths X A B C D E F G H", fill="white")
        rows.append(row)
    table = Image.new("RGB", (1860, 40 + 22 * len(gates)), (20, 20, 24))
    dr = ImageDraw.Draw(table)
    dr.text((8, 8), f"QA score {total}/100   cost US${cost:.3f}", fill="white")
    for i, (k, g) in enumerate(gates.items()):
        dr.text((8, 32 + 22 * i), f"{'PASS' if g['pass'] else 'FAIL'}  {k}: {g['value']} (limit {g['limit']}) {g.get('note', '')}",
                fill=(120, 220, 120) if g["pass"] else (240, 110, 110))
    sheet = Image.new("RGB", (1860, sum(r.height for r in rows) + table.height), (0, 0, 0))
    y = 0
    for r in rows + [table]:
        sheet.paste(r, (0, y)); y += r.height
    sheet.save(dest)
    return dest


def finalize(skin: Path, cost: float, attempts: dict[str, int]) -> dict[str, Any]:
    gates = run(skin)
    total = score(gates)
    report = {"score": total, "gates": gates, "costUsd": round(cost, 4), "attempts": attempts}
    (skin / "qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    review_sheet(skin, gates, total, cost, skin / "qa_sheet.png")
    return report
