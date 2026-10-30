"""Cut-out rig (contract v2) from Gemini art.

One request makes one character:

1. ``create_puppet.generate_character_image`` (Aiwake factory, guarded Gemini
   Flash image) draws a 3/4 bust facing left on magenta, mouth closed.
2. The same call, with that image as reference, redraws only the mouth twice:
   wide open ("AH") and rounded ("OO"). Budget errors skip these shots and
   the visemes are warped from the closed mouth instead.
3. BiRefNet (rembg ``birefnet-general``, the Rigly rigger's matte) cuts
   every shot, then the magenta spill is removed. A Gemini text call returns
   the face, eye and mouth boxes.
4. Layers: ``body`` and ``head`` (split under the chin with an overlap),
   ``eyes`` and ``eyes_closed``, and one mouth sprite per Rhubarb viseme
   ``X A B C D E F G H``. The ``right`` view is the mirror of ``left``;
   ``front`` reuses the left art (status says so).

Everything is written under ``<data>/assets/puppets/<id>/``; nothing here
touches git. ``build_mock_rig`` draws the same layer set with PIL, no network.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

_LOG = logging.getLogger("animind_core.rig")

VISEMES = ("X", "A", "B", "C", "D", "E", "F", "G", "H")
DETECT_MODEL = "gemini-2.5-flash"

STYLE_PROMPT = (
    "{subject}. Expressive 2D cartoon character design, clean flat cel shading, bold dark outlines. "
    "Medium bust shot (head and shoulders), three-quarter view, head turned 25 degrees toward the left "
    "of the image, looking left. Mouth closed, lips together, eyes open, friendly neutral expression. "
    "Head in the upper half of the frame, shoulders reaching the bottom edge, nothing cropped at the top. "
    "Solid flat pure magenta #FF00FF background. No text, no props in front of the face."
)
EDIT_PROMPTS = {
    "open": (
        "Edit this image. Keep the character, pose, head position, framing, colors, outlines and the "
        "magenta background exactly identical. Change only the mouth: wide open as if saying 'AH', "
        "showing the upper teeth and tongue. Do not move the head."
    ),
    "round": (
        "Edit this image. Keep the character, pose, head position, framing, colors, outlines and the "
        "magenta background exactly identical. Change only the mouth: small rounded open 'O' shape, "
        "lips pushed forward as if saying 'OO'. Do not move the head."
    ),
}
DETECT_PROMPT = (
    "Detect, in this cartoon character image, the face (whole head without hair or ears), the left eye, "
    "the right eye and the mouth. Return JSON only: "
    '{"face": [ymin, xmin, ymax, xmax], "left_eye": [...], "right_eye": [...], "mouth": [...]} '
    "with coordinates normalized to 0-1000."
)

Box = tuple[int, int, int, int]  # x0, y0, x1, y1 in pixels


# --------------------------------------------------------------------- Gemini


def _gemini_key() -> str:
    return (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()


def detect_features(image_path: Path) -> dict[str, Box]:
    """Face, eye and mouth boxes from a guarded Gemini text call."""
    from google.genai import types  # noqa: PLC0415

    from production.cost.google_guardrail import guarded_generate_content, make_guarded_gemini_client  # noqa: PLC0415

    with Image.open(image_path) as im:
        w, h = im.size
    client = make_guarded_gemini_client(_gemini_key())
    response = guarded_generate_content(
        client,
        model=DETECT_MODEL,
        contents=[types.Part.from_bytes(data=image_path.read_bytes(), mime_type="image/png"), DETECT_PROMPT],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
        kind="text",
        source="animind_core.rig.detect",
    )
    raw = response.text or ""
    data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    out: dict[str, Box] = {}
    for key in ("face", "left_eye", "right_eye", "mouth"):
        y0, x0, y1, x1 = (float(v) for v in data[key])
        out[key] = (int(x0 * w / 1000), int(y0 * h / 1000), int(x1 * w / 1000), int(y1 * h / 1000))
    return out


def _png(src: Path, dest: Path, size: tuple[int, int] | None = None) -> Path:
    with Image.open(src) as im:
        rgb = im.convert("RGB")
        if size and rgb.size != size:
            rgb = rgb.resize(size, Image.LANCZOS)
        rgb.save(dest, format="PNG")
    return dest


def generate_shots(subject: str, work: Path, *, raw_prompt: str | None = None) -> dict[str, Any]:
    """Draw the closed-mouth master and the two mouth edits. Returns paths and notes."""
    from products.aiwake.animator.factory.create_puppet import generate_character_image  # noqa: PLC0415

    work.mkdir(parents=True, exist_ok=True)
    raw = work / "_gen_closed"
    generate_character_image(raw_prompt or STYLE_PROMPT.format(subject=subject), raw, aspect_ratio="4:5")
    closed = _png(raw, work / "source_closed.png")
    raw.unlink(missing_ok=True)
    size = Image.open(closed).size
    shots: dict[str, Path] = {"closed": closed}
    notes: dict[str, str] = {}
    for name, prompt in EDIT_PROMPTS.items():
        raw = work / f"_gen_{name}"
        try:
            generate_character_image(prompt, raw, aspect_ratio="4:5", reference_path=closed)
            shots[name] = _png(raw, work / f"source_{name}.png", size)
            notes[name] = "gemini"
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — budget stops included; visemes fall back to warps
            _LOG.warning("mouth shot %s skipped: %s", name, exc)
            notes[name] = f"warped (shot failed: {str(exc)[:160]})"
        finally:
            raw.unlink(missing_ok=True)
    return {"shots": shots, "notes": notes}


# ---------------------------------------------------------------- geometry


def _shift(reference: np.ndarray, moving: np.ndarray) -> tuple[float, float]:
    """Translation that aligns ``moving`` onto ``reference`` (phase correlation)."""
    import cv2  # noqa: PLC0415

    a = cv2.cvtColor(reference, cv2.COLOR_RGBA2GRAY).astype(np.float32)
    b = cv2.cvtColor(moving, cv2.COLOR_RGBA2GRAY).astype(np.float32)
    (dx, dy), _resp = cv2.phaseCorrelate(a, b)
    return float(dx), float(dy)


def _translate(img: Image.Image, dx: float, dy: float) -> Image.Image:
    return img.transform(img.size, Image.AFFINE, (1, 0, dx, 0, 1, dy), resample=Image.BICUBIC)


def _grow(box: Box, size: tuple[int, int], fx: float, fy_top: float, fy_bottom: float) -> Box:
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    return (
        max(0, int(x0 - bw * fx)),
        max(0, int(y0 - bh * fy_top)),
        min(size[0], int(x1 + bw * fx)),
        min(size[1], int(y1 + bh * fy_bottom)),
    )


def _union(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _feather_ellipse(w: int, h: int, feather: float = 0.22) -> Image.Image:
    """Opaque ellipse with a soft rim, as an L mask."""
    pad = int(min(w, h) * feather)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).ellipse((pad // 2, pad // 2, w - 1 - pad // 2, h - 1 - pad // 2), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(max(1, pad // 2)))


def _crop_layer(img: Image.Image) -> tuple[Image.Image, tuple[int, int]]:
    bbox = img.getchannel("A").getbbox() or (0, 0, 1, 1)
    return img.crop(bbox), (bbox[0], bbox[1])


# ---------------------------------------------------------------- sprites


def _mouth_sprite(base: Image.Image, shot: Image.Image | None, box: Box, *, sy: float = 1.0, sx: float = 1.0) -> Image.Image:
    """Mouth patch for ``box``: the base mouth, with ``shot`` (scaled about the
    mouth centre) blended in through a feathered ellipse."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    patch = base.crop(box)
    src = (shot or base).crop(box)
    if sx != 1.0 or sy != 1.0:
        nw, nh = max(1, int(w * sx)), max(1, int(h * sy))
        scaled = src.resize((nw, nh), Image.LANCZOS)
        src = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        src.paste(scaled, ((w - nw) // 2, int((h - nh) * 0.35)))
        filled = patch.copy()
        filled.alpha_composite(src)
        src = filled
    mask = _feather_ellipse(w, h)
    alpha = Image.fromarray(
        (np.asarray(mask, np.float32) / 255 * np.asarray(src.getchannel("A"), np.float32)).astype(np.uint8)
    )
    out = src.copy()
    out.putalpha(alpha)
    return out


def _skin_and_ink(base: Image.Image, face: Box) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    arr = np.asarray(base.crop(face))
    px = arr[arr[..., 3] > 200][..., :3]
    if not len(px):
        return (230, 190, 160), (35, 30, 30)
    lum = px.mean(axis=1)
    skin = tuple(int(v) for v in np.median(px[lum >= np.percentile(lum, 40)], axis=0))
    ink = tuple(int(v) for v in np.median(px[lum <= np.percentile(lum, 3)], axis=0))
    return skin, ink  # type: ignore[return-value]


def _eyes_closed(base: Image.Image, eyes: list[Box], skin: tuple[int, int, int], ink: tuple[int, int, int]) -> Image.Image:
    """Lids: skin over each eye plus a dark arc along the lash line."""
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    for x0, y0, x1, y1 in eyes:
        w, h = x1 - x0, y1 - y0
        lid = Image.new("RGBA", (w, h), skin + (255,))
        lid.putalpha(_feather_ellipse(w, h, 0.18))
        layer.alpha_composite(lid, (x0, y0))
        d = ImageDraw.Draw(layer)
        d.arc((x0 + w * 0.08, y0 + h * 0.15, x1 - w * 0.08, y1 - h * 0.15), 15, 165, fill=ink + (255,), width=max(3, h // 9))
    alpha = np.minimum(np.asarray(layer.getchannel("A")), np.asarray(base.getchannel("A")))
    layer.putalpha(Image.fromarray(alpha))
    return layer


def _split(base: Image.Image, cut: int, overlap: int) -> tuple[Image.Image, Image.Image]:
    """Head above ``cut`` (soft bottom edge), body below ``cut - overlap``."""
    h = base.size[1]
    y = np.arange(h, dtype=np.float32)[:, None]
    a = np.asarray(base.getchannel("A"), np.float32)
    head_w = np.clip((cut + overlap / 2 - y) / max(1, overlap / 2), 0, 1)
    body_w = (y >= cut - overlap).astype(np.float32)
    head, body = base.copy(), base.copy()
    head.putalpha(Image.fromarray((a * head_w).astype(np.uint8)))
    body.putalpha(Image.fromarray((a * body_w).astype(np.uint8)))
    return head, body


# ---------------------------------------------------------------- assembly


def _layer(name: str, img: Image.Image, xy: tuple[int, int], z: int, path: Path, url: Callable[[Path], str]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=True)
    return {"name": name, "url": url(path), "x": int(xy[0]), "y": int(xy[1]), "w": img.width, "h": img.height, "z": z}


def _despill(img: Image.Image, start: int = 30, span: int = 50) -> Image.Image:
    """Remove the magenta backdrop that shows through glass or thin gaps.

    Spill is ``min(R, B) - G``: high only for magenta-ish pixels (red ties,
    pink cheeks stay below ``start``). Those pixels fade out and lose the
    magenta cast.
    """
    a = np.asarray(img.convert("RGBA")).astype(np.int16)
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    spill = np.minimum(r, b) - g
    k = np.clip((spill - start) / span, 0.0, 1.0)
    al = (al * (1.0 - k)).astype(np.int16)
    cap = g + start
    a[..., 0] = np.where(spill > start, np.minimum(r, cap), r)
    a[..., 2] = np.where(spill > start, np.minimum(b, cap), b)
    a[..., 3] = al
    return Image.fromarray(a.clip(0, 255).astype(np.uint8))


def _write_view(parts: dict[str, Any], out: Path, url: Callable[[Path], str], mirror: bool) -> dict[str, Any]:
    W, H = parts["size"]

    def place(img: Image.Image, xy: tuple[int, int]) -> tuple[Image.Image, tuple[int, int]]:
        if not mirror:
            return img, xy
        return ImageOps.mirror(img), (W - xy[0] - img.width, xy[1])

    layers = []
    for z, name in enumerate(("body", "head", "eyes", "eyes_closed")):
        img, xy = place(*parts[name])
        layers.append(_layer(name, img, xy, z, out / f"{name}.png", url))
    visemes = {}
    for v in VISEMES:
        img, xy = place(*parts["visemes"][v])
        visemes[v] = _layer(f"mouth_{v}", img, xy, 10, out / "mouths" / f"{v}.png", url)
    view = {"canvas": {"w": W, "h": H}, "layers": layers, "visemes": visemes}
    # Same geometry with file names instead of URLs, for the server renderer.
    local = json.loads(json.dumps(view))
    for layer in local["layers"]:
        layer["url"] = f"{layer['name']}.png"
    for v, layer in local["visemes"].items():
        layer["url"] = f"mouths/{v}.png"
    (out / "view.json").write_text(json.dumps(local, indent=2), encoding="utf-8")
    return view


def load_view(skin: Path, view: str) -> dict[str, Any]:
    """Read ``views/<view>/view.json`` with absolute file paths in ``url``."""
    base = skin / "views" / ("left" if view == "front" else view)
    data = json.loads((base / "view.json").read_text(encoding="utf-8"))
    for layer in [*data["layers"], *data["visemes"].values()]:
        layer["url"] = str(base / layer["url"])
    return data


def assemble(skin: Path, shots: dict[str, Path], boxes: dict[str, Box], url: Callable[[Path], str], *, matte: Callable[[Image.Image], Image.Image]) -> dict[str, Any]:
    """Build the left view parts from mattes + boxes, write left/right views."""
    with Image.open(shots["closed"]) as im:
        base = _despill(matte(im).convert("RGBA"))
    size = base.size
    aligned: dict[str, Image.Image | None] = {"open": None, "round": None}
    for name in aligned:
        if name in shots:
            with Image.open(shots[name]) as im:
                cut = _despill(matte(im).convert("RGBA"))
            dx, dy = _shift(np.asarray(base), np.asarray(cut))
            aligned[name] = _translate(cut, dx, dy) if abs(dx) < 40 and abs(dy) < 40 else None
    face, mouth = boxes["face"], boxes["mouth"]
    mbox = _grow(mouth, size, 0.45, 0.45, 1.3)  # open jaws drop down
    mbox = (mbox[0], mbox[1], mbox[2], min(mbox[3], max(face[3] + 6, mouth[3] + 4)))
    mbox = _union(mbox, _grow(mouth, size, 0.3, 0.3, 0.6))
    op, rd = aligned["open"], aligned["round"]
    sprites = {
        "X": _mouth_sprite(base, None, mbox),
        "A": _mouth_sprite(base, None, mbox),
        "B": _mouth_sprite(base, op, mbox, sy=0.45) if op else _mouth_sprite(base, None, mbox, sy=1.06),
        "C": _mouth_sprite(base, op, mbox, sy=0.72) if op else _mouth_sprite(base, None, mbox, sy=1.12),
        "D": _mouth_sprite(base, op, mbox) if op else _mouth_sprite(base, None, mbox, sy=1.2),
        "E": _mouth_sprite(base, rd, mbox) if rd else _mouth_sprite(base, op, mbox, sx=0.8, sy=0.8),
        "F": _mouth_sprite(base, rd, mbox, sx=0.82, sy=0.85) if rd else _mouth_sprite(base, None, mbox, sx=0.85),
        "G": _mouth_sprite(base, op, mbox, sy=0.3) if op else _mouth_sprite(base, None, mbox),
        "H": _mouth_sprite(base, op, mbox, sy=0.6) if op else _mouth_sprite(base, None, mbox, sy=1.08),
    }
    skin_rgb, ink = _skin_and_ink(base, face)
    eyes_boxes = [_grow(boxes[k], size, 0.18, 0.25, 0.25) for k in ("left_eye", "right_eye")]
    eyes_open = Image.new("RGBA", size, (0, 0, 0, 0))
    for b in eyes_boxes:
        patch = base.crop(b)
        patch.putalpha(Image.fromarray(np.minimum(np.asarray(patch.getchannel("A")), np.asarray(_feather_ellipse(b[2] - b[0], b[3] - b[1], 0.18)))))
        eyes_open.alpha_composite(patch, (b[0], b[1]))
    eyes_closed = _eyes_closed(base, eyes_boxes, skin_rgb, ink)
    chin = max(face[3], mouth[3])
    head, body = _split(base, chin + int((face[3] - face[1]) * 0.04), max(16, (face[3] - face[1]) // 10))
    parts = {
        "size": size,
        "body": _crop_layer(body),
        "head": _crop_layer(head),
        "eyes": _crop_layer(eyes_open),
        "eyes_closed": _crop_layer(eyes_closed),
        "visemes": {v: (img, (mbox[0], mbox[1])) for v, img in sprites.items()},
    }
    base.save(skin / "character.png", format="PNG", optimize=True)
    left = _write_view(parts, skin / "views" / "left", url, mirror=False)
    right = _write_view(parts, skin / "views" / "right", url, mirror=True)
    (skin / "rig.json").write_text(json.dumps({"size": size, "boxes": boxes, "mouthBox": mbox}, indent=2), encoding="utf-8")
    return {"left": left, "right": right}


class BiRefNetMatte:
    """BiRefNet (rembg ``birefnet-general``) with the ONNX memory arena off.

    The engine's cached session keeps growing past 9 GB on CPU after a few
    1024px passes; with the arena off it stays near 8 GB and is freed by
    :meth:`close` once the character is built, so the API does not hold it.
    """

    def __init__(self) -> None:
        self._session: Any = None

    def __call__(self, image: Image.Image) -> Image.Image:
        from rembg import remove  # noqa: PLC0415

        if self._session is None:
            import onnxruntime as ort  # noqa: PLC0415
            from rembg.sessions import sessions_class  # noqa: PLC0415

            opts = ort.SessionOptions()
            opts.enable_cpu_mem_arena = False
            opts.enable_mem_pattern = False
            cls = next(c for c in sessions_class if c.name() == "birefnet-general")
            self._session = cls("birefnet-general", opts)
        out = remove(image.convert("RGB"), session=self._session, alpha_matting=False)
        return out.convert("RGBA")

    def close(self) -> None:
        import gc  # noqa: PLC0415

        self._session = None
        gc.collect()


def reset_budget() -> None:
    """Each character is its own budget unit (like one reel in the engine).

    The per-reel cap (``REEL_REEL_BUDGET_USD``, default $0.05) and the Google
    run cap still apply inside one character: with the defaults only the base
    shot fits and the mouth shots fall back to warps of the closed mouth.
    """
    from production.cost import spend_ledger  # noqa: PLC0415
    from production.cost.google_guardrail import reset_google_cost_tracker  # noqa: PLC0415

    spend_ledger.reset_reel()
    reset_google_cost_tracker()


def build_rig(subject: str, skin: Path, url: Callable[[Path], str], *, raw_prompt: str | None = None) -> dict[str, Any]:
    """Real path: Gemini shots -> BiRefNet mattes -> layered rig with 3 views."""
    reset_budget()
    gen = generate_shots(subject, skin, raw_prompt=raw_prompt)
    boxes = detect_features(gen["shots"]["closed"])
    matte = BiRefNetMatte()
    try:
        views = assemble(skin, gen["shots"], boxes, url, matte=matte)
    finally:
        matte.close()
    status = {"left": "ready", "right": "ready (mirror of left)", "front": "ready (left art; no frontal shot yet)"}
    return {"views": {**views, "front": views["left"]}, "viewStatus": status, "mouthShots": gen["notes"]}


def build_mock_rig(name: str, skin: Path, url: Callable[[Path], str]) -> dict[str, Any]:
    """No-network rig with the same layers, for tests and dev without a key."""
    W, H = 640, 800
    skin.mkdir(parents=True, exist_ok=True)
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    d.rounded_rectangle((110, 520, 530, H + 80), 140, fill=(70, 90, 140, 255), outline=(25, 25, 35, 255), width=6)
    d.ellipse((170, 150, 470, 520), fill=(240, 200, 160, 255), outline=(25, 25, 35, 255), width=6)
    for x in (265, 375):
        d.ellipse((x - 20, 300, x + 20, 344), fill=(30, 30, 40, 255))
    d.line((285, 440, 355, 440), fill=(120, 40, 50, 255), width=8)
    shot = base.copy()
    ImageDraw.Draw(shot).ellipse((280, 420, 360, 480), fill=(120, 30, 40, 255))
    shots_dir = skin / "_mock"
    shots_dir.mkdir(exist_ok=True)
    base.save(shots_dir / "closed.png")
    shot.save(shots_dir / "open.png")
    boxes = {"face": (170, 150, 470, 520), "left_eye": (245, 300, 285, 344), "right_eye": (355, 300, 395, 344), "mouth": (285, 425, 355, 455)}
    views = assemble(skin, {"closed": shots_dir / "closed.png", "open": shots_dir / "open.png"}, boxes, url, matte=lambda im: im.convert("RGBA"))
    status = {"left": "mock", "right": "mock (mirror)", "front": "mock (left art)"}
    return {"views": {**views, "front": views["left"]}, "viewStatus": status, "mouthShots": {"open": "mock", "round": "warped"}}
