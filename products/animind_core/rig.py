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
   ``X A B C D E F G H``. The ``right`` view is the mirror of ``left``.
5. ``front``: one more Gemini call redraws the same character facing the
   camera, with the side shot as reference. It is cut, detected and rigged
   (eyes, lids, warped mouths) like the side. If the budget or detection
   fails, ``front`` falls back to the left art and ``viewStatus`` says so.

``style`` (see :mod:`styles`) swaps the drawing-style sentence of the prompt.

Everything is written under ``<data>/assets/puppets/<id>/``; nothing here
touches git. ``build_mock_rig`` draws the same layer set with PIL, no network.
"""
from __future__ import annotations

import json
import math
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
    "{subject}. {style}. "
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
FRONT_PROMPT = (
    "Redraw this exact same character, same art style, colors, outfit, hair and outlines, but rotate the head "
    "and shoulders to face the camera straight on: full frontal view, nose on the vertical center line of the "
    "face, both eyes and both ears equally visible, symmetric shoulders, looking directly at the viewer. Medium bust shot, head and "
    "shoulders, same scale and framing, head in the upper half, shoulders reaching the bottom edge. Mouth "
    "closed, lips together, eyes open, neutral friendly expression. Solid flat pure magenta #FF00FF background. "
    "No text."
)
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


def generate_shots(subject: str, work: Path, *, raw_prompt: str | None = None, style: str = "cartoon",
                   front: bool = True) -> dict[str, Any]:
    """Draw the closed-mouth master, the front view (side as reference) and the two mouth edits.

    Order matters under the budget cap: master, then front, then mouths (mouths can be warped)."""
    from . import styles  # noqa: PLC0415
    from products.aiwake.animator.factory.create_puppet import generate_character_image  # noqa: PLC0415

    work.mkdir(parents=True, exist_ok=True)
    raw = work / "_gen_closed"
    generate_character_image(raw_prompt or STYLE_PROMPT.format(subject=subject, style=styles.prompt_style(style)), raw, aspect_ratio="4:5")
    closed = _png(raw, work / "source_closed.png")
    raw.unlink(missing_ok=True)
    size = Image.open(closed).size
    shots: dict[str, Path] = {"closed": closed}
    notes: dict[str, str] = {}
    front_boxes: dict[str, Box] | None = None
    if front:
        front_boxes = _front_shot(closed, work, size, shots, notes)
    for name, prompt in EDIT_PROMPTS.items():
        raw = work / f"_gen_{name}"
        try:
            generate_character_image(prompt, raw, aspect_ratio="4:5", reference_path=closed)
            shots[name] = _png(raw, work / f"source_{name}.png", size)
            notes[name] = "gemini"
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — budget stops included; visemes fall back to warps
            _LOG.warning("shot %s skipped: %s", name, exc)
            notes[name] = f"warped (shot failed: {str(exc)[:160]})"
        finally:
            raw.unlink(missing_ok=True)
    return {"shots": shots, "notes": notes, "frontBoxes": front_boxes}


FRONT_RETRY = (
    " IMPORTANT: the previous attempt came out as a three-quarter view. The face must be perfectly frontal and "
    "mirror-symmetric: the nose exactly in the middle between the eyes, both eyes the same size, both cheeks and "
    "both ears equally wide."
)
FRONT_MAX_OFFSET = 0.04  # eyes/mouth centre off the face centre, as a share of face width (3/4 views: >= 0.065)
FRONT_MIN_EYE_RATIO = 0.8  # narrower eye / wider eye


def frontality(boxes: dict[str, Box]) -> dict[str, float | bool]:
    """Is a drawn face frontal? On a 3/4 view the eyes and mouth sit off the face centre and the far
    eye is narrower. Measured on the detector boxes: frontal shots measure <= 0.03, side art >= 0.065."""
    f, le, re_, m = boxes["face"], boxes["left_eye"], boxes["right_eye"], boxes["mouth"]
    fw = max(f[2] - f[0], 1)
    cx = (f[0] + f[2]) / 2
    eyes = abs(((le[0] + le[2]) / 2 + (re_[0] + re_[2]) / 2) / 2 - cx) / fw
    mouth = abs((m[0] + m[2]) / 2 - cx) / fw
    wl, wr = max(le[2] - le[0], 1), max(re_[2] - re_[0], 1)
    ratio = min(wl, wr) / max(wl, wr)
    ok = max(eyes, mouth) <= FRONT_MAX_OFFSET and ratio >= FRONT_MIN_EYE_RATIO
    return {"eyesOffset": round(eyes, 3), "mouthOffset": round(mouth, 3), "eyeRatio": round(ratio, 2), "frontal": ok}


def _front_shot(closed: Path, work: Path, size: tuple[int, int], shots: dict[str, Path], notes: dict[str, str],
                *, attempts: int = 2) -> dict[str, Box] | None:
    """Draw the front view (side as reference), check it is frontal, retry once with a firmer prompt.

    Runs before the mouth edits so the retry spends the budget the open mouth would have used (that
    mouth then falls back to a warp). A 3/4 result that still fails keeps the best attempt, flagged."""
    from products.aiwake.animator.factory.create_puppet import generate_character_image  # noqa: PLC0415

    best: tuple[float, Path, dict[str, Box], dict[str, Any]] | None = None
    for attempt in range(attempts):
        raw = work / "_gen_front"
        try:
            generate_character_image(FRONT_PROMPT + (FRONT_RETRY if attempt else ""), raw, aspect_ratio="4:5", reference_path=closed)
            out = _png(raw, work / f"source_front{'' if not attempt else f'_{attempt + 1}'}.png", size)
            boxes = detect_features(out)
            check = frontality(boxes)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — budget stop or detection failure
            _LOG.warning("front attempt %d failed: %s", attempt + 1, exc)
            if best is None:
                notes["front"] = f"left art (shot failed: {str(exc)[:160]})"
            break
        finally:
            raw.unlink(missing_ok=True)
        score = max(check["eyesOffset"], check["mouthOffset"]) + (1 - check["eyeRatio"]) * 0.1
        if best is None or score < best[0]:
            best = (score, out, boxes, check)
        if check["frontal"]:
            break
    if best is None:
        return None
    _score, out, boxes, check = best
    if out.name != "source_front.png":
        out.replace(work / "source_front.png")
        out = work / "source_front.png"
    shots["front"] = out
    tries = attempt + 1
    notes["front"] = (f"gemini ({'frontal' if check['frontal'] else 'still 3/4, best of ' + str(tries)}; "
                      f"eyes {check['eyesOffset']}, mouth {check['mouthOffset']}, eye ratio {check['eyeRatio']}; tries {tries})")
    return boxes


def regenerate_front(skin: Path, url: Callable[[Path], str]) -> dict[str, Any]:
    """Redraw only the front view of an existing Gemini rig (checked + one retry) and re-rig it."""
    reset_budget()
    closed = skin / "source_closed.png"
    size = Image.open(closed).size
    shots: dict[str, Path] = {}
    notes: dict[str, str] = {}
    boxes = _front_shot(closed, skin, size, shots, notes)
    if not boxes:
        return {"frontShot": notes.get("front", "failed")}
    matte = BiRefNetMatte()
    try:
        views = assemble(skin, {"closed": shots["front"]}, boxes, url, matte=matte, front=True)
    finally:
        matte.close()
    return {"views": views, "frontShot": notes["front"]}


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
    if shot is not None:
        src = _open_cavity(patch, src, sx, sy)
    elif sx != 1.0 or sy != 1.0:
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


def _open_cavity(patch: Image.Image, shot: Image.Image, sx: float, sy: float) -> Image.Image:
    """Base ``patch`` with only the part of ``shot`` that differs (the open
    mouth) pasted in, scaled about its own top edge. Scaling the whole crop
    instead squashed the mustache and lips into a second copy over the base
    one, which read as a horizontal tear in close-ups."""
    a, b = np.asarray(patch, np.int16), np.asarray(shot, np.int16)
    diff = (np.abs(a[..., :3] - b[..., :3]).max(axis=2) > 40) & (b[..., 3] > 128)
    out = patch.copy()
    if not diff.any():
        return out
    m = Image.fromarray((diff * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(2))
    cav = shot.copy()
    cav.putalpha(Image.fromarray(np.minimum(np.asarray(m), np.asarray(shot.getchannel("A")))))
    bx = m.getbbox()
    if bx and (sx != 1.0 or sy != 1.0):
        part = cav.crop(bx)
        nw, nh = max(1, int(part.width * sx)), max(1, int(part.height * sy))
        cav = Image.new("RGBA", patch.size, (0, 0, 0, 0))
        cav.paste(part.resize((nw, nh), Image.LANCZOS), (bx[0] + (part.width - nw) // 2, bx[1]))
    out.alpha_composite(cav)
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


def _iris(base: Image.Image, box: Box) -> tuple[float, float, float]:
    """Iris centre and radius in ``box``: the largest dark blob near the middle of the eye."""
    import cv2  # noqa: PLC0415

    x0, y0, x1, y1 = box
    w, h = max(2, x1 - x0), max(2, y1 - y0)
    arr = np.asarray(base.crop(box)).astype(np.float32)
    lum = arr[..., :3].mean(axis=2)
    inside = (np.asarray(_feather_ellipse(w, h, 0.1)) > 128) & (arr[..., 3] > 128)
    fallback = (x0 + w / 2, y0 + h / 2, max(2.0, min(w, h) * 0.3))
    if inside.sum() < 12:
        return fallback
    dark = (lum < min(np.percentile(lum[inside], 45), 140)) & inside
    n, labels, stats, cents = cv2.connectedComponentsWithStats(dark.astype(np.uint8), connectivity=8)
    best, score = None, 0.0
    for i in range(1, n):
        cx, cy = cents[i]
        area = stats[i, cv2.CC_STAT_AREA]
        central = 1 - min(1.0, math.hypot((cx - w / 2) / (w / 2), (cy - h / 2) / (h / 2)))
        s = area * (0.3 + central)
        if s > score:
            best, score = i, s
    if best is None:
        return fallback
    cx, cy = cents[best]
    r = math.sqrt(stats[best, cv2.CC_STAT_AREA] / math.pi) * 1.1
    r = min(max(r, min(w, h) * 0.16), min(w, h) * 0.45)
    return (x0 + float(cx), y0 + float(cy), float(r))


def _eye_parts(base: Image.Image, eyes: list[Box], skin: tuple[int, int, int], ink: tuple[int, int, int]) -> dict[str, Any]:
    """Movable eyes cut from the art.

    ``eye_white``: the eye with its iris painted out (sclera colour + inpaint);
    ``iris``: each iris/pupil disc at rest; ``eye_socket``: the opening the iris
    is clipped to; ``eyes_half``: upper lids half down. ``meta`` gives the iris
    travel in canvas px (``maxShift``) so a gaze of +-1 stays inside the socket.
    """
    import cv2  # noqa: PLC0415

    size = base.size
    white = Image.new("RGBA", size, (0, 0, 0, 0))
    iris = Image.new("RGBA", size, (0, 0, 0, 0))
    socket = Image.new("RGBA", size, (0, 0, 0, 0))
    half = Image.new("RGBA", size, (0, 0, 0, 0))
    shifts, centres = [], []
    for b in eyes:
        x0, y0, x1, y1 = b
        w, h = x1 - x0, y1 - y0
        cx, cy, r = _iris(base, b)
        centres.append([round(cx, 1), round(cy, 1), round(r, 1)])
        patch = base.crop(b)
        arr = np.asarray(patch).copy()
        lum = arr[..., :3].astype(np.float32).mean(axis=2)
        disc = np.zeros((h, w), np.uint8)
        cv2.circle(disc, (round(cx - x0), round(cy - y0)), round(r * 1.12), 255, -1)
        bright = arr[(lum > np.percentile(lum, 75)) & (arr[..., 3] > 200)][..., :3]
        sclera = tuple(int(v) for v in np.median(bright, axis=0)) if len(bright) else skin
        rgb = np.ascontiguousarray(arr[..., :3])
        rgb[disc > 0] = sclera
        rgb = cv2.inpaint(rgb, cv2.dilate(disc, np.ones((3, 3), np.uint8)) - disc, 3, cv2.INPAINT_TELEA)
        arr[..., :3] = rgb
        ell = np.asarray(_feather_ellipse(w, h, 0.18))
        arr[..., 3] = np.minimum(arr[..., 3], ell)
        white.alpha_composite(Image.fromarray(arr), (x0, y0))
        # iris disc from the original art
        rr = round(r * 1.12)
        ib = (round(cx) - rr, round(cy) - rr, round(cx) + rr, round(cy) + rr)
        ip = base.crop(ib)
        m = Image.new("L", ip.size, 0)
        ImageDraw.Draw(m).ellipse((1, 1, ip.width - 2, ip.height - 2), fill=255)
        m = m.filter(ImageFilter.GaussianBlur(1))
        ip.putalpha(Image.fromarray(np.minimum(np.asarray(ip.getchannel("A")), np.asarray(m))))
        iris.alpha_composite(ip, (ib[0], ib[1]))
        # socket: the inner opening of the eye
        sk = Image.new("L", (w, h), 0)
        ImageDraw.Draw(sk).ellipse((w * 0.1, h * 0.14, w * 0.9, h * 0.86), fill=255)
        sk = sk.filter(ImageFilter.GaussianBlur(max(1, min(w, h) // 20)))
        sock = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        sock.putalpha(sk)
        socket.alpha_composite(sock, (x0, y0))
        shifts.append((max(w * 0.12, w * 0.42 - r * 0.6), max(h * 0.08, h * 0.36 - r * 0.7)))
        # half lid: skin over the upper socket, curved lash line
        lid = Image.new("RGBA", (w, h), skin + (255,))
        lm = Image.new("L", (w, h), 0)
        ImageDraw.Draw(lm).ellipse((w * 0.04, h * 0.02, w * 0.96, h * 0.98), fill=255)
        ImageDraw.Draw(lm).ellipse((w * 0.02, h * 0.42, w * 0.98, h * 1.3), fill=0)
        lid.putalpha(lm.filter(ImageFilter.GaussianBlur(max(1, h // 30))))
        ImageDraw.Draw(lid).arc((w * 0.1, h * 0.42, w * 0.9, h * 1.3), 215, 325,
                                fill=ink + (255,), width=max(2, h // 14))
        half.alpha_composite(lid, (x0, y0))
    for layer in (white, iris, half):
        layer.putalpha(Image.fromarray(np.minimum(np.asarray(layer.getchannel("A")), np.asarray(base.getchannel("A")))))
    mx = min(s[0] for s in shifts)
    my = min(s[1] for s in shifts)
    return {"eye_white": white, "iris": iris, "eye_socket": socket, "eyes_half": half,
            "meta": {"maxShift": [round(mx, 1), round(my, 1)], "irises": centres}}


def _slice_bust(base: Image.Image, face: Box, chin: int) -> tuple[Image.Image, Image.Image, Image.Image]:
    """Torso, neck and head, sliced like the Aiwake puppets
    (``animation.parametric.factory.layer_slicer.slice_character_layers``): the head ends at the
    jaw, the body owns the neck, which rises ``overlap`` px behind the jaw so a head tilt never
    opens a gap. The neck is then lifted out of the body as its own layer (the torso keeps it too)."""
    from animation.parametric.factory.layer_slicer import slice_character_layers  # noqa: PLC0415

    fw, fh = face[2] - face[0], face[3] - face[1]
    collar = chin + int(fh * 0.32)
    sl = slice_character_layers(base, neck_pivot=((face[0] + face[2]) // 2, collar), chin_boundary=chin,
                                facing="left", overlap_px=max(8, fh // 10),
                                # the robots' "facial plate" is the lower face; sampling the head's
                                # width above it must reach the crown, also on bald or long-haired heads
                                facial_plate_bbox=(face[0], face[1] + fh // 2, face[2], face[3]))
    # Under the jaw line the torso also keeps the head pixels, so the head's breath offset
    # slides over art instead of opening a seam.
    a = np.asarray(base.getchannel("A"))
    under = (np.arange(base.size[1])[:, None] >= chin - sl.overlap_px) * a
    torso = sl.body.copy()
    torso.putalpha(Image.fromarray(np.maximum(np.asarray(sl.body.getchannel("A")), under).astype(np.uint8)))
    nx0, nx1 = face[0] + int(fw * 0.18), face[2] - int(fw * 0.18)
    m = Image.new("L", base.size, 0)
    ImageDraw.Draw(m).rectangle((nx0, min(chin, sl.collar_y) - sl.overlap_px, nx1, sl.collar_y), fill=255)
    m = m.filter(ImageFilter.GaussianBlur(max(2, fw // 40)))
    neck = torso.copy()
    neck.putalpha(Image.fromarray(np.minimum(np.asarray(m), np.asarray(torso.getchannel("A")))))
    return torso, neck, sl.head


def _mouthless(head: Image.Image, mbox: Box) -> Image.Image:
    """``head`` with the mouth region (ellipse in ``mbox``) inpainted from the surrounding skin,
    so the face stays clean and the mouth layer alone carries the lips."""
    import cv2  # noqa: PLC0415

    arr = np.asarray(head).copy()
    hole = np.zeros(arr.shape[:2], np.uint8)
    x0, y0, x1, y1 = mbox
    w, h = x1 - x0, y1 - y0
    cv2.ellipse(hole, ((x0 + x1) // 2, (y0 + y1) // 2), (int(w * 0.42), int(h * 0.42)), 0, 0, 360, 255, -1)
    hole &= (arr[..., 3] > 128).astype(np.uint8) * 255
    rgb = cv2.inpaint(np.ascontiguousarray(arr[..., :3]), hole, max(3, w // 12), cv2.INPAINT_TELEA)
    arr[..., :3] = rgb
    return Image.fromarray(arr)


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
    # z: eyes (rest pose, for older clients) and eye_white share a slot; renderers that know
    # eye_white draw white -> iris (shifted by gaze, clipped to eye_socket) -> lids instead.
    order = (("body", 0), ("torso", 0), ("neck", 1), ("head", 2), ("eyes", 3), ("eye_white", 3), ("iris", 4), ("eye_socket", 4),
             ("eyes_half", 5), ("eyes_closed", 6))
    for name, z in order:
        if name in parts:
            img, xy = place(*parts[name])
            layers.append(_layer(name, img, xy, z, out / f"{name}.png", url))
    visemes = {}
    for v in VISEMES:
        img, xy = place(*parts["visemes"][v])
        visemes[v] = _layer(f"mouth_{v}", img, xy, 10, out / "mouths" / f"{v}.png", url)
    view = {"canvas": {"w": W, "h": H}, "layers": layers, "visemes": visemes}
    if "eyeRig" in parts:  # screen-space travel; irises mirrored with the view
        meta = parts["eyeRig"]
        view["eyeRig"] = {"maxShift": meta["maxShift"],
                          "irises": [[W - c[0] if mirror else c[0], c[1], c[2]] for c in meta["irises"]]}
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
    base = skin / "views" / view
    if not (base / "view.json").exists():
        base = skin / "views" / ("left" if view == "front" else view)
    data = json.loads((base / "view.json").read_text(encoding="utf-8"))
    for layer in [*data["layers"], *data["visemes"].values()]:
        layer["url"] = str(base / layer["url"])
    return data


def assemble(skin: Path, shots: dict[str, Path], boxes: dict[str, Box], url: Callable[[Path], str], *,
             matte: Callable[[Image.Image], Image.Image], front: bool = False) -> dict[str, Any]:
    """Build view parts from mattes + boxes: left/right from the side art, or ``views/front`` when ``front``."""
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
    eye = _eye_parts(base, eyes_boxes, skin_rgb, ink)
    chin = max(face[3], mouth[3]) + int((face[3] - face[1]) * 0.04)
    torso, neck, head = _slice_bust(base, face, chin)
    parts = {
        "size": size,
        "torso": _crop_layer(torso),
        "neck": _crop_layer(neck),
        "head": _crop_layer(_mouthless(head, mbox)),
        "eyes": _crop_layer(eyes_open),
        "eyes_closed": _crop_layer(eyes_closed),
        "eye_white": _crop_layer(eye["eye_white"]),
        "iris": _crop_layer(eye["iris"]),
        "eye_socket": _crop_layer(eye["eye_socket"]),
        "eyes_half": _crop_layer(eye["eyes_half"]),
        "eyeRig": eye["meta"],
        "visemes": {v: (img, (mbox[0], mbox[1])) for v, img in sprites.items()},
    }
    if front:
        base.save(skin / "character_front.png", format="PNG", optimize=True)
        (skin / "rig_front.json").write_text(json.dumps({"size": size, "boxes": boxes, "mouthBox": mbox}, indent=2), encoding="utf-8")
        return {"front": _write_view(parts, skin / "views" / "front", url, mirror=False)}
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


def build_rig(subject: str, skin: Path, url: Callable[[Path], str], *, raw_prompt: str | None = None,
              style: str = "cartoon") -> dict[str, Any]:
    """Real path: Gemini shots -> BiRefNet mattes -> layered rig with 3 views (front drawn, not reused)."""
    reset_budget()
    gen = generate_shots(subject, skin, raw_prompt=raw_prompt, style=style)
    boxes = detect_features(gen["shots"]["closed"])
    matte = BiRefNetMatte()
    status = {"left": "ready", "right": "ready (mirror of left)", "front": "ready (left art; no frontal shot)"}
    try:
        views = assemble(skin, gen["shots"], boxes, url, matte=matte)
        fr = gen["shots"].get("front")
        if fr:
            try:
                views |= assemble(skin, {"closed": fr}, gen.get("frontBoxes") or detect_features(fr), url, matte=matte, front=True)
                status["front"] = "ready (gemini front view, side as reference; mouths warped)"
            except Exception as exc:  # noqa: BLE001 — detection/cut failure keeps the side art
                _LOG.warning("front view rig failed: %s", exc)
                status["front"] = f"ready (left art; front rig failed: {str(exc)[:120]})"
    finally:
        matte.close()
    views.setdefault("front", views["left"])
    return {"views": views, "viewStatus": status, "mouthShots": {k: v for k, v in gen["notes"].items() if k != "front"},
            "frontShot": gen["notes"].get("front", "skipped")}


def build_mock_rig(name: str, skin: Path, url: Callable[[Path], str]) -> dict[str, Any]:
    """No-network rig with the same layers, for tests and dev without a key."""
    W, H = 640, 800
    skin.mkdir(parents=True, exist_ok=True)
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    d.rounded_rectangle((110, 520, 530, H + 80), 140, fill=(70, 90, 140, 255), outline=(25, 25, 35, 255), width=6)
    d.ellipse((170, 150, 470, 520), fill=(240, 200, 160, 255), outline=(25, 25, 35, 255), width=6)
    for x in (265, 375):
        d.ellipse((x - 26, 298, x + 26, 346), fill=(250, 250, 248, 255), outline=(25, 25, 35, 255), width=3)
        d.ellipse((x - 16, 306, x + 8, 338), fill=(60, 40, 30, 255))
        d.ellipse((x - 10, 314, x + 2, 330), fill=(10, 10, 12, 255))
    d.line((285, 440, 355, 440), fill=(120, 40, 50, 255), width=8)
    shot = base.copy()
    ImageDraw.Draw(shot).ellipse((280, 420, 360, 480), fill=(120, 30, 40, 255))
    shots_dir = skin / "_mock"
    shots_dir.mkdir(exist_ok=True)
    base.save(shots_dir / "closed.png")
    shot.save(shots_dir / "open.png")
    boxes = {"face": (170, 150, 470, 520), "left_eye": (237, 296, 293, 348), "right_eye": (347, 296, 403, 348), "mouth": (285, 425, 355, 455)}
    views = assemble(skin, {"closed": shots_dir / "closed.png", "open": shots_dir / "open.png"}, boxes, url, matte=lambda im: im.convert("RGBA"))
    views |= assemble(skin, {"closed": shots_dir / "closed.png"}, boxes, url, matte=lambda im: im.convert("RGBA"), front=True)
    status = {"left": "mock", "right": "mock (mirror)", "front": "mock (front rig)"}
    return {"views": views, "viewStatus": status, "mouthShots": {"open": "mock", "round": "warped"}, "frontShot": "mock"}
