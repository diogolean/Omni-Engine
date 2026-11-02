"""Adapt an Aiwake ``puppet.json`` v3 into the animind rig.

Generation, the BiRefNet cutout, the landmark pass, and the head/body/collar
slicer stay in ``products.aiwake.animator.factory`` and
``animation.parametric.factory``. This module only seats that result.

Framing numbers come from the manifest (the same contract
``solve_puppet_matrix`` seats). The 25° three-quarter pose is already in the
plate. Any extra scale is applied once here, at load, and the scene never
rotates the puppet per frame.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw

from animation.parametric.factory.puppet_matrix import solve_puppet_matrix

from . import rig

_CANVAS = (1080, 1920)
_V3_FILES = (
    "puppet.json",
    "character.png",
    "head.png",
    "body.png",
    "collar.png",
    "eyes_open.png",
    "eyes_half.png",
    "eyes_blink.png",
    "source_original.png",
)


def avatar_prompt(description: str, *, stick: bool, facing: str) -> str:
    """Person or stick-figure variant of the Aiwake 9:16 cel prompt.

    The pose lock, the 27% head, the blank mouth, and the black stage are the
    same contract as ``PROMPT_DEEPSEEK_GHIBLI``.
    """
    who = " ".join((description or "a person").split())
    if stick:
        subject = (
            f"A single South Park stick-figure of {who}. Construction-paper cutout, "
            "flat unshaded colors, thick black contour, stick-figure. "
            "NO mouth, NO nose, NO human mouth, lower face one flat colour."
        )
    else:
        subject = (
            f"A human character: {who}. Studio Ghibli 1990s anime cel, clean flat shading, "
            "hand-drawn dark ink outlines. Keep any moustache or beard. The skin under the "
            "facial hair is blank even skin. NO human mouth, NO lips, NO smile creases, NO teeth."
        )
    side = "left" if facing == "left" else "right"
    return (
        f"{subject} Medium bust, STRICT POSE LOCK: exactly 25 degrees facing {side} "
        "in three-quarter view, never dead-front. "
        "STRICT PROPORTION: crown-to-chin head height is exactly 27 percent of the 9:16 "
        "portrait height; the torso fills the lower 73 percent. "
        "The head docks into a short collar and the opaque torso continues past the bottom edge. "
        "Solid pure black background. --ar 9:16"
    )


def snapshot(skin: Path, dest: Path) -> None:
    """Copy the v3 skin so a later failed plate cannot replace it."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in _V3_FILES:
        source = skin / name
        if source.is_file():
            shutil.copy2(source, dest / name)
    mouths = skin / "mouths"
    if mouths.is_dir():
        shutil.copytree(mouths, dest / "mouths")


def restore(src: Path, skin: Path) -> None:
    """Put a saved v3 skin back. Native pixels, not a reseated copy."""
    if not (src / "puppet.json").is_file():
        return
    skin.mkdir(parents=True, exist_ok=True)
    for name in _V3_FILES:
        source = src / name
        if source.is_file():
            shutil.copy2(source, skin / name)
    mouths = src / "mouths"
    if mouths.is_dir():
        target = skin / "mouths"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(mouths, target)


def _open(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGBA")


def _alpha_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    return image.getchannel("A").getbbox()


def _body_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    box = _alpha_bbox(image)
    if box is None:
        return (0, 0, image.width, image.height)
    return box


def _seat(manifest: dict[str, Any], body: Image.Image) -> tuple[float, tuple[int, int], tuple[int, int]]:
    """One uniform scale and one offset from the manifest's framing block."""
    framing = dict(manifest.get("framing") or {})
    anchors = manifest["anchors"]
    calibration = dict(manifest.get("calibration") or {})
    head_box = calibration.get("head_bbox") or [0, 0, body.width, max(1, body.height // 3)]
    head_h = max(1, int(head_box[3]) - int(head_box[1]))
    eye_y = (float(anchors["left_eye"][1]) + float(anchors["right_eye"][1])) / 2.0
    eye = ((float(anchors["left_eye"][0]) + float(anchors["right_eye"][0])) / 2.0, eye_y)
    canvas = manifest.get("canvas_size") or [body.width, body.height]
    matrix = solve_puppet_matrix(
        head_height=head_h,
        eye_center=eye,
        neck_pivot=(float(anchors["neck_pivot"][0]), float(anchors["neck_pivot"][1])),
        body_bbox=_body_bbox(body),
        canvas_size=(int(canvas[0]), int(canvas[1])),
    )
    # The slicer already docked the head into the collar. Seat the whole plate
    # once. A second body offset would pull that dock apart on every frame.
    target_eye = int(framing.get("target_eye_y") or 770)
    scale = float(matrix.head_scale)
    offset = (
        int(matrix.head_offset_x),
        int(round(target_eye - eye_y * scale)),
    )
    height = int(framing.get("body_anchor_y") or _CANVAS[1])
    return scale, offset, (_CANVAS[0], height)


def _bake(image: Image.Image, scale: float, offset: tuple[int, int], canvas: tuple[int, int]) -> Image.Image:
    resized = image.resize(
        (max(1, int(round(image.width * scale))), max(1, int(round(image.height * scale)))),
        Image.Resampling.LANCZOS,
    )
    plate = Image.new("RGBA", canvas, (0, 0, 0, 0))
    plate.paste(resized, offset, resized)
    return plate


def _place(image: Image.Image) -> tuple[Image.Image, tuple[int, int]]:
    box = _alpha_bbox(image)
    if box is None:
        return Image.new("RGBA", (2, 2), (0, 0, 0, 0)), (0, 0)
    return image.crop(box), (box[0], box[1])


def _point(x: float, y: float, scale: float, offset: tuple[int, int]) -> tuple[int, int]:
    return (int(round(x * scale + offset[0])), int(round(y * scale + offset[1])))


def _around(center: tuple[int, int], rx: int, ry: int) -> tuple[int, int, int, int]:
    return (center[0] - rx, center[1] - ry, center[0] + rx, center[1] + ry)


def _disc(canvas: tuple[int, int], center: tuple[int, int], radius: int, fill: tuple[int, int, int, int]) -> Image.Image:
    image = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(_around(center, radius, radius), fill=fill)
    return image


def _mouth_part(skin: Path, code: str, scale: float, offset: tuple[int, int], canvas: tuple[int, int]) -> tuple[Image.Image, tuple[int, int]]:
    path = skin / "mouths" / f"mouth_{code}.png"
    if not path.is_file():
        path = skin / "mouths" / "mouth_X.png"
    if not path.is_file():
        return Image.new("RGBA", (8, 8), (0, 0, 0, 0)), (0, 0)
    return _place(_bake(_open(path), scale, offset, canvas))


def adapt(skin: Path, url: Callable[[Path], str]) -> dict[str, Any]:
    """Build left, right, and front views from the v3 layers and their anchors."""
    manifest = json.loads((skin / "puppet.json").read_text(encoding="utf-8"))
    anchors = manifest["anchors"]
    body = _open(skin / "body.png")
    scale, offset, canvas = _seat(manifest, body)
    head = _bake(_open(skin / "head.png"), scale, offset, canvas)
    torso = _bake(body, scale, offset, canvas)
    collar_path = skin / "collar.png"
    collar = _bake(_open(collar_path), scale, offset, canvas) if collar_path.is_file() else Image.new("RGBA", canvas, (0, 0, 0, 0))
    if _alpha_bbox(collar) is None:
        neck_at = _point(float(anchors["neck_pivot"][0]), float(anchors["neck_pivot"][1]), scale, offset)
        collar = _disc(canvas, neck_at, 6, (40, 32, 28, 255))

    radius = max(4, int(round(float(anchors.get("eye_radius") or 8) * scale)))
    left_eye = _point(float(anchors["left_eye"][0]), float(anchors["left_eye"][1]), scale, offset)
    right_eye = _point(float(anchors["right_eye"][0]), float(anchors["right_eye"][1]), scale, offset)
    mouth_at = _point(float(anchors["mouth"][0]), float(anchors["mouth"][1]), scale, offset)
    whites = Image.new("RGBA", canvas, (0, 0, 0, 0))
    irises = Image.new("RGBA", canvas, (0, 0, 0, 0))
    sockets = Image.new("RGBA", canvas, (0, 0, 0, 0))
    brows = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw_brows = ImageDraw.Draw(brows)
    for center in (left_eye, right_eye):
        whites.alpha_composite(_disc(canvas, center, radius, (248, 248, 246, 255)))
        irises.alpha_composite(_disc(canvas, center, max(2, radius // 3), (20, 16, 14, 255)))
        sockets.alpha_composite(_disc(canvas, center, radius, (255, 255, 255, 255)))
        draw_brows.arc(
            (center[0] - radius, center[1] - radius - 4, center[0] + radius, center[1] - 2),
            200, 340, fill=(30, 22, 18, 255), width=3,
        )
    half_path = skin / "eyes_half.png"
    blink_path = skin / "eyes_blink.png"
    eyes_half = _bake(_open(half_path), scale, offset, canvas) if half_path.is_file() else whites
    eyes_closed = _bake(_open(blink_path), scale, offset, canvas) if blink_path.is_file() else whites

    head_box = (manifest.get("calibration") or {}).get("head_bbox") or [0, 0, body.width // 2, body.height // 2]
    face = (
        _point(head_box[0], head_box[1], scale, offset)[0],
        _point(head_box[0], head_box[1], scale, offset)[1],
        _point(head_box[2], head_box[3], scale, offset)[0],
        _point(head_box[2], head_box[3], scale, offset)[1],
    )
    eye_boxes = (_around(left_eye, radius, radius), _around(right_eye, radius, radius))
    mouth_w = max(12, int(radius * 2))
    mouth = _around(mouth_at, mouth_w // 2, max(6, radius // 2))
    face = (
        min(face[0], mouth[0], eye_boxes[0][0], eye_boxes[1][0]),
        min(face[1], eye_boxes[0][1], eye_boxes[1][1]),
        max(face[2], mouth[2], eye_boxes[0][2], eye_boxes[1][2]),
        max(face[3], mouth[3]),
    )
    visemes = {code: _mouth_part(skin, code, scale, offset, canvas) for code in rig.VISEMES}
    happy = _mouth_part(skin, "A", scale, offset, canvas)
    smug = skin / "mouths" / "mouth_smug_smile.png"
    if smug.is_file():
        happy = _place(_bake(_open(smug), scale, offset, canvas))
    parts = {
        "size": canvas,
        "torso": _place(torso),
        "neck": _place(collar),
        "head": _place(head),
        "eyes": _place(whites),
        "eye_white": _place(whites),
        "iris": _place(irises),
        "eye_socket": _place(sockets),
        "eyes_half": _place(eyes_half),
        "eyes_closed": _place(eyes_closed),
        "brows": _place(brows),
        "visemes": visemes,
        "expressions": {"happy": happy},
        "eyeRig": {
            "maxShift": [max(1.0, radius * 0.3), max(1.0, radius * 0.2)],
            "irises": [[left_eye[0], left_eye[1], radius / 3], [right_eye[0], right_eye[1], radius / 3]],
        },
        "mouthQA": {
            "head": "aiwake v3",
            "mouths": "aiwake v3",
            "mouthBox": list(mouth),
            "mouth": list(mouth),
            "eyes": [list(eye_boxes[0]), list(eye_boxes[1])],
            "irises": [[left_eye[0], left_eye[1]], [right_eye[0], right_eye[1]]],
            "mouthless": True,
        },
    }
    boxes = {
        "face": list(face),
        "left_eye": list(eye_boxes[0]),
        "right_eye": list(eye_boxes[1]),
        "mouth": list(mouth),
    }
    rig_doc = {
        "size": list(canvas),
        "boxes": boxes,
        "mouthBox": list(mouth),
        "framing": dict(manifest.get("framing") or {}),
        "pipeline": "aiwake-v3",
    }
    (skin / "rig.json").write_text(json.dumps(rig_doc, indent=2), encoding="utf-8")
    (skin / "rig_front.json").write_text(json.dumps(rig_doc, indent=2), encoding="utf-8")
    left = rig._write_view(parts, skin / "views" / "left", url, mirror=False)
    right = rig._write_view(parts, skin / "views" / "right", url, mirror=True)
    front = rig._write_view(parts, skin / "views" / "front", url, mirror=False)
    return {"left": left, "right": right, "front": front}
