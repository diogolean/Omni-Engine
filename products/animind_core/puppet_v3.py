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

import cv2
import numpy as np
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
    "_gemini.png",
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
            "hand-drawn dark ink outlines. Keep any moustache or beard. "
            "NO human mouth, NO lips, NO lip colour, NO pink mouth, NO smile, NO teeth, NO smile creases. "
            "The skin under the moustache is one even blank skin tone with no line drawn on it."
        )
    side = "left" if facing == "left" else "right"
    return (
        f"{subject} Medium bust, STRICT POSE LOCK: exactly 25 degrees facing {side} "
        "in three-quarter view, never dead-front. "
        "STRICT PROPORTION: crown-to-chin head height is exactly 27 percent of the 9:16 "
        "portrait height; the torso fills the lower 73 percent. "
        "The head docks into a short collar and the opaque torso is cut off by the bottom edge. "
        "Solid pure black background on every edge, including the corners and the bottom. "
        "No white border and no white strip. --ar 9:16"
    )


MOUTH_EDIT_PROMPT = (
    "Edit the reference. Remove the mouth and the lips. Leave blank even skin under the moustache. "
    "Change nothing else: same person, same hair, same moustache, same clothes, same pose, same framing. "
    "NO human mouth, NO lips, NO lip colour, NO teeth. Solid black background to every edge."
)


def drop_white_footer(image: Image.Image) -> Image.Image:
    """If the bottom of the plate is a white strip, scale the figure onto that edge.

    The fill is black, matching the prompted background, and the torso pixels
    touch the last row.
    """
    arr = np.asarray(image.convert("RGBA"))
    height, width = arr.shape[:2]
    rgb = arr[:, :, :3].astype(np.int16)
    lum = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    white = (lum >= 220) & (chroma <= 18)
    row_white = white.mean(axis=1) > 0.70
    if not row_white[int(height * 0.85):].mean() >= 0.5:
        return image
    content = np.nonzero(~row_white)[0]
    if content.size == 0:
        return image
    bottom = int(content.max()) + 1
    if bottom > height * 0.98:
        return image
    cropped = image.convert("RGBA").crop((0, 0, width, bottom))
    scale = height / float(bottom)
    resized = cropped.resize(
        (max(1, int(round(width * scale))), height),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    canvas.paste(resized, ((width - resized.width) // 2, 0), resized)
    return canvas


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


def _seat_human(qa: dict, scale: float, offset: tuple[int, int], socket: Image.Image) -> dict:
    """Move the cut-eye measurements onto the seated canvas."""
    def ty(y: float) -> int:
        return int(round(float(y) * scale + offset[1]))

    def tp(x: float, y: float) -> tuple[int, int]:
        return (int(round(float(x) * scale + offset[0])), int(round(float(y) * scale + offset[1])))

    iris = qa["iris"][0]
    ix, iy = tp(iris[0], iris[1])
    box = _alpha_bbox(socket) or (ix - 4, iy - 4, ix + 4, iy + 4)
    return {
        "lines": {state: ty(qa["painted"][state]["drawn"][0]) for state in ("open", "half", "closed")},
        "painted": {state: ty(qa["painted"][state]["edge"][0]) for state in ("open", "half", "closed")},
        "iris": [ix, iy, max(1, int(round(iris[2] * scale)))],
        "sclera": list(box),
        "browBottom": ty(qa["browBottom"]),
        "eyeTop": ty(qa["eyeTop"]),
        "browHeight": max(4, int(round(float(qa.get("browHeight") or 8) * scale))),
        "forehead": [
            int(round(qa["forehead"][0] * scale + offset[0])),
            int(round(qa["forehead"][1] * scale + offset[1])),
            int(round(qa["forehead"][2] * scale + offset[0])),
            int(round(qa["forehead"][3] * scale + offset[1])),
        ] if qa.get("forehead") else None,
        "shift": [max(1.0, float(qa["shift"][0]) * scale), max(1.0, float(qa["shift"][1]) * scale)],
    }


def _point(x: float, y: float, scale: float, offset: tuple[int, int]) -> tuple[int, int]:
    return (int(round(x * scale + offset[0])), int(round(y * scale + offset[1])))


def _around(center: tuple[int, int], rx: int, ry: int) -> tuple[int, int, int, int]:
    return (center[0] - rx, center[1] - ry, center[0] + rx, center[1] + ry)


def _disc(canvas: tuple[int, int], center: tuple[int, int], radius: int, fill: tuple[int, int, int, int]) -> Image.Image:
    image = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(_around(center, radius, radius), fill=fill)
    return image


def clean_border_white(image: Image.Image) -> Image.Image:
    """Drop white islands that touch the frame. They survive a black-key cutout."""
    arr = np.asarray(image.convert("RGBA")).copy()
    rgb = arr[:, :, :3].astype(np.int16)
    lum = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    white = ((lum >= 225) & (chroma <= 22) & (arr[:, :, 3] > 8)).astype(np.uint8)
    count, labels = cv2.connectedComponents(white, connectivity=8)
    if count <= 1:
        return image
    edge = np.zeros(labels.shape, bool)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    touch = set(np.unique(labels[edge])) - {0}
    if not touch:
        return image
    kill = np.isin(labels, list(touch))
    arr[kill, 3] = 0
    return Image.fromarray(arr, "RGBA")


def human_mouths(
    mouth_at: tuple[int, int],
    width: int,
    *,
    open_cap: float | dict[str, float] | None = None,
    squeeze: float = 0.92,
) -> dict[str, tuple[Image.Image, tuple[int, int]]]:
    """One human mouth set, every viseme on the same canvas and the same anchor.

    The Aiwake rigger draws robot bars. Those stay on mecha puppets. A person
    uses the organic set, and no viseme is allowed to change the puppet's scale.
    """
    from . import mouthset  # noqa: PLC0415

    colours = {
        "outline": (48, 32, 30, 255),
        "cavity": (36, 16, 18, 255),
        "teeth": (246, 242, 236, 255),
        "tongue": (186, 86, 90, 255),
        "lip": (154, 78, 76, 255),
    }
    drawn = mouthset.draw_set(max(18, int(width)), colours, squeeze=squeeze, open_cap=open_cap)
    sample = next(iter(drawn.values()))
    anchor = (int(round(mouth_at[0] - sample.width / 2)), int(round(mouth_at[1] - sample.height / 2)))
    return {code: (sprite, anchor) for code, sprite in drawn.items()}


def human_expressions(
    anchor: tuple[int, int],
    size: tuple[int, int],
    width: int,
    *,
    open_cap: float | None = None,
    squeeze: float = 0.92,
) -> dict[str, tuple[Image.Image, tuple[int, int]]]:
    """Emotion rests on the same canvas and anchor as the visemes."""
    from . import mouthset  # noqa: PLC0415

    colours = {
        "outline": (48, 32, 30, 255),
        "cavity": (36, 16, 18, 255),
        "teeth": (246, 242, 236, 255),
        "tongue": (186, 86, 90, 255),
        "lip": (154, 78, 76, 255),
    }
    drawn = mouthset.draw_expressions(max(18, int(width)), colours, squeeze=squeeze, open_cap=open_cap)
    seated: dict[str, tuple[Image.Image, tuple[int, int]]] = {}
    ax, ay = int(anchor[0]), int(anchor[1])
    for name, sprite in drawn.items():
        if sprite.width <= size[0] and sprite.height <= size[1]:
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            canvas.paste(sprite, ((size[0] - sprite.width) // 2, (size[1] - sprite.height) // 2), sprite)
            seated[name] = (canvas, anchor)
            continue
        # Wider than the viseme canvas (the South Park smile). Seat the ink
        # on the same point and keep the stroke; a paste would clip it.
        seated[name] = (
            sprite,
            (ax + (size[0] - sprite.width) // 2, ay + (size[1] - sprite.height) // 2),
        )
    return seated


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
    human_dir = skin / "human_eyes"
    human_meta = None
    brow_left = brow_right = None
    if (human_dir / "geometry.json").is_file():
        def baked(name: str) -> Image.Image:
            return _bake(_open(human_dir / f"{name}.png"), scale, offset, canvas)
        whites = baked("eye_white")
        irises = baked("iris")
        sockets = baked("eye_socket")
        eyes_half = baked("eyes_half")
        eyes_closed = baked("eyes_closed")
        brows = baked("brows")
        brow_left = baked("brow_left")
        brow_right = baked("brow_right")
        head = _bake(_open(human_dir / "head.png"), scale, offset, canvas)
        human_meta = _seat_human(json.loads((human_dir / "geometry.json").read_text(encoding="utf-8"))["qa"], scale, offset, sockets)
    else:
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
    pad = 12
    face = (
        min(face[0], eye_boxes[0][0], eye_boxes[1][0]) - pad,
        min(face[1], eye_boxes[0][1], eye_boxes[1][1]) - pad,
        max(face[2], eye_boxes[0][2], eye_boxes[1][2]) + pad,
        face[3] + pad,
    )
    from . import mouth_anchor, qa  # noqa: PLC0415

    face_h = max(1, face[3] - face[1])
    viseme_cap = {code: 0.95 * share * face_h for code, share in qa.OPEN_DRAWN.items()}
    expression_cap = 0.95 * qa.EXPRESSION_OPEN_ABS * face_h
    mouth_width = max(18, int(radius * 3))

    def _pack(view_name: str) -> tuple[Any, dict, dict, list[int]]:
        anchor = mouth_anchor.place(head, face, eye_boxes[0], eye_boxes[1], view=view_name)
        visemes = human_mouths((anchor.x, anchor.y), mouth_width, open_cap=viseme_cap)
        sample = next(iter(visemes.values()))
        expressions = human_expressions(
            sample[1], sample[0].size, mouth_width, open_cap=expression_cap,
        )
        box = list(anchor.as_box(face, mouth_width))
        return anchor, visemes, expressions, box

    facing = mouth_anchor.infer_view(face, eye_boxes[0], eye_boxes[1])
    left_anchor, visemes, expressions, mouth = _pack("left")
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
        **({"brow_left": _place(brow_left), "brow_right": _place(brow_right)} if brow_left is not None else {}),
        "visemes": visemes,
        "expressions": expressions,
        "eyeRig": {
            "maxShift": [max(1.0, radius * 0.3), max(1.0, radius * 0.2)],
            "irises": [[left_eye[0], left_eye[1], radius / 3], [right_eye[0], right_eye[1], radius / 3]],
        },
        "mouthQA": {
            "head": "aiwake v3",
            "mouths": "human",
            "mouthBox": list(mouth),
            "mouth": list(mouth),
            "face": list(face),
            "eyes": [list(eye_boxes[0]), list(eye_boxes[1])],
            "irises": [[left_eye[0], left_eye[1]], [right_eye[0], right_eye[1]]],
            "mouthless": True,
            "anchor": left_anchor.to_dict(),
        },
        **({"humanEyes": human_meta} if human_meta else {}),
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
        "irises": [[left_eye[0], left_eye[1]], [right_eye[0], right_eye[1]]],
        "framing": dict(manifest.get("framing") or {}),
        "pipeline": "aiwake-v3",
    }
    (skin / "rig.json").write_text(json.dumps(rig_doc, indent=2), encoding="utf-8")
    left = rig._write_view(parts, skin / "views" / "left", url, mirror=False)
    right = rig._write_view(parts, skin / "views" / "right", url, mirror=True)
    views: dict[str, Any] = {"left": left, "right": right}
    front_dir = skin / "views" / "front"
    rig_front = skin / "rig_front.json"
    # A front slot is the midline. A three-quarter plate stays under left/right.
    # Writing it as views/front and tagging the anchor "left" is a lie.
    if facing == "front":
        front_anchor, front_visemes, front_expressions, front_mouth = _pack("front")
        front_doc = json.loads(json.dumps(rig_doc))
        front_doc["boxes"]["mouth"] = list(front_mouth)
        front_doc["mouthBox"] = list(front_mouth)
        rig_front.write_text(json.dumps(front_doc, indent=2), encoding="utf-8")
        parts["visemes"] = front_visemes
        parts["expressions"] = front_expressions
        parts["mouthQA"]["mouth"] = list(front_mouth)
        parts["mouthQA"]["mouthBox"] = list(front_mouth)
        parts["mouthQA"]["anchor"] = front_anchor.to_dict()
        views["front"] = rig._write_view(parts, front_dir, url, mirror=False)
    else:
        if front_dir.exists():
            shutil.rmtree(front_dir)
        if rig_front.is_file():
            rig_front.unlink()
    return views
