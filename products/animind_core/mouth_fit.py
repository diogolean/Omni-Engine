"""Seat and size a saved mouth set so the approval gates pass on the way out.

``stick.build`` and the illustrated portrait builder call :func:`apply`. A
hairline rest used to pass only after ``approval.legalize_mouths`` grew X
until it was as tall as the vowels, so an open mouth read as about 1.04× rest.
The rest lip stays a lip. Vowels sit near the top of ``qa.OPEN_REL`` and under
``qa.EXPRESSION_OPEN_ABS + qa.OPEN_SLACK``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from . import mouth_anchor, qa

# Rest lip as a share of the face. Thick enough to read, thin enough that a
# vowel at the top of OPEN_REL stays under the expression cap (0.125).
REST_SHARE = 0.072
# open/rest targets. Each one is under OPEN_REL + 0.05.
CINEMA_RATIO = {
    "B": 1.22,
    "C": 1.40,
    "D": 1.55,
    "E": 1.48,
    "F": 1.36,
    "G": 1.28,
    "H": 1.34,
}
# Vowels may not use the scream cap. Stay under the expression slack line.
VOWEL_ABS = qa.EXPRESSION_OPEN_ABS + qa.OPEN_SLACK - 0.006


def _clamp(factor: float) -> float:
    return max(0.45, min(2.4, factor))


def _scale_ink(image: Image.Image, factor: float) -> Image.Image:
    if factor <= 0 or abs(factor - 1.0) < 0.03:
        return image
    box = image.getchannel("A").getbbox()
    if not box:
        return image
    part = image.crop(box).resize(
        (max(1, box[2] - box[0]), max(1, int(round((box[3] - box[1]) * factor)))),
        Image.Resampling.LANCZOS,
    )
    pad = 6
    canvas = Image.new("RGBA", (part.width + 2 * pad, part.height + 2 * pad), (0, 0, 0, 0))
    canvas.paste(part, (pad, pad), part)
    return canvas


def _reseat(skin: Path, name: str) -> None:
    folder = skin / "views" / name
    view = json.loads((folder / "view.json").read_text(encoding="utf-8"))
    boxes = mouth_anchor._boxes(folder, view, skin, name)
    if not boxes:
        return
    face, left_eye, right_eye = boxes
    anchor = mouth_anchor.place(mouth_anchor._paste_head(folder, view), face, left_eye, right_eye, view=name)
    sprites: dict[str, Image.Image] = {}
    layers: dict[str, dict[str, Any]] = {}
    for family in ("visemes", "expressions"):
        for key, layer in (view.get(family) or {}).items():
            token = f"{family}:{key}"
            sprites[token] = Image.open(folder / layer["url"]).convert("RGBA")
            layers[token] = layer
    for token, (image, xy) in mouth_anchor.seat(sprites, anchor).items():
        layer = layers[token]
        image.save(folder / layer["url"], "PNG")
        layer["x"], layer["y"] = int(xy[0]), int(xy[1])
        layer["w"], layer["h"] = image.size
    (folder / "view.json").write_text(json.dumps(view, indent=2), encoding="utf-8")


def _face_for(skin: Path, name: str) -> list[int]:
    rig_name = "rig_front.json" if name == "front" and (skin / "rig_front.json").is_file() else "rig.json"
    return list(json.loads((skin / rig_name).read_text(encoding="utf-8"))["boxes"]["face"])


def _retarget(folder: Path, family: str, key: str, factor: float) -> None:
    view = json.loads((folder / "view.json").read_text(encoding="utf-8"))
    layer = (view.get(family) or {}).get(key)
    if layer is None or abs(factor - 1.0) < 0.03:
        return
    image = _scale_ink(Image.open(folder / layer["url"]).convert("RGBA"), factor)
    image.save(folder / layer["url"], "PNG")
    layer["w"], layer["h"] = image.size
    (folder / "view.json").write_text(json.dumps(view, indent=2), encoding="utf-8")


def _share_canvas(folder: Path) -> None:
    """One canvas for every viseme so the mouth-range gate can compare them."""
    view = json.loads((folder / "view.json").read_text(encoding="utf-8"))
    visemes = view.get("visemes") or {}
    if len(visemes) < 2:
        return
    images = {code: Image.open(folder / layer["url"]).convert("RGBA") for code, layer in visemes.items()}
    width = max(image.width for image in images.values())
    height = max(image.height for image in images.values())
    for code, image in images.items():
        if image.size == (width, height):
            continue
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
        layer = visemes[code]
        canvas.save(folder / layer["url"], "PNG")
        layer["w"], layer["h"] = canvas.size
    (folder / "view.json").write_text(json.dumps(view, indent=2), encoding="utf-8")


def _fill_mouth_box(skin: Path) -> None:
    """Record the mouth rectangle ``double_mouth`` reads next to the anchor."""
    for name in ("left", "front", "right"):
        folder = skin / "views" / name
        path = folder / "view.json"
        if not path.is_file():
            continue
        view = json.loads(path.read_text(encoding="utf-8"))
        boxes = mouth_anchor._boxes(folder, view, skin, name)
        if not boxes:
            continue
        face, left_eye, right_eye = boxes
        anchor = mouth_anchor.place(mouth_anchor._paste_head(folder, view), face, left_eye, right_eye, view=name)
        meta = view.setdefault("mouthQA", {})
        meta["mouth"] = list(anchor.as_box(face, max(12, int((int(face[2]) - int(face[0])) * 0.34))))
        meta["face"] = [int(v) for v in face]
        path.write_text(json.dumps(view, indent=2), encoding="utf-8")


def _close_enough(current: float, target: float) -> bool:
    return abs(current - target) <= max(0.006, 0.06 * target)


def _cinema_pass(folder: Path, face: list[int]) -> None:
    """Scale X to a lip, then each vowel toward its cinema ratio."""
    face_h = max(1, int(face[3]) - int(face[1]))
    skin = folder.parent.parent
    for _ in range(8):
        gate = qa.mouth_open(folder, face)
        shares = gate.get("faceShare") or {}
        x_share = float(shares.get("X") or 0.0)
        x_px = max(1.0, x_share * face_h)
        target_x = REST_SHARE * face_h
        if abs(x_px - target_x) > max(2.0, 0.12 * target_x):
            _retarget(folder, "visemes", "X", _clamp(target_x / x_px))
            _reseat(skin, folder.name)
            continue
        moved = False
        for code, ratio in CINEMA_RATIO.items():
            share = float(shares.get(code) or 0.0)
            if share <= 0:
                continue
            target_share = min(
                ratio * (x_px / face_h),
                VOWEL_ABS,
                (qa.OPEN_REL.get(code, 1.7) + 0.04) * (x_px / face_h),
                qa.OPEN_ABS - 0.005,
            )
            if _close_enough(share, target_share):
                continue
            _retarget(folder, "visemes", code, _clamp(target_share / share))
            moved = True
        cap = qa.EXPRESSION_OPEN_ABS * 0.90
        for key, share in (gate.get("expressionFaceShare") or {}).items():
            if float(share) > cap:
                _retarget(folder, "expressions", key, _clamp(cap / float(share)))
                moved = True
        if not moved:
            return
        _reseat(skin, folder.name)


def _safety_pass(folder: Path, face: list[int]) -> None:
    """Shrink anything still past the gate. Does not grow X up to the vowel."""
    skin = folder.parent.parent
    for _ in range(6):
        gate = qa.mouth_open(folder, face)
        if gate["pass"]:
            return
        shares = gate.get("faceShare") or {}
        ratios = gate.get("ratios") or {}
        moved = False
        for code, ratio in ratios.items():
            if code in "XA":
                continue
            limit = qa.OPEN_REL.get(code, 1.7) + 0.05
            if shares.get(code, 0) > qa.OPEN_FLOOR and float(ratio) > limit:
                _retarget(folder, "visemes", code, _clamp((limit * 0.92) / float(ratio)))
                moved = True
            if shares.get(code, 0) > qa.OPEN_ABS + qa.OPEN_SLACK:
                _retarget(folder, "visemes", code, _clamp((qa.OPEN_ABS * 0.9) / float(shares[code])))
                moved = True
        for key, share in (gate.get("bad") or {}).items():
            if str(key).startswith("expression:"):
                _retarget(
                    folder, "expressions", str(key).split(":", 1)[1],
                    _clamp(min(0.92, (qa.EXPRESSION_OPEN_ABS * 0.90) / max(float(share), 1e-3))),
                )
                moved = True
        if not moved:
            return
        _reseat(skin, folder.name)


def apply(skin: Path) -> dict[str, Any]:
    """Move every mouth onto the nose–chin anchor and open the vowels.

    Returns ``{view: mouthOpen passed}``.
    """
    mouth_anchor.reposition_skin(skin)
    notes: dict[str, Any] = {}
    for name in ("left", "front", "right"):
        folder = skin / "views" / name
        if not (folder / "view.json").is_file():
            continue
        face = _face_for(skin, name)
        _cinema_pass(folder, face)
        _safety_pass(folder, face)
        _share_canvas(folder)
        _reseat(skin, name)
        notes[name] = bool(qa.mouth_open(folder, face)["pass"])
    mouth_anchor.reposition_skin(skin)
    _fill_mouth_box(skin)
    # Last, so a later scale cannot put lip colour back into the fringe.
    for name in ("left", "front", "right"):
        folder = skin / "views" / name
        if (folder / "view.json").is_file():
            qa.match_skin_rim(folder)
    return notes
