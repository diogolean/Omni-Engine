"""Cut-out rig (contract v2) from Gemini art.

One request makes one character:

1. ``create_puppet.generate_character_image`` (Aiwake factory, guarded Gemini
   Flash image) draws a 3/4 bust facing left on magenta, mouth closed.
2. Referenced Gemini edits make a mouthless head and a parts sheet with all
   nine Rhubarb mouths plus half/closed lids. Failed cells fall back to
   deterministic warps and drawn lids.
3. BiRefNet (rembg ``birefnet-general``, the Rigly rigger's matte) cuts
   every shot, then the magenta spill is removed. A Gemini text call returns
   the face, eye and mouth boxes.
4. Layers: ``torso``, ``neck`` and mouthless ``head`` (split under the chin
   with an overlap), movable eyes, lids, eyebrows, and one mouth sprite per
   Rhubarb viseme ``X A B C D E F G H``. The ``right`` view mirrors ``left``.
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
import logging
import math
import os
import re
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

_LOG = logging.getLogger("animind_core.rig")

VISEMES = ("X", "A", "B", "C", "D", "E", "F", "G", "H")
DETECT_MODEL = "gemini-2.5-flash"

STYLE_PROMPT = (
    "{subject}. {style}. "
    "Medium bust shot (head and shoulders), three-quarter view, head turned 25 degrees toward the left "
    "of the image, looking left. Mouth closed, lips together, eyes open, friendly neutral expression, delicate thin softly drawn eyebrows. "
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
MOUTHLESS_PROMPT = (
    "Edit this image: the same character, identical pose, framing, scale, lighting, colours, outlines and art "
    "style, but remove the mouth and lips completely: smooth clean skin where the mouth was, no mouth, no lips, "
    "no lines, no shadow, no teeth. Keep the nose, chin, jaw, facial hair, eyes, glasses and everything else exactly "
    "as they are. Same solid flat background. No text."
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

    from production.cost.google_guardrail import (  # noqa: PLC0415
        guarded_generate_content,
        make_guarded_gemini_client,
    )

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


def _budget_left() -> float:
    from production.cost.google_guardrail import get_google_cost_tracker, max_google_cost_usd  # noqa: PLC0415
    return max_google_cost_usd() - get_google_cost_tracker().total_usd()


def spent_usd() -> float:
    from production.cost.google_guardrail import get_google_cost_tracker  # noqa: PLC0415
    return get_google_cost_tracker().total_usd()


def _parts_sheet(closed: Path, work: Path, boxes: dict[str, Box], attempts: dict[str, int], notes: dict[str, str],
                 tries: int = 2, tag: str = "sheet") -> Path | None:
    """Draw the 4x3 parts sheet (9 mouths + 3 eye states) and gate it: 12 cells must slice out and
    the closed-mouth and open-eye cells must register on the master (NCC). Re-drawn while the cap allows."""
    from products.aiwake.animator.factory.create_puppet import generate_character_image  # noqa: PLC0415

    from . import partsheet, qa  # noqa: PLC0415

    with Image.open(closed) as im:
        base = im.convert("RGBA")
    best: tuple[int, Path] | None = None
    for attempt in range(tries):
        if _budget_left() < qa.IMAGE_USD:
            notes[tag] = notes.get(tag, "") + f" | no budget for attempt {attempt + 1}"
            break
        attempts[tag] = attempts.get(tag, 0) + 1
        raw = work / f"_gen_{tag}"
        try:
            generate_character_image(partsheet.SHEET_PROMPT, raw, aspect_ratio="4:3", reference_path=closed)
            out = _png(raw, work / f"source_{tag}.png")
            got = partsheet.load(out, base, boxes)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — budget stop / bad image
            notes[tag] = f"failed: {str(exc)[:160]}"
            continue
        finally:
            raw.unlink(missing_ok=True)
        allgood = (got or {}).get("good", [])
        good = [k for k in allgood if k in partsheet.VISEMES]
        lids = all(e in allgood for e in ("eyes_closed", "eyes_half"))
        if len(good) >= partsheet.MIN_GOOD_MOUTHS:
            n = len(good) + 3 * lids
            if best is None or n > best[0]:
                keep = work / f"source_{tag}_best.png"
                out.replace(keep)
                best = (n, keep)
                notes[tag] = f"gemini (cells ok {''.join(good)}{' +lids' if lids else ' no lids'}, tries {attempt + 1})"
            if lids and len(good) == len(partsheet.VISEMES):
                break  # perfect sheet: stop spending
        else:
            notes[tag] = f"rejected (cells ok {''.join(good) or '-'}, tries {attempt + 1})"
    if best is None:
        return None
    final = work / f"source_{tag}.png"
    best[1].replace(final)
    return final


def _mouthless_shot(src: Path, work: Path, boxes: dict[str, Box], tag: str, attempts: dict[str, int],
                    notes: dict[str, str], tries: int = 2) -> Path | None:
    """Gemini edit of ``src`` with the mouth removed (the head is drawn mouthless instead of inpainted).
    Gated: no lip/cavity ink may remain around the mouth; re-drawn once while the cap allows."""
    from products.aiwake.animator.factory.create_puppet import generate_character_image  # noqa: PLC0415

    from . import qa  # noqa: PLC0415

    size = Image.open(src).size
    best: tuple[float, Path] | None = None
    prev = work / f"source_{tag}.png"
    if prev.exists():  # a re-draw only replaces an earlier attempt that left more ink
        keep = prev.replace(work / f"source_{tag}_0.png")
        with Image.open(keep) as im, Image.open(src) as ref:
            best = (qa.ink_share(np.asarray(im.convert("RGB")), np.asarray(ref.convert("RGB")), boxes["mouth"]), keep)
    for attempt in range(tries):
        if _budget_left() < qa.IMAGE_USD:
            notes[tag] = notes.get(tag, "") + f" | no budget for attempt {attempt + 1}"
            break
        attempts[tag] = attempts.get(tag, 0) + 1
        raw = work / f"_gen_{tag}"
        try:
            generate_character_image(MOUTHLESS_PROMPT, raw, aspect_ratio="4:5", reference_path=src)
            out = _png(raw, work / f"source_{tag}_{attempts[tag]}.png", size)
            with Image.open(out) as im, Image.open(src) as ref:
                left = qa.ink_share(np.asarray(im.convert("RGB")), np.asarray(ref.convert("RGB")), boxes["mouth"])
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            notes[tag] = f"failed: {str(exc)[:160]}"
            continue
        finally:
            raw.unlink(missing_ok=True)
        if best is None or left < best[0]:
            best = (left, out)
        ok = left <= qa.LIMITS["residualMouth"]
        notes[tag] = f"gemini ({'pass' if ok else 'FAIL'} residual ink {left:.3f}, tries {attempts[tag]})"
        if ok:
            break
    if best is None:
        return None
    final = work / f"source_{tag}.png"
    best[1].replace(final)
    for f in work.glob(f"source_{tag}_*.png"):
        f.unlink()
    return final


def generate_shots(subject: str, work: Path, *, raw_prompt: str | None = None, style: str = "cartoon",
                   front: bool = True, attempts: dict[str, int] | None = None) -> dict[str, Any]:
    """Generate-then-verify: master (closed mouth) -> parts sheet (all mouths + lids, gated) -> front view
    (gated for symmetry). Order is priority under the cap; a gate failure re-draws only while budget remains,
    otherwise that asset falls back to the deterministic path (warped mouths, drawn lids, side art)."""
    from products.aiwake.animator.factory.create_puppet import generate_character_image  # noqa: PLC0415

    from . import styles  # noqa: PLC0415

    attempts = attempts if attempts is not None else {}
    work.mkdir(parents=True, exist_ok=True)
    raw = work / "_gen_closed"
    attempts["master"] = 1
    generate_character_image(raw_prompt or STYLE_PROMPT.format(subject=subject, style=styles.prompt_style(style)), raw, aspect_ratio="4:5")
    closed = _png(raw, work / "source_closed.png")
    raw.unlink(missing_ok=True)
    size = Image.open(closed).size
    shots: dict[str, Path] = {"closed": closed}
    notes: dict[str, str] = {}
    boxes = detect_features(closed)
    # priority under the cap: master, mouthless master, front (retries keep 3 images in reserve),
    # mouthless front, side sheet, front sheet; leftover budget re-draws a sheet that failed its gate
    ml = _mouthless_shot(closed, work, boxes, "mouthless", attempts, notes, tries=1)
    front_boxes: dict[str, Box] | None = None
    if front and _budget_left() >= 0.039:
        front_boxes = _front_shot(closed, work, size, shots, notes, attempts=MAX_RETRIES, side_boxes=boxes,
                                  counter=attempts, reserve=3)
    fml = None
    if front_boxes is not None and "front" in shots:
        fml = _mouthless_shot(shots["front"], work, front_boxes, "front_mouthless", attempts, notes, tries=1)
    sheet = _parts_sheet(closed, work, boxes, attempts, notes, tries=1)
    fs = None
    if front_boxes is not None and "front" in shots and _budget_left() >= 0.039:
        fs = _parts_sheet(shots["front"], work, front_boxes, attempts, notes, tries=1, tag="sheet_front")
    # leftovers: the mouthless heads first (they decide double mouths), then the sheets
    for tag, src, bx in (("mouthless", closed, boxes), ("front_mouthless", shots.get("front"), front_boxes)):
        if src is not None and bx is not None and "pass" not in notes.get(tag, "") and _budget_left() >= 0.039:
            got = _mouthless_shot(src, work, bx, tag, attempts, notes, tries=1)
            if tag == "mouthless":
                ml = got or ml
            else:
                fml = got or fml
    if sheet is None and _budget_left() >= 0.039:
        sheet = _parts_sheet(closed, work, boxes, attempts, notes, tries=MAX_RETRIES - 1)
    if fs is None and front_boxes is not None and "front" in shots and _budget_left() >= 0.039:
        fs = _parts_sheet(shots["front"], work, front_boxes, attempts, notes, tries=1, tag="sheet_front")
    if ml is not None:
        shots["mouthless"] = ml
    if fml is not None:
        shots["front_mouthless"] = fml
    if sheet is not None:
        shots["sheet"] = sheet
    if fs is not None:
        shots["sheet_front"] = fs
    return {"shots": shots, "notes": notes, "frontBoxes": front_boxes, "boxes": boxes}


FRONT_RETRY = (
    " IMPORTANT: the previous attempt came out as a three-quarter view. The face must be perfectly frontal and "
    "mirror-symmetric: the nose exactly in the middle between the eyes, both eyes the same size, both cheeks and "
    "both ears equally wide."
)
FRONT_LANDMARKS = (
    " Keep the eye line and the mouth at exactly the same height on the face as in the reference image "
    "(same proportions, same muzzle/jaw length)."
)
MAX_RETRIES = 3  # per asset (sheet, front); each retry still needs budget left under the cap
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
                *, attempts: int = 2, side_boxes: dict[str, Box] | None = None,
                counter: dict[str, int] | None = None, reserve: int = 0) -> dict[str, Box] | None:
    """Draw the front view (side as reference), check it is frontal, retry once with a firmer prompt.

    Runs before the mouth edits so the retry spends the budget the open mouth would have used (that
    mouth then falls back to a warp). A 3/4 result that still fails keeps the best attempt, flagged."""
    from products.aiwake.animator.factory.create_puppet import generate_character_image  # noqa: PLC0415

    best: tuple[float, Path, dict[str, Box], dict[str, Any]] | None = None
    from . import qa  # noqa: PLC0415

    drift_bad = False
    for attempt in range(attempts):
        if attempt and _budget_left() < qa.IMAGE_USD * (1 + reserve):
            notes["frontBudget"] = f"no budget for front attempt {attempt + 1}"
            break
        if counter is not None:
            counter["front"] = counter.get("front", 0) + 1
        raw = work / "_gen_front"
        try:
            prompt = FRONT_PROMPT + (FRONT_RETRY if attempt else "") + (FRONT_LANDMARKS if drift_bad else "")
            generate_character_image(prompt, raw, aspect_ratio="4:5", reference_path=closed)
            out = _png(raw, work / f"source_front{'' if not attempt else f'_{attempt + 1}'}.png", size)
            boxes = detect_features(out)
            check = frontality(boxes)
            drift = 0.0
            if side_boxes is not None:
                drift = qa.landmark_alignment({k: list(v) for k, v in side_boxes.items()},
                                              {k: list(v) for k, v in boxes.items()})["value"]
            check["drift"] = drift
            drift_bad = drift > qa.LIMITS["landmarkDrift"]
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — budget stop or detection failure
            _LOG.warning("front attempt %d failed: %s", attempt + 1, exc)
            if best is None:
                notes["front"] = f"left art (shot failed: {str(exc)[:160]})"
            break
        finally:
            raw.unlink(missing_ok=True)
        score = max(check["eyesOffset"], check["mouthOffset"]) + (1 - check["eyeRatio"]) * 0.1 + check["drift"] * 0.5
        if best is None or score < best[0]:
            best = (score, out, boxes, check)
        if check["frontal"] and not drift_bad:
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
                      f"eyes {check['eyesOffset']}, mouth {check['mouthOffset']}, eye ratio {check['eyeRatio']}; drift {check.get('drift', 0)}; tries {tries})")
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


def _lips_mask(box: Box, mouth: Box) -> Image.Image:
    """Alpha of every mouth sprite inside ``box``: a soft ellipse around the detected mouth
    (dropping below it for open jaws). The base head is inpainted only where this is fully
    opaque, so where the sprite fades out the base underneath is the very same art: no seam,
    no rectangle, no second mustache."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    mw, mh = mouth[2] - mouth[0], mouth[3] - mouth[1]
    # detector boxes are tight: lip corners and cupid's bow sit outside them
    ex0, ex1 = mouth[0] - x0 - mw * 0.3, mouth[2] - x0 + mw * 0.3
    ey0, ey1 = mouth[1] - y0 - mh * 0.3, min(h - 2, mouth[3] - y0 + mh * 0.7)
    r = max(2, int(min(ex1 - ex0, ey1 - ey0) * 0.08))
    # fade fully inside the box, but never shrink past the detected mouth itself
    ex0 = max(ex0, min(3 * r, mouth[0] - x0))
    ey0 = max(ey0, min(3 * r, mouth[1] - y0))
    ex1 = min(ex1, max(w - 3 * r, mouth[2] - x0))
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).ellipse((ex0, ey0, ex1, ey1), fill=255)
    return m.filter(ImageFilter.GaussianBlur(r))


def _mouth_sprite(base: Image.Image, shot: Image.Image | None, box: Box, mouth: Box, *,
                  sy: float = 1.0, sx: float = 1.0, ref: Image.Image | None = None) -> Image.Image:
    """Mouth sprite for ``box``: the base art (lips + mustache, which this layer owns) with,
    for open shapes, only the cavity of ``shot`` colour-matched and pasted in. Alpha is
    always :func:`_lips_mask`, identical for every viseme so crossfades stay seamless."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    patch = base.crop(box)
    lips = _lips_mask(box, mouth)
    if shot is not None:
        patch = _open_cavity(patch, shot.crop(box), lips, sx, sy, ref.crop(box) if ref is not None else None)
    elif sx != 1.0 or sy != 1.0:
        nw, nh = max(1, int(w * sx)), max(1, int(h * sy))
        scaled = patch.resize((nw, nh), Image.LANCZOS)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        layer.paste(scaled, ((w - nw) // 2, int((h - nh) * 0.35)))
        core = Image.fromarray(np.where(np.asarray(lips) > 250, 255, 0).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3))
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), core))  # the rim keeps the base art untouched
        patch = patch.copy()
        patch.alpha_composite(layer)
    alpha = np.minimum(np.asarray(lips), np.asarray(base.crop(box).getchannel("A")))
    patch.putalpha(Image.fromarray(alpha))
    return patch


def _open_cavity(patch: Image.Image, shot: Image.Image, lips: Image.Image, sx: float, sy: float,
                 ref: Image.Image | None = None) -> Image.Image:
    """Base ``patch`` with only the open-mouth cavity of ``shot`` pasted in. The cavity is the
    largest changed blob inside the lips area, minus dark desaturated hair (a redrawn mustache
    would otherwise double), shifted to the base colours (median of the unchanged ring) and
    scaled about its top edge."""
    import cv2  # noqa: PLC0415

    a, b = np.asarray(patch, np.int16), np.asarray(shot, np.int16)
    r = np.asarray(ref, np.int16) if ref is not None else a  # sheet cells: diff against the sheet's own closed mouth
    inside = np.asarray(lips) > 250  # cavity stays in the opaque core; the rim is always base art
    def _hair(x: np.ndarray) -> np.ndarray:
        rgb = x[..., :3].astype(np.float32)
        return (rgb.mean(axis=2) < 90) & (rgb.max(axis=2) - rgb.min(axis=2) < 35)

    hair = _hair(a) | _hair(b) | _hair(r)  # mustache in either image stays the base's (owned by this layer, never redrawn)
    diff = (np.abs(r[..., :3] - b[..., :3]).max(axis=2) > 40) & (b[..., 3] > 128) & (r[..., 3] > 128) & inside & ~hair
    diff = cv2.morphologyEx(diff.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(diff, connectivity=8)
    out = patch.copy()
    if n < 2:
        return out
    keep = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    cav_m = lab == keep
    ring = inside & ~cav_m & (b[..., 3] > 128) & (a[..., 3] > 128)
    shift = np.median(a[ring][:, :3] - b[ring][:, :3], axis=0) if ring.sum() > 20 else np.zeros(3)
    fixed = np.asarray(shot).copy()
    fixed[..., :3] = np.clip(b[..., :3] + shift, 0, 255).astype(np.uint8)
    m = Image.fromarray((cav_m * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(1.5))
    cav = Image.fromarray(fixed)
    cav.putalpha(Image.fromarray(np.minimum(np.asarray(m), fixed[..., 3])))
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


def _lid_skin(base: Image.Image, box: Box, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    """Skin tone of the upper lid: median of the band just above the eye opening."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    arr = np.asarray(base.crop((x0, max(0, y0 - h // 6), x1, y0 + h // 6)))
    px = arr[arr[..., 3] > 200][..., :3]
    if len(px) < 10:
        return fallback
    lum = px.mean(axis=1)
    return tuple(int(v) for v in np.median(px[lum >= np.percentile(lum, 50)], axis=0))  # type: ignore[return-value]


def _eye_opening(base: Image.Image, box: Box) -> tuple[float, float, float, float]:
    """Bounding box (box-relative) of the visible eye: sclera (bright, unsaturated) plus the iris disc,
    padded 12%. Falls back to the inner ellipse of the box when nothing is found."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    arr = np.asarray(base.crop(box).convert("RGB")).astype(np.int16)
    mx, mn = arr.max(axis=2), arr.min(axis=2)
    sclera = (mn > 190) & (mx - mn < 40)
    ix, iy, ir = _iris(base, box)
    ys, xs = np.nonzero(sclera)
    pts_x = list(xs) + [ix - x0 - ir, ix - x0 + ir]
    pts_y = list(ys) + [iy - y0 - ir, iy - y0 + ir]
    if len(xs) < 6:
        return w * 0.06, h * 0.1, w * 0.94, h * 0.9
    ax0, ax1 = float(np.percentile(pts_x, 2)), float(np.percentile(pts_x, 98))
    ay0, ay1 = float(min(pts_y)), float(max(pts_y))
    px, py = (ax1 - ax0) * 0.12, (ay1 - ay0) * 0.12
    return max(w * 0.06, ax0 - px), max(h * 0.08, ay0 - py), min(w * 0.94, ax1 + px), min(h * 0.92, ay1 + py)


def _eyes_closed(base: Image.Image, eyes: list[Box], skin: tuple[int, int, int], ink: tuple[int, int, int]) -> Image.Image:
    """Fallback closed lids at full resolution: lid-coloured skin over the eye opening, plus a thin
    soft lash line (drawn 4x and downsampled). The parts sheet's drawn lids are preferred."""
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    for x0, y0, x1, y1 in eyes:
        w, h = x1 - x0, y1 - y0
        tone = _lid_skin(base, (x0, y0, x1, y1), skin)
        # thin lash line in a deep lid tone, not a thick black stroke
        lash = tuple(int(0.6 * i + 0.4 * t * 0.55) for i, t in zip(ink, tone))
        k = 4
        ex0, ey0, ex1, ey1 = _eye_opening(base, (x0, y0, x1, y1))
        m = Image.new("L", (w * k, h * k), 0)
        ImageDraw.Draw(m).ellipse((ex0 * k, ey0 * k, ex1 * k, ey1 * k), fill=255)
        lid = Image.new("RGBA", (w, h), tone + (255,))
        lid.putalpha(m.resize((w, h), Image.LANCZOS))
        line = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
        ow, oh = ex1 - ex0, ey1 - ey0
        ImageDraw.Draw(line).arc(((ex0 + ow * 0.05) * k, (ey0 - oh * 0.1) * k, (ex1 - ow * 0.05) * k, (ey0 + oh * 0.75) * k),
                                 20, 160, fill=lash + (255,), width=int(max(0.9 * k, oh * k / 26)))
        lid.alpha_composite(line.resize((w, h), Image.LANCZOS))
        layer.alpha_composite(lid, (x0, y0))
    alpha = np.minimum(np.asarray(layer.getchannel("A")), np.asarray(base.getchannel("A")))
    layer.putalpha(Image.fromarray(alpha))
    return layer


def _eyebrows(base: Image.Image, eyes: list[Box]) -> tuple[Image.Image, Image.Image]:
    """Lift the two brow strokes out of the face and return ``(layer, face_without_brows)``.

    Brows are selected as the widest dark component in the narrow skin band
    above each eye.  The mask is feathered by one pixel; the same mask is
    inpainted on the head so moving a brow cannot expose a second, fixed brow.
    """
    import cv2  # noqa: PLC0415

    rgba = np.asarray(base.convert("RGBA")).copy()
    mask = np.zeros(rgba.shape[:2], np.uint8)
    for x0, y0, x1, y1 in eyes:
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        rx0, rx1 = max(0, x0 - w // 5), min(base.width, x1 + w // 5)
        ry0, ry1 = max(0, y0 - int(h * 0.9)), min(base.height, y0 + max(1, int(h * 0.05)))
        crop = rgba[ry0:ry1, rx0:rx1]
        if crop.size == 0:
            continue
        opaque = crop[..., 3] > 128
        rgb = crop[..., :3].astype(np.float32)
        lum = rgb.mean(axis=2)
        if opaque.any():
            pixels = rgb[opaque]
            skin = np.median(pixels[pixels.mean(axis=1) >= np.median(pixels.mean(axis=1))], axis=0)
        else:
            skin = np.array([220, 180, 160], np.float32)
        dark = (lum < float(skin.mean()) - 38) & (np.abs(rgb - skin).max(axis=2) > 35) & opaque
        dark = cv2.morphologyEx(dark.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, lab, stats, cents = cv2.connectedComponentsWithStats(dark, connectivity=8)
        candidates = [
            i for i in range(1, n)
            if stats[i, cv2.CC_STAT_WIDTH] >= max(5, int(w * 0.18))
            and stats[i, cv2.CC_STAT_WIDTH] >= 1.35 * stats[i, cv2.CC_STAT_HEIGHT]
            and ry0 + cents[i][1] < y0 - h * 0.04
            and stats[i, cv2.CC_STAT_AREA] >= 4
        ]
        if not candidates:
            continue
        best = max(candidates, key=lambda i: stats[i, cv2.CC_STAT_AREA] * stats[i, cv2.CC_STAT_WIDTH])
        chosen = (lab == best).astype(np.uint8)
        chosen = cv2.dilate(chosen, np.ones((3, 3), np.uint8))
        mask[ry0:ry1, rx0:rx1] = np.maximum(mask[ry0:ry1, rx0:rx1], chosen * 255)

    soft = cv2.GaussianBlur(mask, (0, 0), 0.8)
    layer_arr = rgba.copy()
    layer_arr[..., 3] = np.minimum(layer_arr[..., 3], soft)
    layer = Image.fromarray(layer_arr)
    clean_arr = rgba.copy()
    hole = ((mask > 0) & (rgba[..., 3] > 128)).astype(np.uint8) * 255
    if hole.any():
        clean_arr[..., :3] = cv2.inpaint(
            np.ascontiguousarray(clean_arr[..., :3]), hole, 3, cv2.INPAINT_TELEA
        )
    return layer, Image.fromarray(clean_arr)


def _sheet_lids(base: Image.Image, closed: Image.Image, eyes: list[Box]) -> Image.Image | None:
    """Closed lids cut from the parts sheet (registered ``closed``): the drawn lids inside each eye
    opening, crisp 1 px edge, shifted to the master's skin colours (median over the eye box rim)."""
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    for x0, y0, x1, y1 in eyes:
        w, h = x1 - x0, y1 - y0
        cell = np.asarray(closed.crop((x0, y0, x1, y1))).astype(np.int16)
        ref = np.asarray(base.crop((x0, y0, x1, y1))).astype(np.int16)
        if (cell[..., 3] > 128).mean() < 0.6:
            return None
        k = 4
        m = Image.new("L", (w * k, h * k), 0)
        ImageDraw.Draw(m).ellipse((w * k * 0.06, h * k * 0.1, w * k * 0.94, h * k * 0.9), fill=255)
        m = np.asarray(m.resize((w, h), Image.LANCZOS))
        rim = (m < 20) & (cell[..., 3] > 128) & (ref[..., 3] > 128)
        shift = np.median(ref[rim][:, :3] - cell[rim][:, :3], axis=0) if rim.sum() > 10 else np.zeros(3)
        out = cell.copy()
        out[..., :3] = np.clip(cell[..., :3] + shift, 0, 255)
        out[..., 3] = np.minimum(m, cell[..., 3])
        layer.alpha_composite(Image.fromarray(out.astype(np.uint8)), (x0, y0))
    layer.putalpha(Image.fromarray(np.minimum(np.asarray(layer.getchannel("A")), np.asarray(base.getchannel("A")))))
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
        shifts.append((max(1.0, min(w * 0.4 - r * 1.1, w * 0.18)), max(1.0, min(h * 0.36 - r * 1.1, h * 0.1))))  # iris stays inside the socket
        # half lid: skin over the upper socket, curved lash line
        # only over the eye opening (never the brows, glasses or hair above), thin soft lash line
        tone = _lid_skin(base, (x0, y0, x0 + w, y0 + h), skin)
        ox0, oy0, ox1, oy1 = _eye_opening(base, b)
        ow, oh = ox1 - ox0, oy1 - oy0
        lid = Image.new("RGBA", (w, h), tone + (255,))
        lm = Image.new("L", (w, h), 0)
        ImageDraw.Draw(lm).ellipse((ox0, oy0, ox1, oy1), fill=255)
        ImageDraw.Draw(lm).ellipse((ox0 - ow * 0.02, oy0 + oh * 0.45, ox1 + ow * 0.02, oy1 + oh * 0.8), fill=0)
        lid.putalpha(lm.filter(ImageFilter.GaussianBlur(0.8)))
        lash = tuple(int(0.6 * i + 0.4 * t * 0.55) for i, t in zip(ink, tone))
        ImageDraw.Draw(lid).arc((ox0 + ow * 0.04, oy0 + oh * 0.45, ox1 - ow * 0.04, oy1 + oh * 0.8), 200, 340,
                                fill=lash + (255,), width=max(1, round(oh / 22)))
        soft = Image.new("L", (w, h), 0)
        ImageDraw.Draw(soft).ellipse((ox0 + 1.5, oy0 + 1.5, ox1 - 1.5, oy1 - 1.5), fill=255)
        soft = np.asarray(soft.filter(ImageFilter.GaussianBlur(1.5)))
        lid.putalpha(Image.fromarray(np.minimum(np.asarray(lid.getchannel("A")), soft)))
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


def _mouthless(head: Image.Image, mbox: Box, mouth: Box) -> Image.Image:
    """``head`` inpainted where the mouth sprite is fully opaque (:func:`_lips_mask` > 250), so
    the mouth layer alone carries lips and mustache and its soft rim lies over unchanged art."""
    import cv2  # noqa: PLC0415

    arr = np.asarray(head).copy()
    hole = np.zeros(arr.shape[:2], np.uint8)
    x0, y0, x1, y1 = mbox
    hole[y0:y1, x0:x1] = (np.asarray(_lips_mask(mbox, mouth)) > 250).astype(np.uint8) * 255
    hole &= (arr[..., 3] > 128).astype(np.uint8) * 255
    rgb = cv2.inpaint(np.ascontiguousarray(arr[..., :3]), hole, max(3, (x1 - x0) // 12), cv2.INPAINT_TELEA)
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
             ("eyes_half", 5), ("eyes_closed", 6), ("brows", 7))
    for name, z in order:
        if name in parts:
            img, xy = place(*parts[name])
            layers.append(_layer(name, img, xy, z, out / f"{name}.png", url))
    visemes = {}
    for v in VISEMES:
        img, xy = place(*parts["visemes"][v])
        visemes[v] = _layer(f"mouth_{v}", img, xy, 10, out / "mouths" / f"{v}.png", url)
    view = {"canvas": {"w": W, "h": H}, "layers": layers, "visemes": visemes}
    if "mouthQA" in parts:
        mq = dict(parts["mouthQA"])
        if mirror:
            for k in ("mouthBox", "mouth"):
                b = mq[k]
                mq[k] = [W - b[2], b[1], W - b[0], b[3]]
        view["mouthQA"] = mq
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


def _refine_mouth(base: Image.Image, mouth: Box) -> Box:
    """Grow the detector's mouth box to the drawn lips: pixels redder than the surrounding skin that touch
    the box (full, glossy or painted lips often spill past the detector box and survive the erase)."""
    import cv2  # noqa: PLC0415

    x0, y0, x1, y1 = mouth
    w, h = x1 - x0, y1 - y0
    W, H = base.size
    rx0, ry0, rx1, ry1 = max(0, x0 - w // 2), max(0, y0 - h), min(W, x1 + w // 2), min(H, y1 + h)
    a = np.asarray(base.crop((rx0, ry0, rx1, ry1))).astype(np.int16)
    red = a[..., 0] - a[..., 1]
    opaque = a[..., 3] > 128
    if opaque.sum() < 20:
        return mouth
    skin_red = float(np.median(red[opaque]))
    lips = ((red > skin_red + 25) & (a[..., 0] > a[..., 2] + 20) & opaque).astype(np.uint8)
    n, labels, stats, _c = cv2.connectedComponentsWithStats(lips, connectivity=8)
    bx0, by0, bx1, by1 = x0 - rx0, y0 - ry0, x1 - rx0, y1 - ry0
    out = list(mouth)
    for i in range(1, n):
        sx, sy, sw, sh, area = stats[i]
        if area < 6 or sx > bx1 or sx + sw < bx0 or sy > by1 or sy + sh < by0:
            continue  # must touch the detector box
        out = [min(out[0], rx0 + sx), min(out[1], ry0 + sy), max(out[2], rx0 + sx + sw), max(out[3], ry0 + sy + sh)]
    # never more than 1.5x the detector box per side (blush / tongue-red clothing nearby)
    out[0], out[1] = max(out[0], x0 - w // 4), max(out[1], y0 - h // 2)
    out[2], out[3] = min(out[2], x1 + w // 4), min(out[3], y1 + h // 2)
    return tuple(int(v) for v in out)  # type: ignore[return-value]


def _clean_base(base: Image.Image, shots: dict[str, Path], key: str, mbox: Box, mouth: Box) -> tuple[Image.Image, str]:
    """``base`` with no mouth: the mouth area comes from the Gemini mouthless edit (``shots[key]``, aligned
    and colour-matched, blended in a soft ellipse so eyes/hair stay the master's), else an inpaint fallback."""
    if key in shots and shots[key].exists():
        with Image.open(shots[key]) as im:
            ml = im.convert("RGBA").resize(base.size, Image.LANCZOS)
        with Image.open(shots["closed"]) as im:
            ref = im.convert("RGBA").resize(base.size)
        dx, dy = _shift(np.asarray(ref), np.asarray(ml))
        if abs(dx) < 40 and abs(dy) < 40:
            ml = _translate(ml, dx, dy)
        W, H = base.size
        mw, mh = mouth[2] - mouth[0], mouth[3] - mouth[1]
        ell = (mouth[0] - mw * 0.55, mouth[1] - mh * 0.8, mouth[2] + mw * 0.55, mouth[3] + mh * 1.1)
        m = Image.new("L", base.size, 0)
        ImageDraw.Draw(m).ellipse(ell, fill=255)
        m = m.filter(ImageFilter.GaussianBlur(max(3, mw // 8)))
        a, b = np.asarray(ref).astype(np.int16), np.asarray(ml).astype(np.int16)
        ring = (np.asarray(m) > 5) & (np.asarray(m) < 120)
        shift = np.median(a[ring][:, :3] - b[ring][:, :3], axis=0) if ring.sum() > 30 else np.zeros(3)
        fixed = np.asarray(base).copy()
        k = (np.asarray(m).astype(np.float32) / 255)[..., None]
        src = np.clip(b[..., :3] + shift, 0, 255)
        fixed[..., :3] = (fixed[..., :3] * (1 - k) + src * k).astype(np.uint8)
        return Image.fromarray(fixed), "gemini mouthless"
    return _mouthless(base, mbox, mouth), "inpaint (no mouthless shot)"


def _iso_sprite(src: Image.Image, clean: Image.Image, box: Box, mouth: Box, *, sx: float = 1.0,
                sy: float = 1.0) -> tuple[Image.Image | None, float]:
    """The mouth alone, cut from ``src`` by a mask (pixels that differ from the mouthless ``clean`` head,
    connected to the detected mouth, holes filled), never a rectangle with the chin. Returns the sprite and
    the share of mouth ink in ``src`` that lies outside that mask (a second / ghost mouth in the cell)."""
    import cv2  # noqa: PLC0415

    from . import qa  # noqa: PLC0415

    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    S = np.asarray(src.convert("RGBA").crop(box)).astype(np.int16)
    M = np.asarray(clean.crop(box)).astype(np.int16)
    ok = (M[..., 3] > 128) & (S[..., 3] > 128)
    mx0, my0, mx1, my1 = mouth[0] - x0, mouth[1] - y0, mouth[2] - x0, mouth[3] - y0
    mw, mh = max(1, mx1 - mx0), max(1, my1 - my0)
    yy, xx = np.mgrid[0:h, 0:w]
    ell = ((xx - (mx0 + mx1) / 2) / (mw * 0.75)) ** 2 + ((yy - (my0 + my1) / 2 - mh * 0.3) / (mh * 1.3)) ** 2
    ring = ok & (ell > 1.6)
    shift = np.median(M[ring][:, :3] - S[ring][:, :3], axis=0) if ring.sum() > 30 else np.zeros(3)
    Sc = np.clip(S[..., :3] + shift, 0, 255).astype(np.uint8)
    # sheet cells: white teeth keyed out with the background leave enclosed alpha holes -> fill them
    opq = (S[..., 3] > 128).astype(np.uint8)
    filled = opq.copy()
    cs, _hh = cv2.findContours(opq, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(filled, cs, -1, 1, thickness=cv2.FILLED)
    holes = ((filled > 0) & (opq == 0)).astype(np.uint8)
    if holes.any():
        Sc = cv2.inpaint(np.ascontiguousarray(Sc), holes, 3, cv2.INPAINT_TELEA)
        ok = ok | ((holes > 0) & (M[..., 3] > 128))
    diff = ((np.abs(Sc.astype(np.int16) - M[..., :3]).max(axis=2) > 28) & ok).astype(np.uint8)
    diff = cv2.morphologyEx(diff, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    diff = cv2.morphologyEx(diff, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, st, _c = cv2.connectedComponentsWithStats(diff, connectivity=8)
    cx0, cy0, cx1, cy1 = mx0 + mw * 0.1, my0 + mh * 0.1, mx1 - mw * 0.1, my1 - mh * 0.1
    keep = np.zeros_like(diff)
    hit = [i for i in range(1, n) if st[i][0] <= cx1 and st[i][0] + st[i][2] >= cx0 and st[i][1] <= cy1 and st[i][1] + st[i][3] >= cy0]
    top = max((st[i][4] for i in hit), default=0)
    for i in hit:  # the mouth, not a jaw / outline stroke of the cell that grazes the core at its side
        if i == max(hit, key=lambda j: st[j][4]) or (st[i][4] >= 0.03 * top and ell[int(_c[i][1]), int(_c[i][0])] <= 1):
            keep[lab == i] = 1
    keep[ell > 1] = 0  # never past the mouth ellipse (misaligned glasses / hair edges can join through close)
    if keep.sum() < 10:
        return None, 0.0
    cnts, _h = cv2.findContours(keep, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(keep, cnts, -1, 1, thickness=cv2.FILLED)
    ink = qa.ink_mask(Sc, qa.skin_of(M[..., :3][ok] if ok.any() else Sc.reshape(-1, 3)))
    near = cv2.dilate(keep, np.ones((9, 9), np.uint8)).astype(bool)
    raw_diff = (np.abs(Sc.astype(np.int16) - M[..., :3]).max(axis=2) > 28)
    zone = ((xx - (mx0 + mx1) / 2) / (mw * 0.75)) ** 2 + ((yy - (my0 + my1) / 2 - mh * 0.15) / (mh * 1.05)) ** 2 <= 1
    # a ghost mouth is a wide lip/mouth stroke left out of the cut; small specks and the short lip shadow are not
    left = (ink & raw_diff & ~near & ok & zone).astype(np.uint8)
    n2, lab2, st2, _c2 = cv2.connectedComponentsWithStats(left, connectivity=8)
    wide = sum(int(st2[i][4]) for i in range(1, n2) if st2[i][2] >= 0.35 * mw)
    dropped = float(wide) / max(1, int(keep.sum()))
    r = max(2, mw // 25)
    mask = cv2.dilate(keep * 255, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    pad = 3 * r
    mask[:pad] = 0; mask[-pad:] = 0; mask[:, :pad] = 0; mask[:, -pad:] = 0  # noqa: E702
    alpha = np.asarray(Image.fromarray(mask).filter(ImageFilter.GaussianBlur(r)))
    sprite = Image.fromarray(np.dstack([Sc, np.minimum(alpha, M[..., 3]).astype(np.uint8)]))
    if sx != 1.0 or sy != 1.0:
        bx = sprite.getchannel("A").getbbox()
        if bx:
            part = sprite.crop(bx)
            nw, nh = max(1, int(part.width * sx)), max(1, int(part.height * sy))
            out = Image.new("RGBA", sprite.size, (0, 0, 0, 0))
            px = bx[0] + (part.width - nw) // 2
            py = min(bx[1], h - pad - nh)
            out.paste(part.resize((nw, nh), Image.LANCZOS), (max(0, px), max(0, py)))
            sprite = out
    return sprite, round(dropped, 3)


def _cap_open(sprites: dict[str, Image.Image], under: Image.Image, face_h: int) -> dict[str, Image.Image]:
    """Natural amplitude: squash any viseme that opens past qa.OPEN_REL x the rest mouth (or qa.OPEN_ABS of
    the face) vertically, upper lip fixed, so a short "ah" never turns into a scream."""
    from . import qa  # noqa: PLC0415

    u = np.asarray(under.convert("RGBA"))
    rest = max(1, qa.open_height(np.asarray(sprites["X"]), u))
    out = dict(sprites)
    for v, spr in sprites.items():
        a = np.asarray(spr)
        h = qa.open_height(a, u)
        cap = min(max(qa.OPEN_REL.get(v, 1.7) * rest, qa.OPEN_FLOOR * face_h), qa.OPEN_ABS * face_h)
        if h <= cap or h == 0:
            continue
        bb = spr.getchannel("A").getbbox()
        if not bb:
            continue
        k = cap / h
        part = spr.crop(bb)
        nh = max(1, round(part.height * k))
        img = Image.new("RGBA", spr.size, (0, 0, 0, 0))
        img.paste(part.resize((part.width, nh), Image.LANCZOS), (bb[0], bb[1] + round((part.height - nh) * 0.15)))
        out[v] = img
    return out


def assemble(skin: Path, shots: dict[str, Path], boxes: dict[str, Box], url: Callable[[Path], str], *,
             matte: Callable[[Image.Image], Image.Image], front: bool = False,
             sheet: Path | None = None) -> dict[str, Any]:
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
    face, mouth = boxes["face"], _refine_mouth(base, boxes["mouth"])
    mbox = _grow(mouth, size, 0.6, 0.7, 1.3)  # open jaws drop down
    mbox = (mbox[0], mbox[1], mbox[2], min(mbox[3], max(face[3] + 6, mouth[3] + 4)))
    mbox = _union(mbox, _grow(mouth, size, 0.3, 0.3, 0.6))
    clean, head_src = _clean_base(base, shots, "mouthless", mbox, mouth)
    x_sprite, _ = _iso_sprite(base, clean, mbox, mouth)
    if x_sprite is None:  # nothing to cut (mouthless edit kept the mouth): soft-ellipse sprite of the master
        x_sprite = _mouth_sprite(base, None, mbox, mouth)
        lips = mouth
    else:  # the real lips (detector boxes are loose, reach the nose): every other mouth is cut around them,
        lb = x_sprite.getchannel("A").point(lambda q: 255 if q > 128 else 0).getbbox() or (0, 0, 1, 1)
        lw, lh = lb[2] - lb[0], lb[3] - lb[1]  # open jaws drop down: at least 0.6 x width tall, mostly below
        lips = (mbox[0] + lb[0], mbox[1] + lb[1] - lh // 4, mbox[0] + lb[2], mbox[1] + lb[1] + max(lh, int(lw * 0.6)))
    warp = {"X": (1, 1), "A": (1, 1), "B": (1, 1.06), "C": (1, 1.12), "D": (1.02, 1.25), "E": (0.92, 1.15),
            "F": (0.82, 1.05), "G": (1, 1), "H": (1, 1.08)}

    def scaled(v: str) -> Image.Image:
        sx_, sy_ = warp[v]
        bx = x_sprite.getchannel("A").getbbox()
        if not bx or (sx_, sy_) == (1, 1):
            return x_sprite
        part = x_sprite.crop(bx)
        nw, nh = max(1, int(part.width * sx_)), max(1, int(part.height * sy_))
        out = Image.new("RGBA", x_sprite.size, (0, 0, 0, 0))
        out.paste(part.resize((nw, nh), Image.LANCZOS), (bx[0] + (part.width - nw) // 2, bx[1]))
        return out

    sprites = {v: scaled(v) for v in VISEMES}
    ghost: dict[str, float] = {}
    for name, shapes in (("open", "CDH"), ("round", "EF")):
        if aligned[name] is not None:
            got, g = _iso_sprite(aligned[name], clean, mbox, lips)
            if got is not None:
                sprites |= {v: got for v in shapes}
                ghost |= {v: g for v in shapes}
    parts_from_sheet: dict[str, Any] | None = None
    if sheet is not None and sheet.exists():
        from . import partsheet  # noqa: PLC0415
        with Image.open(shots["closed"]) as src:  # register on the unmatted art: the matte's empty alpha skews NCC
            ref_art = src.convert("RGB") if src.size == base.size else base
        parts_from_sheet = partsheet.load(sheet, ref_art, boxes)
    good = set(parts_from_sheet.get("good", [])) if parts_from_sheet else set()
    if parts_from_sheet:  # per-cell: only cells that registered well replace the fallback mouths
        ms = parts_from_sheet["mouths"]
        for v in "ABCDEFGH":
            if v in good:
                got, g = _iso_sprite(ms[v], clean, mbox, lips)
                if got is not None and g <= 0.05:  # a cell with a second mouth stroke is dropped, fallback kept
                    sprites[v], ghost[v] = got, g
                elif got is not None:
                    ghost[f"{v}_rejected"] = g
    sprites = _cap_open(sprites, clean.crop(mbox), face[3] - face[1])
    skin_rgb, ink = _skin_and_ink(base, face)
    raw_eye_boxes = [boxes[k] for k in ("left_eye", "right_eye")]
    eyes_boxes = [_grow(b, size, 0.18, 0.12, 0.25) for b in raw_eye_boxes]
    eyes_open = Image.new("RGBA", size, (0, 0, 0, 0))
    for b in eyes_boxes:
        patch = base.crop(b)
        patch.putalpha(Image.fromarray(np.minimum(np.asarray(patch.getchannel("A")), np.asarray(_feather_ellipse(b[2] - b[0], b[3] - b[1], 0.18)))))
        eyes_open.alpha_composite(patch, (b[0], b[1]))
    eyes_closed = _eyes_closed(base, eyes_boxes, skin_rgb, ink)
    if parts_from_sheet and "eyes_closed" in good:
        eyes_closed = _sheet_lids(base, parts_from_sheet["eyes"]["eyes_closed"], eyes_boxes) or eyes_closed
    eye = _eye_parts(base, eyes_boxes, skin_rgb, ink)
    if parts_from_sheet and "eyes_half" in good:  # Gemini's drawn half lids, registered, over the drawn fallback
        eye["eyes_half"] = _sheet_lids(base, parts_from_sheet["eyes"]["eyes_half"], eyes_boxes) or eye["eyes_half"]
    brows, clean = _eyebrows(clean, raw_eye_boxes)
    chin = max(face[3], mouth[3]) + int((face[3] - face[1]) * 0.04)
    torso, neck, head = _slice_bust(clean, face, chin)
    parts = {
        "size": size,
        "torso": _crop_layer(torso),
        "neck": _crop_layer(neck),
        "head": _crop_layer(head),
        "eyes": _crop_layer(eyes_open),
        "eyes_closed": _crop_layer(eyes_closed),
        "eye_white": _crop_layer(eye["eye_white"]),
        "iris": _crop_layer(eye["iris"]),
        "eye_socket": _crop_layer(eye["eye_socket"]),
        "eyes_half": _crop_layer(eye["eyes_half"]),
        "brows": _crop_layer(brows),
        "eyeRig": eye["meta"],
        "visemes": {v: (img, (mbox[0], mbox[1])) for v, img in sprites.items()},
        "mouthQA": {"head": head_src, "cellGhost": ghost, "mouthBox": list(mbox), "mouth": list(mouth)},
    }
    (skin / ("sheet_front.json" if front else "sheet.json")).write_text(json.dumps(
        {"used": sorted(good), "ncc": parts_from_sheet.get("cellNcc") if parts_from_sheet else None}), encoding="utf-8")
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
    """One call, generate-then-verify: Gemini master + parts sheet + front, each gated and re-drawn within
    the cap; BiRefNet mattes; layered rig in 3 views; then the QA gates (:mod:`qa`) score the rig, a failed
    skin/edge gate on sheet mouths rebuilds with the deterministic mouths, and ``qa.json`` + ``qa_sheet.png``
    are stored with the character."""
    from . import qa  # noqa: PLC0415

    reset_budget()
    attempts: dict[str, int] = {}
    gen = generate_shots(subject, skin, raw_prompt=raw_prompt, style=style, attempts=attempts)
    boxes = gen["boxes"]
    sheet = gen["shots"].get("sheet")
    matte = BiRefNetMatte()
    status = {"left": "ready", "right": "ready (mirror of left)", "front": "ready (left art; no frontal shot)"}
    try:
        views = assemble(skin, gen["shots"], boxes, url, matte=matte, sheet=sheet)
        fr = gen["shots"].get("front")
        fshots = {"closed": fr} | ({"mouthless": gen["shots"]["front_mouthless"]} if "front_mouthless" in gen["shots"] else {})
        if fr:
            try:
                views |= assemble(skin, fshots, gen.get("frontBoxes") or detect_features(fr), url, matte=matte,
                                  front=True, sheet=gen["shots"].get("sheet_front") or sheet)
                status["front"] = "ready (gemini front view, side as reference; sheet mouths where cells register)"
            except Exception as exc:  # noqa: BLE001 — detection/cut failure keeps the side art
                _LOG.warning("front view rig failed: %s", exc)
                status["front"] = f"ready (left art; front rig failed: {str(exc)[:120]})"
        report = qa.finalize(skin, spent_usd(), attempts)
        if sheet is not None and not (report["gates"]["skin"]["pass"] and report["gates"]["edges"]["pass"]):
            attempts["sheetFallback"] = 1  # free retry: deterministic mouths/lids instead of the sheet
            views |= assemble(skin, gen["shots"], boxes, url, matte=matte)
            if fr:
                views |= assemble(skin, fshots, gen.get("frontBoxes") or detect_features(fr), url, matte=matte, front=True)
            gen["notes"]["sheet"] = gen["notes"].get("sheet", "") + " | QA fallback to warped mouths"
            report = qa.finalize(skin, spent_usd(), attempts)
    finally:
        matte.close()
    views.setdefault("front", views["left"])
    return {"views": views, "viewStatus": status, "mouthShots": {"sheet": gen["notes"].get("sheet", "none"), "sheetFront": gen["notes"].get("sheet_front", "none")},
            "frontShot": gen["notes"].get("front", "skipped"),
            "qa": {"score": report["score"], "costUsd": report["costUsd"], "attempts": attempts,
                   "failed": [k for k, g in report["gates"].items() if not g["pass"]]}}


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
        d.line((x - 26, 290, x, 284, x + 26, 290), fill=(65, 42, 35, 255), width=4, joint="curve")
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
