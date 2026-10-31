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
WEIGHTS = {"skin": 15, "mouthless": 10, "doubleMouth": 20, "mouthOpen": 10, "mouths": 10, "front": 10, "landmarks": 10,
           "edges": 10, "resolution": 5}


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


def skin_of(rgb: np.ndarray) -> np.ndarray:
    """Local skin colour: median of the brighter half of the pixels (lips, lines and cavity are darker)."""
    px = rgb.reshape(-1, 3).astype(np.float32)
    lum = px.mean(axis=1)
    return np.median(px[lum >= np.median(lum)], axis=0)


def ink_mask(rgb: np.ndarray, skin: np.ndarray) -> np.ndarray:
    """Mouth ink relative to the local skin: lip red, dark-red cavity, or a dark warm lip line."""
    c = rgb.astype(np.float32)
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    lum = c.mean(axis=2)
    srg, slum = float(skin[0] - skin[1]), float(skin.mean())
    lips = (r - g > srg + 30) & (r > b + 25)
    cavity = (lum < 70) & (r > g + 30) & (r - b > 40)
    line = (lum < 0.55 * slum) & (r > b + 10) & (r - g > srg - 5)
    return lips | cavity | line


def _mouth_region(mouth: list[int] | tuple[int, ...], shape: tuple[int, ...]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = mouth
    w, h = x1 - x0, y1 - y0
    return (max(0, x0 - w // 4), max(0, y0 - h // 2), min(shape[1], x1 + w // 4), min(shape[0], y1 + int(h * 0.8)))


def ink_share(rgb: np.ndarray, ref: np.ndarray, mouth: list[int] | tuple[int, ...]) -> float:
    """Share of mouth-ink pixels left around the mouth of ``rgb`` (a mouthless edit); skin from ``ref``'s
    surroundings so a smudge that is merely darker than skin still counts."""
    x0, y0, x1, y1 = _mouth_region(mouth, rgb.shape)
    patch = rgb[y0:y1, x0:x1]
    if patch.size == 0:
        return 0.0
    import cv2  # noqa: PLC0415
    ink = ink_mask(patch, skin_of(ref[y0:y1, x0:x1])).astype(np.uint8)
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return round(float(ink.mean()), 3)


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


def _stacked(ink: np.ndarray, mw: int, mh: int, rgb: np.ndarray | None = None) -> bool:
    """Two wide mouth-ink blobs one above the other with skin between them: a second mouth
    (teeth or tongue between the lips of an open mouth are not skin)."""
    import cv2  # noqa: PLC0415
    n, _lab, st, _c = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    big = [st[i] for i in range(1, n) if st[i][4] >= max(10, 0.03 * mw * mh) and st[i][2] >= 0.3 * mw]
    for i, a in enumerate(big):
        for b in big[i + 1:]:
            top, low = (a, b) if a[1] < b[1] else (b, a)
            overlap = min(top[0] + top[2], low[0] + low[2]) - max(top[0], low[0])
            gap = low[1] - (top[1] + top[3])
            if overlap >= 0.5 * min(top[2], low[2]) and gap >= max(3, 0.12 * mh):
                if rgb is None:
                    return True
                gx0, gx1 = max(top[0], low[0]), min(top[0] + top[2], low[0] + low[2])
                g = rgb[top[1] + top[3]:low[1], gx0:gx1].reshape(-1, 3).astype(int)
                sk = skin_of(rgb.reshape(-1, 3))
                if len(g) and (np.abs(g - sk).max(axis=1) < 30).mean() >= 0.7:
                    return True
    return False


def double_mouth(view: Path) -> dict[str, Any]:
    """No second mouth anywhere: (1) the mouthless head carries no lip/mouth ink around the mouth;
    (2) every composed viseme (head + sprite) shows one mouth, no ink outside the sprite and no
    stacked lip lines inside it; (3) the sheet cell had no mouth ink outside its cut mask."""
    import cv2  # noqa: PLC0415
    v = json.loads((view / "view.json").read_text())
    mq = v.get("mouthQA")
    if not mq:  # rigs built before the mouthless head: boxes from the rig file
        rj = view.parent.parent / ("rig_front.json" if view.name == "front" else "rig.json")
        if not rj.exists():
            return {"pass": False, "value": None, "limit": 0, "note": "no mouth box"}
        r = json.loads(rj.read_text())
        mq = {"mouth": r["boxes"]["mouth"], "head": "old rig"}
    head = next(l for l in v["layers"] if l["name"] == "head")
    canvas = Image.new("RGBA", (v["canvas"]["w"], v["canvas"]["h"]), (0, 0, 0, 0))
    canvas.alpha_composite(Image.open(view / head["url"]).convert("RGBA"), (head["x"], head["y"]))
    mx0, my0, mx1, my1 = mq["mouth"]
    mw, mh = mx1 - mx0, my1 - my0
    rx0, ry0, rx1, ry1 = _mouth_region(mq["mouth"], (v["canvas"]["h"], v["canvas"]["w"]))
    base = np.asarray(canvas)[ry0:ry1, rx0:rx1]
    skin = skin_of(base[..., :3][base[..., 3] > 128]) if (base[..., 3] > 128).any() else np.array([220, 180, 160])
    k = np.ones((2, 2), np.uint8)

    yy, xx = np.mgrid[ry0:ry1, rx0:rx1]
    # where the real lips are: the rest mouth X sprite (detector boxes are loose and reach the nose / jaw);
    # a ghost mouth sits there, the nose tip above and the jaw line / collar below are not mouth ink
    zx0, zy0, zx1, zy1 = mx0, my0, mx1, my1
    xs = v["visemes"].get("X")
    if xs and (view / xs["url"]).exists():
        bb = Image.open(view / xs["url"]).convert("RGBA").getchannel("A").point(lambda q: 255 if q > 128 else 0).getbbox()
        if bb:
            zx0, zy0, zx1, zy1 = xs["x"] + bb[0], xs["y"] + bb[1], xs["x"] + bb[2], xs["y"] + bb[3]
    zw, zh = max(4, zx1 - zx0), max(4, zy1 - zy0)
    zone = ((xx - (zx0 + zx1) / 2) / (zw * 0.65)) ** 2 + ((yy - (zy0 + zy1) / 2) / (zh * 0.8 + 4)) ** 2 <= 1

    def ink_of(rgba: np.ndarray) -> np.ndarray:
        m = ink_mask(rgba[..., :3], skin) & (rgba[..., 3] > 128) & zone
        return cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, k).astype(bool)

    min_px = max(8, int(0.01 * mw * mh))
    bad: dict[str, str] = {}
    head_ink = int(ink_of(base).sum())
    if head_ink >= min_px:
        bad["head"] = f"{head_ink}px ink on mouthless head"
    for name, m in v["visemes"].items():
        spr = Image.open(view / m["url"]).convert("RGBA")
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.alpha_composite(spr, (m["x"], m["y"]))
        comp = canvas.copy()
        comp.alpha_composite(layer)
        c = np.asarray(comp)[ry0:ry1, rx0:rx1]
        a = np.asarray(layer)[ry0:ry1, rx0:rx1, 3]
        ink = ink_of(c)
        outside = int((ink & (a < 40)).sum())
        if outside >= min_px:
            bad[name] = f"{outside}px ink outside sprite"
        elif _stacked(ink & (a >= 40), mw, mh, c[..., :3]):
            bad[name] = "stacked lip lines"
        elif mq.get("cellGhost", {}).get(name, 0) > 0.05:
            bad[name] = f"cell ghost {mq['cellGhost'][name]}"
    return {"pass": not bad, "value": len(bad), "limit": 0, "bad": bad, "head": mq.get("head")}


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


# How far each viseme may open: drawn mouth height relative to the rest mouth X (any species / style),
# and never past OPEN_ABS of the face height. Natural speech, never a scream; A (MBP) stays near closed.
OPEN_REL = {"X": 1.0, "A": 1.15, "B": 1.3, "G": 1.35, "H": 1.4, "C": 1.45, "F": 1.5, "E": 1.55, "D": 1.7}
OPEN_ABS = 0.2
# a mouth hidden under a beard or mustache rests tiny; below this share of the face any viseme is fine
OPEN_FLOOR = 0.07
OPEN_SLACK = 0.01


def open_height(sprite: np.ndarray, head: np.ndarray) -> int:
    """Drawn mouth height in px: sprite pixels (alpha > 128) that differ from the mouthless head under them."""
    a = sprite[..., 3] > 128
    d = np.abs(sprite[..., :3].astype(np.int16) - head[..., :3].astype(np.int16)).max(axis=2) > 28
    return int(_mouth_blob(a & d)[3])


def _mouth_blob(m: np.ndarray) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the mouth in a changed-pixels mask: the largest blob plus sizeable blobs
    centred over it (lips split by teeth); stray jaw / outline strokes at the sides are ignored."""
    import cv2  # noqa: PLC0415
    n, _lab, st, cen = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    if n <= 1:
        return 0, 0, 0, 0
    big = 1 + int(np.argmax(st[1:, 4]))
    bx, by, bw, bh, ba = st[big]
    x0, y0, x1, y1 = bx, by, bx + bw, by + bh
    for i in range(1, n):
        if i != big and st[i][4] >= 0.15 * ba and bx + 0.15 * bw <= cen[i][0] <= bx + 0.85 * bw:
            x0, y0 = min(x0, st[i][0]), min(y0, st[i][1])
            x1, y1 = max(x1, st[i][0] + st[i][2]), max(y1, st[i][1] + st[i][3])
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


def mouth_open(view: Path, face: list[int]) -> dict[str, Any]:
    """No viseme opens past its natural share of the face height (OPEN_CAP)."""
    v = json.loads((view / "view.json").read_text())
    head = next(l for l in v["layers"] if l["name"] == "head")
    canvas = Image.new("RGBA", (v["canvas"]["w"], v["canvas"]["h"]), (0, 0, 0, 0))
    canvas.alpha_composite(Image.open(view / head["url"]).convert("RGBA"), (head["x"], head["y"]))
    fh = max(1, face[3] - face[1])
    hs = {}
    for name, m in v["visemes"].items():
        spr = np.asarray(Image.open(view / m["url"]).convert("RGBA"))
        h, w = spr.shape[:2]
        hs[name] = open_height(spr, np.asarray(canvas.crop((m["x"], m["y"], m["x"] + w, m["y"] + h))))
    rest = max(1, hs.get("X", 0))
    ratios = {k: round(h / rest, 2) for k, h in hs.items()}
    face_share = {k: round(h / fh, 3) for k, h in hs.items()}
    bad = {k: ratios[k] for k in hs if (ratios[k] > OPEN_REL.get(k, 1.7) + 0.05 and face_share[k] > OPEN_FLOOR) or face_share[k] > OPEN_ABS + OPEN_SLACK}
    return {"pass": not bad, "value": max(ratios.values(), default=0), "limit": OPEN_REL, "ratios": ratios,
            "faceShare": face_share, "bad": bad}


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
        "doubleMouth": double_mouth(left),
        "mouthOpen": mouth_open(left, rig["boxes"]["face"]),
        "resolution": resolution(rig["boxes"]),
    }
    if front and (skin / "views" / "front" / "view.json").exists():
        fs = skin_delta_e(skin / "views" / "front")
        gates["skin"] = fs if fs["value"] > gates["skin"]["value"] else gates["skin"]
        fm = mouth_range(skin / "views" / "front")
        if not fm["pass"]:
            gates["mouths"] = fm | {"view": "front"}
        fd = double_mouth(skin / "views" / "front")
        if not fd["pass"] or not gates["doubleMouth"]["pass"]:
            gates["doubleMouth"] = {"pass": False, "value": (gates["doubleMouth"]["value"] or 0) + (fd["value"] or 0),
                                    "limit": 0, "bad": {"left": gates["doubleMouth"].get("bad"), "front": fd.get("bad")}}
        fo = mouth_open(skin / "views" / "front", front["boxes"]["face"])
        if not fo["pass"]:
            gates["mouthOpen"] = fo | {"view": "front"}
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
