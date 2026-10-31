"""Animation-ready parts drawn by design: one Gemini sheet per character with the 9 Rhubarb
mouths and 3 eye states in the master's exact style and skin tone, grid-sliced and registered
back onto the master (like Aiwake's ``NO mouth`` / parts prompts, but one image for all parts so
the whole set costs a single $0.039 call and every cell shares one palette).

Layout (4 x 3, magenta background, no text):
    row 1: X A B C      row 2: D E F G      row 3: H eyes-open eyes-half eyes-closed
Mouth cells show the lower face (nose tip to chin), eye cells the eye band, all at the master's
angle. Cells are registered to the master by multi-scale template matching against the closed
mouth (cell X) and the open eyes; the other cells reuse that transform, so a cell only has to
differ from its neighbour where the mouth or lids move.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

Box = tuple[int, int, int, int]
VISEMES = "XABCDEFGH"
EYES = ("eyes_open", "eyes_half", "eyes_closed")
ORDER = list(VISEMES) + list(EYES)
COLS, ROWS = 4, 3
CELL_MIN_NCC = 0.7  # a sheet cell is used only if it registers this well; else that part falls back
MIN_GOOD_MOUTHS = 5

SHEET_PROMPT = (
    "Using this character as the exact reference (same art style, line weight, colours, skin tone, facial hair "
    "and the same three-quarter angle), draw an animation parts sheet: a 4 by 3 grid of 12 equal cells on a "
    "solid flat pure magenta #FF00FF background, cells separated by magenta gaps, no text, no labels, no borders. "
    "Cells 1-9 (left to right, top to bottom) are close-ups of the SAME lower face, from the nose tip to the chin, "
    "identical framing and scale in every cell, only the mouth changes: "
    "1 relaxed closed mouth; 2 closed lips pressed (M,B,P); 3 slightly open, teeth together (K,S,T); "
    "4 open (EH, AE); 5 wide open (AH) showing teeth and tongue; 6 rounded open (AO); 7 small round pucker (OO, W); "
    "8 upper teeth on lower lip (F,V); 9 open with tongue up behind the teeth (L). "
    "Any moustache or beard stays identical in all nine cells. "
    "Cells 10-12 are close-ups of the SAME eye band (both eyes and eyebrows, identical framing): "
    "10 eyes open as in the reference; 11 eyes half closed; 12 eyes fully closed, skin-coloured upper lids down with a fine thin lash line, no thick black stroke, eyebrows unchanged. "
    "Glasses, earrings, hats, hair strands, fur, scales and every accessory stay exactly as in the "
    "reference in ALL 12 cells (if glasses are worn, the eyes close behind the same glasses). Every cell is a "
    "crop of the same view as the reference, never a different angle. Flat clean cel shading, crisp outlines, nothing outside the cells."
)


def _bg(rgb: np.ndarray, ref: np.ndarray | None = None, lines: bool = True) -> np.ndarray:
    """Background (magenta as asked, or whatever flat colour Gemini used: taken from the border)
    plus thin separator-line pixels (dark or magenta)."""
    border = np.concatenate([rgb[:3].reshape(-1, 3), rgb[-3:].reshape(-1, 3), rgb[:, :3].reshape(-1, 3), rgb[:, -3:].reshape(-1, 3)])
    ref = np.median(border, axis=0) if ref is None else ref
    flat = np.abs(rgb - ref).sum(axis=-1) < 60
    mag = (rgb[..., 0] > 160) & (rgb[..., 2] > 120) & (rgb[..., 1] < 120) & (rgb[..., 0] - rgb[..., 1] > 70)
    return (flat | mag | (rgb.max(axis=-1) < 60)) if lines else (flat | mag)


def _runs(sep: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    """Content spans between separator columns/rows (``sep`` True = separator)."""
    spans, start = [], None
    for i, v in enumerate(list(sep) + [True]):
        if not v and start is None:
            start = i
        elif v and start is not None:
            if i - start >= min_len:
                spans.append((start, i))
            start = None
    return spans


def slice_grid(sheet: Image.Image) -> list[Image.Image]:
    """Cells in reading order. Gemini does not always keep the asked 4x3 layout (4x4 seen), so the
    grid is detected from fully-background rows/columns; equal 4x3 split only if that fails."""
    rgb = np.asarray(sheet.convert("RGB")).astype(int)
    bg = _bg(rgb)
    W, H = sheet.size
    cols = _runs(bg.mean(axis=0) > 0.97, W // 10)
    rows = _runs(bg.mean(axis=1) > 0.97, H // 10)
    if len(cols) * len(rows) < COLS * ROWS:
        cols = [(int(c * W / COLS), int((c + 1) * W / COLS)) for c in range(COLS)]
        rows = [(int(r * H / ROWS), int((r + 1) * H / ROWS)) for r in range(ROWS)]
    cells: list[Image.Image] = []
    for y0, y1 in rows:
        for x0, x1 in cols:
            sub = ~bg[y0:y1, x0:x1]
            ys, xs = np.nonzero(sub)
            box = (x0, y0, x1, y1)
            if len(xs):  # trim the background margin so registration sees only the drawing
                box = (x0 + xs.min(), y0 + ys.min(), x0 + xs.max() + 1, y0 + ys.max() + 1)
            cell = sheet.crop(box)
            cell.info["bg"] = _sheet_bg(rgb)
            cells.append(cell)
    return cells


def _sheet_bg(rgb: np.ndarray) -> np.ndarray:
    border = np.concatenate([rgb[:3].reshape(-1, 3), rgb[-3:].reshape(-1, 3), rgb[:, :3].reshape(-1, 3), rgb[:, -3:].reshape(-1, 3)])
    return np.median(border, axis=0)


def _demagenta(cell: Image.Image) -> Image.Image:
    rgb = np.asarray(cell.convert("RGB")).astype(int)
    bg = _bg(rgb, cell.info.get("bg", np.array([255, 0, 255])), lines=False)
    out = cell.convert("RGBA")
    a = np.where(bg, 0, 255).astype(np.uint8)
    out.putalpha(Image.fromarray(a))
    return out


def register(cell: Image.Image, base: Image.Image, region: Box, scales: np.ndarray | None = None) -> tuple[float, int, int, float]:
    """Best ``(scale, x, y, ncc)`` placing ``cell`` over ``base`` around ``region`` (grey NCC)."""
    import cv2  # noqa: PLC0415

    x0, y0, x1, y1 = region
    rw, rh = x1 - x0, y1 - y0
    pad = int(max(rw, rh) * 0.6)
    sx0, sy0 = max(0, x0 - pad), max(0, y0 - pad)
    search = np.asarray(base.convert("L").crop((sx0, sy0, min(base.width, x1 + pad), min(base.height, y1 + pad)))).astype(np.float32)
    g = np.asarray(cell.convert("L")).astype(np.float32)
    best = (1.0, x0, y0, -1.0)
    fit = min(rw / cell.width, rh / cell.height)
    for s in (scales if scales is not None else np.linspace(0.6, 1.8, 25)) * fit:
        w, h = max(8, int(cell.width * s)), max(8, int(cell.height * s))
        if w >= search.shape[1] or h >= search.shape[0]:
            continue
        t = cv2.resize(g, (w, h), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(search, t, cv2.TM_CCOEFF_NORMED)
        _mn, mx, _l, loc = cv2.minMaxLoc(res)
        if mx > best[3]:
            best = (float(s), sx0 + loc[0], sy0 + loc[1], float(mx))
    return best


def refine(cell: Image.Image, base: Image.Image, region: Box) -> tuple[float, int, int, float]:
    """Coarse scale sweep, then a fine one around the best scale."""
    x0, y0, x1, y1 = region
    fit = min((x1 - x0) / cell.width, (y1 - y0) / cell.height)
    s0 = register(cell, base, region)[0] / fit
    return register(cell, base, region, np.linspace(s0 - 0.06, s0 + 0.06, 13))


def place(cell: Image.Image, size: tuple[int, int], reg: tuple[float, int, int, float]) -> Image.Image:
    """``cell`` scaled and pasted at ``reg`` on a transparent canvas of ``size``."""
    s, x, y, _ = reg
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    c = _demagenta(cell).resize((max(1, int(cell.width * s)), max(1, int(cell.height * s))), Image.LANCZOS)
    out.paste(c, (x, y), c)
    return out


def align(cell: Image.Image, base: Image.Image, region: Box, face: Box | None = None) -> tuple[Image.Image, float]:
    """Per-cell alignment: scale/translate NCC seed, then an affine ECC refine (cells are drawn
    separately, so each has its own small tilt / shift). Returns the warped RGBA cell in master
    coordinates and the NCC of the grey drawing over ``region`` after warping."""
    import cv2  # noqa: PLC0415

    # Gemini often draws whole heads instead of the asked close-ups: also seed on the face, and
    # keep whichever seed ends best after the affine refine.
    seeds = [refine(cell, base, region)]
    if face is not None:
        fx0, fy0, fx1, fy1 = face
        p = int((fy1 - fy0) * 0.3)
        seeds.append(refine(cell, base, (max(0, fx0 - p), max(0, fy0 - p), min(base.width, fx1 + p), min(base.height, fy1 + p))))
    return max((_affine(cell, base, region, s) for s in seeds), key=lambda r: r[1])


def _affine(cell: Image.Image, base: Image.Image, region: Box, seed: tuple[float, int, int, float]) -> tuple[Image.Image, float]:
    import cv2  # noqa: PLC0415

    placed = place(cell, base.size, seed)
    x0, y0, x1, y1 = region
    g_b = np.asarray(base.convert("L")).astype(np.float32)[y0:y1, x0:x1]
    arr = np.asarray(placed).astype(np.float32)
    g_c = (arr[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32))[y0:y1, x0:x1]
    mask = (arr[y0:y1, x0:x1, 3] > 128).astype(np.uint8)
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        _, warp = cv2.findTransformECC(g_b, g_c, warp, cv2.MOTION_AFFINE,
                                       (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5), mask, 5)
        if np.abs(warp[:, :2] - np.eye(2)).max() > 0.25 or np.abs(warp[:, 2]).max() > 0.15 * max(x1 - x0, y1 - y0):  # implausible: keep the seed
            warp = np.eye(2, 3, dtype=np.float32)
    except cv2.error:
        pass
    full = np.eye(3, dtype=np.float32)
    full[:2] = warp
    shift = np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], np.float32)
    m = (shift @ full @ np.linalg.inv(shift))[:2]
    out = cv2.warpAffine(np.asarray(placed), m, base.size, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    o = out.astype(np.float32)
    g_o = (o[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32))[y0:y1, x0:x1]
    k = o[y0:y1, x0:x1, 3] > 128
    ncc = -1.0
    if k.sum() > 50:
        a, b = g_o[k] - g_o[k].mean(), g_b[k] - g_b[k].mean()
        ncc = float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))
    return Image.fromarray(out), ncc


def load(sheet_path: Path, base: Image.Image, boxes: dict[str, Box]) -> dict[str, Any] | None:
    """Registered parts in master coordinates: ``{"mouths": {v: img}, "eyes": {state: img}, "ncc": {...}}``,
    or ``None`` when the sheet cannot be matched (caller falls back to edits / warps)."""
    with Image.open(sheet_path) as im:
        return load_image(im.convert("RGB"), base, boxes)


OPEN_RANK = "BHCED"  # Rhubarb shapes from least to most open (F pucker, G F/V, A pressed keep their cells)


def opening(img: Image.Image, ref: Image.Image, mouth: Box) -> float:
    """How far a mouth cell departs from the rest mouth X inside the (grown) mouth box: mean colour change."""
    x0, y0, x1, y1 = mouth
    w, h = x1 - x0, y1 - y0
    box = (max(0, x0 - w // 4), max(0, y0 - h // 2), x1 + w // 4, y1 + h)
    a = np.asarray(img.convert("RGB").crop(box)).astype(np.float32)
    b = np.asarray(ref.convert("RGB").crop(box)).astype(np.float32)
    return float(np.abs(a - b).mean())


def rank_by_opening(mouths: dict[str, Image.Image], mouth: Box, good: list[str]) -> dict[str, str]:
    """Gemini often draws the right shapes in the wrong cells. Re-assign the open shapes among the good
    cells by measured opening (least -> most: B, H, C, E, D) so D is always the widest. Returns new->old."""
    if "X" not in good:
        return {}
    keys = [k for k in OPEN_RANK if k in good]
    if len(keys) < 3:
        return {}
    score = {k: opening(mouths[k], mouths["X"], mouth) for k in keys}
    by_open = sorted(keys, key=score.get)
    target = [k for k in OPEN_RANK if k in keys]
    old = {k: mouths[k] for k in keys}
    mapping = {}
    for new, src in zip(target, by_open, strict=True):
        mouths[new] = old[src]
        mapping[new] = src
    return {k: v for k, v in mapping.items() if k != v}


def load_image(sheet: Image.Image, base: Image.Image, boxes: dict[str, Box]) -> dict[str, Any] | None:
    cells = slice_grid(sheet)
    if len(cells) < COLS * ROWS:
        return None
    named = dict(zip(ORDER, cells[: COLS * ROWS], strict=True))  # extra cells (4x4 sheets) are ignored
    face, mouth = boxes["face"], boxes["mouth"]
    fh = face[3] - face[1]
    lower = (face[0], max(face[1], mouth[1] - fh // 4), face[2], min(base.height, face[3] + fh // 10))
    le, re_ = boxes["left_eye"], boxes["right_eye"]
    band = (min(le[0], re_[0]) - fh // 10, min(le[1], re_[1]) - fh // 8, max(le[2], re_[2]) + fh // 10, max(le[3], re_[3]) + fh // 20)
    band = (max(0, band[0]), max(0, band[1]), min(base.width, band[2]), min(base.height, band[3]))
    mouths, eyes, m_ncc, e_ncc = {}, {}, [], []
    for v in VISEMES:
        mouths[v], n = align(named[v], base, lower, face)
        m_ncc.append(n)
    for e in EYES:
        eyes[e], n = align(named[e], base, band, face)
        e_ncc.append(n)
    good = [k for k, n in zip(ORDER, m_ncc + e_ncc, strict=True) if n >= CELL_MIN_NCC]
    relabel = rank_by_opening(mouths, mouth, good)
    return {
        "mouths": mouths,
        "relabel": relabel,
        "eyes": eyes,
        "ncc": {"mouth": round(float(np.median(m_ncc)), 3), "eyes": round(float(np.median(e_ncc)), 3)},
        "good": good,
        "cellNcc": {k: round(n, 3) for k, n in zip(ORDER, m_ncc + e_ncc, strict=True)},
    }
