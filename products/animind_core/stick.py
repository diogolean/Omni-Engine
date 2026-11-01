"""Procedural stick-character rig.

The stick type is deliberately independent of image generation.  It writes a
contract-v2 rig made from anti-aliased vector primitives: separate body,
arms, mouthless head, movable pupils, soft lids, delicate brows, and the nine
canonical Rhubarb mouth shapes.  The regular scene compositor then supplies
breathing, head motion, gaze, blinking, emotion poses, and hard-cut lip sync.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw

VISEMES = "XABCDEFGH"
CANVAS = (640, 800)
SCALE = 4


def _vector(draw: Callable[[ImageDraw.ImageDraw], None]) -> Image.Image:
    image = Image.new("RGBA", (CANVAS[0] * SCALE, CANVAS[1] * SCALE), (0, 0, 0, 0))
    draw(ImageDraw.Draw(image))
    return image.resize(CANVAS, Image.Resampling.LANCZOS)


def _crop(image: Image.Image) -> tuple[Image.Image, tuple[int, int]]:
    box = image.getchannel("A").getbbox() or (0, 0, 1, 1)
    return image.crop(box), (box[0], box[1])


def _save_layer(
    name: str,
    image: Image.Image,
    xy: tuple[int, int],
    z: int,
    folder: Path,
    url: Callable[[Path], str],
) -> dict[str, Any]:
    path = folder / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)
    return {"name": name, "url": url(path), "x": xy[0], "y": xy[1], "w": image.width, "h": image.height, "z": z}


def _mouth(code: str, ink: tuple[int, int, int, int]) -> Image.Image:
    """One restrained, clean mouth on a shared 144x88 canvas."""
    size = (144 * SCALE, 88 * SCALE)
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    cx, cy = 72 * SCALE, 43 * SCALE
    widths = {"X": 72, "A": 58, "B": 78, "C": 88, "D": 82, "E": 44, "F": 30, "G": 76, "H": 64}
    heights = {"X": 4, "A": 3, "B": 14, "C": 24, "D": 34, "E": 30, "F": 20, "G": 17, "H": 27}
    w, h = widths[code] * SCALE, heights[code] * SCALE
    if code in "XA":
        bow = (3 if code == "X" else 0) * SCALE
        d.arc((cx - w // 2, cy - 8 * SCALE - bow, cx + w // 2, cy + 8 * SCALE), 18, 162,
              fill=ink, width=3 * SCALE)
    else:
        cavity = (42, 30, 34, 255)
        d.ellipse((cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2), fill=cavity, outline=ink,
                  width=3 * SCALE)
        if code in "BCG":
            d.rounded_rectangle((cx - w * 0.35, cy - h * 0.30, cx + w * 0.35, cy - h * 0.02),
                                radius=2 * SCALE, fill=(247, 244, 236, 255))
        if code == "H":
            d.ellipse((cx - w * 0.22, cy + h * 0.08, cx + w * 0.22, cy + h * 0.38),
                      fill=(190, 86, 92, 255))
    return image.resize((144, 88), Image.Resampling.LANCZOS)


def _parts(colour: tuple[int, int, int]) -> dict[str, Any]:
    k = SCALE
    ink = (35, 38, 44, 255)
    skin = (*colour, 255)
    body = _vector(lambda d: (
        d.line((320*k, 485*k, 320*k, 720*k), fill=ink, width=13*k),
        d.line((320*k, 710*k, 230*k, 790*k), fill=ink, width=13*k),
        d.line((320*k, 710*k, 410*k, 790*k), fill=ink, width=13*k),
    ))
    arm_left = _vector(lambda d: d.line((316*k, 535*k, 238*k, 630*k, 205*k, 590*k),
                                        fill=ink, width=12*k, joint="curve"))
    arm_right = _vector(lambda d: d.line((324*k, 535*k, 402*k, 630*k, 435*k, 590*k),
                                         fill=ink, width=12*k, joint="curve"))
    head = _vector(lambda d: d.ellipse((190*k, 150*k, 450*k, 470*k), fill=skin, outline=ink, width=9*k))
    eye_white = _vector(lambda d: (
        d.ellipse((248*k, 282*k, 298*k, 326*k), fill=(253, 253, 250, 255)),
        d.ellipse((342*k, 282*k, 392*k, 326*k), fill=(253, 253, 250, 255)),
    ))
    iris = _vector(lambda d: (
        d.ellipse((263*k, 292*k, 285*k, 318*k), fill=ink),
        d.ellipse((357*k, 292*k, 379*k, 318*k), fill=ink),
    ))
    socket = _vector(lambda d: (
        d.ellipse((248*k, 282*k, 298*k, 326*k), fill=(255, 255, 255, 255)),
        d.ellipse((342*k, 282*k, 392*k, 326*k), fill=(255, 255, 255, 255)),
    ))

    def lids(level: str) -> Image.Image:
        def paint(d: ImageDraw.ImageDraw) -> None:
            for x0, x1 in ((248, 298), (342, 392)):
                if level == "half":
                    d.pieslice((x0*k, 275*k, x1*k, 329*k), 180, 360, fill=skin)
                    y = 306
                else:
                    d.ellipse((x0*k, 279*k, x1*k, 329*k), fill=skin)
                    y = 306
                # Warm, low-contrast lash: readable without a hard black lid.
                d.arc((x0*k, (y-8)*k, x1*k, (y+10)*k), 190, 350,
                      fill=(126, 112, 104, 190), width=1*k)
        return _vector(paint)

    def brow(left: bool) -> Image.Image:
        x0, x1 = ((247, 299) if left else (341, 393))
        return _vector(lambda d: d.arc((x0*k, 252*k, x1*k, 286*k), 195, 345,
                                       fill=(68, 61, 58, 230), width=3*k))

    return {
        "body": _crop(body), "arm_left": _crop(arm_left), "arm_right": _crop(arm_right), "head": _crop(head),
        "eye_white": _crop(eye_white), "iris": _crop(iris), "eye_socket": _crop(socket),
        "eyes_half": _crop(lids("half")), "eyes_closed": _crop(lids("closed")),
        "brow_left": _crop(brow(True)), "brow_right": _crop(brow(False)),
        "mouths": {v: (_mouth(v, ink), (248, 365)) for v in VISEMES},
    }


def _write_view(
    parts: dict[str, Any],
    folder: Path,
    url: Callable[[Path], str],
    *,
    mirror: bool,
) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)

    def place(pair: tuple[Image.Image, tuple[int, int]]) -> tuple[Image.Image, tuple[int, int]]:
        image, xy = pair
        if not mirror:
            return image, xy
        return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (CANVAS[0] - xy[0] - image.width, xy[1])

    layers = []
    order = (
        ("body", 0), ("arm_left", 1), ("arm_right", 1), ("head", 2), ("eye_white", 3),
        ("iris", 4), ("eye_socket", 4), ("eyes_half", 5), ("eyes_closed", 6),
        ("brow_left", 7), ("brow_right", 7),
    )
    for name, z in order:
        image, xy = place(parts[name])
        layers.append(_save_layer(name, image, xy, z, folder, url))
    visemes = {}
    for code, pair in parts["mouths"].items():
        image, xy = place(pair)
        visemes[code] = _save_layer(f"mouth_{code}", image, xy, 10, folder / "mouths", url)
    view = {
        "canvas": {"w": CANVAS[0], "h": CANVAS[1]},
        "layers": layers,
        "visemes": visemes,
        "eyeRig": {"maxShift": [10, 6], "irises": [[274, 305, 12], [368, 305, 12]]},
        "procedural": True,
    }
    local = json.loads(json.dumps(view))
    for layer in local["layers"]:
        layer["url"] = f"{layer['name']}.png"
    for code, layer in local["visemes"].items():
        layer["url"] = f"mouths/{layer['name']}.png"
    (folder / "view.json").write_text(json.dumps(local, indent=2), encoding="utf-8")
    return view


def build(skin: Path, url: Callable[[Path], str], colour: tuple[int, int, int] = (244, 218, 190)) -> dict[str, Any]:
    """Build front/left/right contract-v2 views without network or model calls."""
    parts = _parts(colour)
    views = {
        "front": _write_view(parts, skin / "views" / "front", url, mirror=False),
        "left": _write_view(parts, skin / "views" / "left", url, mirror=False),
        "right": _write_view(parts, skin / "views" / "right", url, mirror=True),
    }
    # Compatibility files used by the existing loader and review-sheet code.
    meta = {
        "type": "stick", "size": CANVAS,
        "boxes": {"face": [190, 150, 450, 470], "left_eye": [248, 282, 298, 326],
                  "right_eye": [342, 282, 392, 326], "mouth": [270, 382, 370, 426]},
        "mouthBox": [248, 365, 392, 453],
    }
    (skin / "rig.json").write_text(json.dumps(meta), encoding="utf-8")
    (skin / "rig_front.json").write_text(json.dumps(meta), encoding="utf-8")
    preview = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    for name in ("body", "arm_left", "arm_right", "head", "eye_white", "iris", "brow_left", "brow_right"):
        image, xy = parts[name]
        preview.alpha_composite(image, xy)
    preview.alpha_composite(*parts["mouths"]["X"])
    preview.save(skin / "character.png", "PNG", optimize=True)
    return {
        "views": views,
        "viewStatus": {"front": "procedural", "left": "procedural", "right": "procedural mirror"},
        "mouthShots": {"source": "procedural Rhubarb X/A-H"},
        "frontShot": "procedural",
        "qa": {"score": 100, "costUsd": 0.0, "attempts": {}, "failed": []},
    }
