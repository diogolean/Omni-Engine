"""Offline stick rig used only when no image provider is configured.

Production stick figures are drawn by the image model through
:func:`products.animind_core.avatar.create_avatar` (the Aiwake path). This
module remains the no-key mock so tests and a machine without credentials
still return a contract-v2 rig. It writes a
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

from . import mouthset

VISEMES = "XABCDEFGH"
CANVAS = (640, 800)
SCALE = 4
PRESETS: dict[str, dict[str, Any]] = {
    "teacher": {
        "label": "Teacher", "skin": (244, 218, 190), "outfit": (47, 92, 146),
        "accessory": "glasses", "head": "oval", "brow": "arch", "build": "upright",
    },
    "sage": {
        "label": "Sage", "skin": (181, 132, 94), "outfit": (98, 72, 126),
        "accessory": "beard", "head": "round", "brow": "soft", "build": "broad",
    },
    "rebel": {
        "label": "Rebel", "skin": (225, 174, 136), "outfit": (155, 52, 58),
        "accessory": "bandana", "head": "angular", "brow": "blade", "build": "lean",
    },
    # Kept for API compatibility; Phase 4's three authored hero presets are
    # teacher, sage and rebel.
    "explorer": {
        "label": "Explorer", "skin": (235, 194, 151), "outfit": (65, 119, 91),
        "accessory": "hat", "head": "oval", "brow": "soft", "build": "upright",
    },
}


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


def _parts(
    colour: tuple[int, int, int],
    outfit: tuple[int, int, int],
    accessory: str,
    *,
    head_shape: str = "oval",
    brow_style: str = "arch",
    build: str = "upright",
) -> dict[str, Any]:
    k = SCALE
    ink = (35, 38, 44, 255)
    skin = (*colour, 255)

    def draw_body(d: ImageDraw.ImageDraw) -> None:
        # Aiwake's portrait contract uses shoulders at least 1.6x the head
        # width. Preserve that strong bust silhouette even though the limbs
        # remain economical stick strokes.
        shoulders = {"broad": (125, 515), "lean": (170, 470)}.get(build, (145, 495))
        d.polygon(
            ((shoulders[0]*k, 386*k), (shoulders[1]*k, 386*k), (386*k, 548*k), (254*k, 548*k)),
            fill=(*outfit, 238), outline=ink, width=5*k,
        )
        d.line((320*k, 374*k, 320*k, 704*k), fill=ink, width=11*k)
        d.line((320*k, 696*k, 230*k, 790*k), fill=ink, width=11*k)
        d.line((320*k, 696*k, 410*k, 790*k), fill=ink, width=11*k)

    def draw_neck(d: ImageDraw.ImageDraw) -> None:
        d.rounded_rectangle((294*k, 310*k, 346*k, 398*k), 16*k, fill=skin, outline=ink, width=5*k)
        d.arc((278*k, 365*k, 362*k, 412*k), 195, 345, fill=(240, 240, 235, 210), width=5*k)

    body = _vector(draw_body)
    neck = _vector(draw_neck)
    arm_left = _vector(lambda d: d.line((215*k, 404*k, 157*k, 536*k, 202*k, 578*k),
                                        fill=ink, width=11*k, joint="curve"))
    arm_right = _vector(lambda d: d.line((425*k, 404*k, 483*k, 536*k, 438*k, 578*k),
                                         fill=ink, width=11*k, joint="curve"))

    def draw_head(d: ImageDraw.ImageDraw) -> None:
        if head_shape == "round":
            d.ellipse((202*k, 92*k, 438*k, 334*k), fill=skin, outline=ink, width=7*k)
        elif head_shape == "angular":
            points = ((225*k, 96*k), (411*k, 108*k), (444*k, 195*k), (416*k, 310*k),
                      (320*k, 341*k), (216*k, 307*k), (195*k, 194*k))
            d.polygon(points, fill=skin)
            d.line((*points, points[0]), fill=ink, width=7*k, joint="curve")
        else:
            d.ellipse((207*k, 88*k, 433*k, 340*k), fill=skin, outline=ink, width=7*k)

    head = _vector(draw_head)
    eye_white = _vector(lambda d: (
        d.ellipse((252*k, 190*k, 302*k, 234*k), fill=(253, 253, 250, 255)),
        d.ellipse((338*k, 190*k, 388*k, 234*k), fill=(253, 253, 250, 255)),
    ))
    iris = _vector(lambda d: (
        d.ellipse((267*k, 200*k, 289*k, 226*k), fill=ink),
        d.ellipse((353*k, 200*k, 375*k, 226*k), fill=ink),
    ))
    socket = _vector(lambda d: (
        d.ellipse((252*k, 190*k, 302*k, 234*k), fill=(255, 255, 255, 255)),
        d.ellipse((338*k, 190*k, 388*k, 234*k), fill=(255, 255, 255, 255)),
    ))

    def lids(level: str) -> Image.Image:
        def paint(d: ImageDraw.ImageDraw) -> None:
            for x0, x1 in ((252, 302), (338, 388)):
                if level == "half":
                    d.pieslice((x0*k, 183*k, x1*k, 237*k), 180, 360, fill=skin)
                    y = 214
                else:
                    d.ellipse((x0*k, 187*k, x1*k, 237*k), fill=skin)
                    y = 214
                # Warm, low-contrast lash: readable without a hard black lid.
                d.arc((x0*k, (y-8)*k, x1*k, (y+10)*k), 190, 350,
                      fill=(126, 112, 104, 190), width=1*k)
        return _vector(paint)

    def brow(left: bool) -> Image.Image:
        x0, x1 = ((248, 304) if left else (336, 392))
        def paint(d: ImageDraw.ImageDraw) -> None:
            tone = (68, 61, 58, 220)
            if brow_style == "blade":
                points = ((x0*k, 176*k), (x1*k, 170*k), ((x1-2)*k, 176*k), ((x0+4)*k, 181*k))
            elif brow_style == "soft":
                points = ((x0*k, 178*k), ((x0+24)*k, 169*k), (x1*k, 176*k),
                          ((x1-3)*k, 181*k), ((x0+25)*k, 175*k), ((x0+3)*k, 183*k))
            else:
                points = ((x0*k, 180*k), ((x0+27)*k, 166*k), (x1*k, 177*k),
                          ((x1-2)*k, 182*k), ((x0+28)*k, 173*k), ((x0+4)*k, 185*k))
            d.polygon(points, fill=tone)
        return _vector(paint)

    def draw_accessory(d: ImageDraw.ImageDraw) -> None:
        if accessory == "glasses":
            for x0, x1 in ((246, 307), (333, 394)):
                d.rounded_rectangle((x0*k, 183*k, x1*k, 240*k), 12*k, outline=ink, width=3*k)
            d.line((307*k, 207*k, 333*k, 207*k), fill=ink, width=3*k)
        elif accessory == "hat":
            d.pieslice((210*k, 48*k, 430*k, 174*k), 180, 360, fill=(*outfit, 255), outline=ink, width=4*k)
            d.line((182*k, 112*k, 458*k, 112*k), fill=ink, width=8*k)
        elif accessory == "bandana":
            d.line((207*k, 139*k, 433*k, 128*k), fill=(*outfit, 255), width=11*k)
            d.polygon(((431*k, 128*k), (477*k, 105*k), (453*k, 157*k)), fill=(*outfit, 255))
        elif accessory == "beard":
            d.arc((247*k, 252*k, 393*k, 333*k), 0, 180, fill=(78, 65, 58, 220), width=8*k)

    colours = {
        "outline": ink, "cavity": (42, 30, 34, 255), "teeth": (247, 244, 236, 255),
        "tongue": (190, 86, 92, 255), "lip": (142, 72, 78, 255),
    }
    mouths = mouthset.place((270, 246, 370, 311), mouthset.draw_set(90, colours, open_cap=42))
    expressions = mouthset.place(
        (270, 246, 370, 311),
        mouthset.draw_expressions(90, colours, open_cap=42),
    )

    return {
        "body": _crop(body), "neck": _crop(neck), "arm_left": _crop(arm_left), "arm_right": _crop(arm_right),
        "head": _crop(head),
        "eye_white": _crop(eye_white), "iris": _crop(iris), "eye_socket": _crop(socket),
        "eyes_half": _crop(lids("half")), "eyes_closed": _crop(lids("closed")),
        "brow_left": _crop(brow(True)), "brow_right": _crop(brow(False)),
        "accessory": _crop(_vector(draw_accessory)),
        "mouths": mouths,
        "expressions": expressions,
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
        ("body", 0), ("arm_left", 1), ("arm_right", 1), ("neck", 1), ("head", 2), ("eye_white", 3),
        ("iris", 4), ("eye_socket", 4), ("eyes_half", 5), ("eyes_closed", 6),
        ("brow_left", 7), ("brow_right", 7), ("accessory", 8),
    )
    for name, z in order:
        image, xy = place(parts[name])
        layers.append(_save_layer(name, image, xy, z, folder, url))
    visemes = {}
    for code, pair in parts["mouths"].items():
        image, xy = place(pair)
        visemes[code] = _save_layer(f"mouth_{code}", image, xy, 10, folder / "mouths", url)
    expressions = {}
    for emotion, pair in parts["expressions"].items():
        image, xy = place(pair)
        expressions[emotion] = _save_layer(
            f"expression_{emotion}", image, xy, 10, folder / "expressions", url
        )
    view = {
        "canvas": {"w": CANVAS[0], "h": CANVAS[1]},
        "layers": layers,
        "visemes": visemes,
        "expressions": expressions,
        "eyeRig": {"maxShift": [10, 6], "irises": [[278, 213, 12], [364, 213, 12]]},
        "procedural": True,
    }
    local = json.loads(json.dumps(view))
    for layer in local["layers"]:
        layer["url"] = f"{layer['name']}.png"
    for code, layer in local["visemes"].items():
        layer["url"] = f"mouths/{layer['name']}.png"
    for emotion, layer in local["expressions"].items():
        layer["url"] = f"expressions/{layer['name']}.png"
    (folder / "view.json").write_text(json.dumps(local, indent=2), encoding="utf-8")
    return view


def build(
    skin: Path,
    url: Callable[[Path], str],
    colour: tuple[int, int, int] | None = None,
    *,
    preset: str = "teacher",
    outfit: tuple[int, int, int] | None = None,
    accessory: str | None = None,
) -> dict[str, Any]:
    """Build front/left/right contract-v2 views without network or model calls."""
    preset = preset if preset in PRESETS else "teacher"
    spec = PRESETS[preset]
    colour = colour or spec["skin"]
    outfit = outfit or spec["outfit"]
    accessory = accessory if accessory is not None else str(spec["accessory"])
    parts = _parts(
        colour, outfit, accessory,
        head_shape=str(spec["head"]), brow_style=str(spec["brow"]), build=str(spec["build"]),
    )
    views = {
        "front": _write_view(parts, skin / "views" / "front", url, mirror=False),
        "left": _write_view(parts, skin / "views" / "left", url, mirror=False),
        "right": _write_view(parts, skin / "views" / "right", url, mirror=True),
    }
    # Compatibility files used by the existing loader and review-sheet code.
    meta = {
        "type": "stick", "size": CANVAS,
        "boxes": {"face": [195, 88, 444, 341], "left_eye": [252, 190, 302, 234],
                  "right_eye": [338, 190, 388, 234], "mouth": [270, 246, 370, 311]},
        "mouthBox": [248, 238, 392, 326],
    }
    (skin / "rig.json").write_text(json.dumps(meta), encoding="utf-8")
    (skin / "rig_front.json").write_text(json.dumps(meta), encoding="utf-8")
    preview = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    for name in ("body", "arm_left", "arm_right", "neck", "head", "eye_white", "iris",
                 "brow_left", "brow_right", "accessory"):
        image, xy = parts[name]
        preview.alpha_composite(image, xy)
    preview.alpha_composite(*parts["mouths"]["X"])
    preview.save(skin / "character.png", "PNG", optimize=True)
    return {
        "views": views,
        "viewStatus": {"front": "procedural", "left": "procedural", "right": "procedural mirror"},
        "mouthShots": {"source": "procedural Rhubarb X/A-H"},
        "frontShot": "procedural",
        "preset": preset,
        "customization": {"skin": colour, "outfit": outfit, "accessory": accessory},
        "qa": {"score": 100, "costUsd": 0.0, "attempts": {}, "failed": []},
    }
