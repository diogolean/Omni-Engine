"""The mouth docks under the mustache, and the monologue stands on the set.

The photographed plate is not required. These fixtures are the beige band and
the brown chin ovals that used to ride along with the mustache layer, and a
bust whose stage is opaque black.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from products.animind_core import approval, dock, rig, scene, sheet

BEIGE = (230, 210, 170)
BROWN = (120, 70, 40)
HAIR = (28, 26, 26)
CHEEK = (10, 200, 10)
CAVITY = (18, 10, 12)


def _close(rgb: np.ndarray, color: tuple[int, int, int], tol: int = 12) -> np.ndarray:
    return np.abs(rgb.astype(int) - np.array(color)).max(axis=2) <= tol


def _dirty_parts() -> list[tuple[str, Image.Image, tuple[int, int], int]]:
    width, height = 220, 260
    rng = np.random.default_rng(3)
    noise = rng.integers(-16, 17, (height, width, 3), dtype=np.int16)
    skin = np.clip(np.array([206, 164, 132], np.int16) + noise, 40, 230).astype(np.uint8)
    head = np.dstack([skin, np.full((height, width), 255, np.uint8)])
    head[28:36, 10:18, :3] = CHEEK
    head[118:142, 36:184, :3] = BEIGE
    head_img = Image.fromarray(head, "RGBA")
    draw = ImageDraw.Draw(head_img)
    draw.ellipse((46, 146, 84, 172), fill=BROWN + (255,))
    draw.ellipse((136, 146, 174, 172), fill=BROWN + (255,))

    stache = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    paint = ImageDraw.Draw(stache)
    paint.ellipse((28, 86, 192, 116), fill=HAIR + (255,))
    paint.rectangle((36, 118, 184, 142), fill=BEIGE + (255,))
    paint.ellipse((46, 146, 84, 172), fill=BROWN + (255,))
    paint.ellipse((136, 146, 174, 172), fill=BROWN + (255,))

    mouth = Image.new("RGBA", (84, 40), (0, 0, 0, 0))
    lips = ImageDraw.Draw(mouth)
    lips.rounded_rectangle((0, 0, 83, 39), radius=16, fill=CAVITY + (255,))
    lips.arc((0, 0, 83, 28), 200, 340, fill=(210, 150, 140, 255), width=4)
    return [
        ("head", head_img, (0, 0), 2),
        ("mustache", stache, (0, 0), 11),
        ("mouth_D", mouth, (68, 128), 10),
    ]


def _composite(parts: list[tuple[str, Image.Image, tuple[int, int], int]]) -> Image.Image:
    canvas = Image.new("RGBA", (220, 260), (0, 0, 0, 0))
    for _name, image, xy, _z in sorted(parts, key=lambda part: part[3]):
        sprite = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sprite.paste(image, xy)
        canvas = Image.alpha_composite(canvas, sprite)
    return canvas


def test_mouth_docks_under_a_hair_only_mustache():
    seated = dock.seat_parts(_dirty_parts())
    by_name = {name: (image, xy) for name, image, xy, _z in seated}
    hair = np.asarray(by_name["mustache"][0])
    assert tuple(hair[100, 110][:3]) == HAIR
    assert hair[100, 110, 3] > 200
    assert hair[128, 110, 3] == 0  # the beige band is not part of the mustache
    assert hair[158, 64, 3] == 0  # neither are the brown chin ovals

    mouth, mouth_xy = by_name["mouth_D"]
    ink = np.flatnonzero((np.asarray(mouth.getchannel("A")) > 128).any(axis=1))
    assert int(mouth_xy[1] + ink.min()) <= 116  # top ink tucked under the hair

    frame = np.asarray(_composite(seated).convert("RGB"))
    assert not _close(frame[118:142, 36:184], BEIGE).any()
    assert not _close(frame[146:172, 46:84], BROWN).any()
    assert not _close(frame[146:172, 136:174], BROWN).any()
    assert tuple(frame[32, 14]) == CHEEK
    assert _close(frame[120:150, 90:130], CAVITY, tol=18).any()


def test_keep_hair_does_not_paint_a_flat_band_over_the_chin():
    from products.animind_core.rig import _mouthless

    img = Image.new("RGBA", (300, 300), (225, 180, 150, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 250, 299, 299), fill=(245, 245, 245, 255))
    draw.ellipse((60, 110, 240, 170), fill=(35, 32, 34, 255))
    draw.line((115, 180, 185, 180), fill=(150, 80, 70, 255), width=4)
    draw.point((100, 176), fill=(160, 120, 100, 255))  # shaded chin, inside the old band
    out = np.asarray(_mouthless(img, (40, 90, 260, 260), (60, 110, 240, 185), keep_hair=True))
    assert tuple(out[176, 100][:3]) == (160, 120, 100)
    assert np.abs(out[178:183, 120:180, :3].astype(int) - np.array((225, 180, 150))).max() < 25


def test_black_stage_keys_out_and_leaves_the_suit_and_pupil():
    img = Image.new("RGBA", (80, 100), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((22, 28, 58, 92), fill=(40, 36, 48, 255))
    draw.ellipse((36, 40, 46, 50), fill=(8, 8, 8, 255))
    keyed = dock.key_backdrop(img)
    assert keyed.getpixel((1, 1))[3] == 0
    assert keyed.getpixel((40, 70))[3] == 255
    assert keyed.getpixel((41, 45))[3] == 255


def _skin(root: Path, *, black_stage: bool) -> Path:
    skin = root / "skin"
    width, height = 240, 320
    if black_stage:
        head = Image.new("RGBA", (width, height), (0, 0, 0, 255))
        ImageDraw.Draw(head).ellipse((70, 150, 170, 280), fill=(214, 170, 140, 255))
        body = Image.new("RGBA", (width, height), (0, 0, 0, 255))
        ImageDraw.Draw(body).rectangle((80, 240, 160, 310), fill=(36, 34, 40, 255))
    else:
        head = Image.new("RGBA", (width, height), (214, 170, 140, 255))
        body = Image.new("RGBA", (width, height), (36, 34, 40, 255))
    mouth = Image.new("RGBA", (48, 28), (0, 0, 0, 0))
    ImageDraw.Draw(mouth).rounded_rectangle((4, 4, 44, 24), radius=8, fill=(20, 12, 14, 255))
    for view_name in ("front", "left", "right"):
        folder = skin / "views" / view_name
        (folder / "mouths").mkdir(parents=True)
        head.save(folder / "head.png")
        body.save(folder / "body.png")
        mouth.save(folder / "mouths" / "X.png")
        doc = {
            "canvas": {"w": width, "h": height},
            "layers": [
                {"name": "body", "url": "body.png", "x": 0, "y": 0, "w": width, "h": height, "z": 1},
                {"name": "head", "url": "head.png", "x": 0, "y": 0, "w": width, "h": height, "z": 2},
            ],
            "visemes": {
                "X": {"name": "mouth_X", "url": "mouths/X.png", "x": 96, "y": 210, "w": 48, "h": 28, "z": 10},
            },
            "expressions": {},
        }
        (folder / "view.json").write_text(json.dumps(doc), encoding="utf-8")
    return skin


def _corner(frame: Image.Image) -> np.ndarray:
    return np.asarray(frame.convert("RGB"))[:16, :16]


def test_monologue_sits_on_the_requested_set(tmp_path):
    skin = _skin(tmp_path, black_stage=True)
    studio, _track = approval.monologue(skin, name="Albert Einstein")
    assert studio.scenario == "studio"
    assert studio.names == ["Albert Einstein"]
    loaded = studio.rigs[0]["front"]
    head = next(part for part in loaded.layers if part[0] == "head")
    assert head[1].getpixel((2, 2))[3] == 0

    wide = _corner(studio.frame(0.35))
    assert float(wide.mean()) > 8
    assert not (wide == 0).all()

    room, _track = approval.monologue(skin, name="Albert Einstein", scenario="classroom")
    assert room.scenario == "classroom"
    assert room.names == ["Albert Einstein"]
    assert float(_corner(room.frame(0.35))[..., 0].mean()) > float(wide[..., 0].mean()) + 20

    data = rig.load_view(skin, "front")
    view = scene.load_rig_view(
        data, scene.fit_scale(data["canvas"]["h"], "solo", 0), scene.light("classroom", "solo", 0),
    )
    product = scene.Scene(
        template="solo", scenario="classroom", rigs=[{"front": view, "left": view, "right": view}],
        script=room.script, envelope=np.ones(400), env_fps=100, duration_s=4.0,
        disclaimer="Parody", names=["Albert Einstein"], roles=["Talk"], lang="en-US",
    )
    assert float(_corner(product.frame(0.35))[..., 0].mean()) > 40


def test_review_sheet_uses_the_same_seat(tmp_path):
    skin = tmp_path / "sheet"
    folder = skin / "views" / "front"
    (folder / "mouths").mkdir(parents=True)
    parts = _dirty_parts()
    layers = []
    for name, image, xy, z in parts:
        if name.startswith("mouth"):
            path = folder / "mouths" / "D.png"
            key = "visemes"
        else:
            path = folder / f"{name}.png"
            key = "layers"
        image.save(path)
        layers.append((key, {
            "name": name if not name.startswith("mouth") else "mouth_D",
            "url": str(path.relative_to(folder)),
            "x": xy[0], "y": xy[1], "w": image.width, "h": image.height, "z": z,
        }))
    doc = {
        "canvas": {"w": 220, "h": 260},
        "layers": [item for key, item in layers if key == "layers"],
        "visemes": {"D": next(item for key, item in layers if key == "visemes"), "X": next(item for key, item in layers if key == "visemes")},
        "expressions": {},
    }
    (folder / "view.json").write_text(json.dumps(doc), encoding="utf-8")
    view = rig.load_view(skin, "front")
    frame = np.asarray(sheet.compose(view, "D").convert("RGB"))
    assert not _close(frame[118:142, 36:184], BEIGE).any()
    assert tuple(frame[32, 14]) == CHEEK
