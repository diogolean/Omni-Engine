import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from products.animind_core import mouthset, qa


def _face(lines):
    img = np.full((80, 120, 3), (240, 200, 175), np.uint8)
    for y in lines:
        img[y:y + 3, 30:90] = (150, 60, 60)
    return img


def test_ink_share_sees_a_leftover_lip_line():
    ref = _face([40])
    assert qa.ink_share(_face([]), ref, (35, 35, 85, 45)) == 0.0
    assert qa.ink_share(_face([40]), ref, (35, 35, 85, 45)) > 0.04


def test_stacked_lip_lines_are_a_second_mouth():
    one = qa.ink_mask(_face([40]), np.array([240, 200, 175]))
    two = qa.ink_mask(_face([30, 50]), np.array([240, 200, 175]))
    assert not qa._stacked(one, 50, 10)
    assert qa._stacked(two, 50, 10)


_COLOURS = {
    "outline": (48, 32, 30, 255),
    "cavity": (36, 16, 18, 255),
    "teeth": (246, 242, 236, 255),
    "tongue": (186, 86, 90, 255),
    "lip": (154, 78, 76, 255),
}


def _ink_inside(image: Image.Image) -> None:
    alpha = np.asarray(image.getchannel("A"))
    assert int(alpha[0].max()) == 0 and int(alpha[-1].max()) == 0
    assert int(alpha[:, 0].max()) == 0 and int(alpha[:, -1].max()) == 0
    box = image.getchannel("A").getbbox()
    assert box is not None
    assert box[0] >= 1 and box[1] >= 1
    assert box[2] <= image.width - 1 and box[3] <= image.height - 1


def _outside_counts(view: Path) -> dict[str, int]:
    """Pixels the double-mouth gate calls ink outside the sprite, before its minimum."""
    doc = json.loads((view / "view.json").read_text(encoding="utf-8"))
    head = next(layer for layer in doc["layers"] if layer["name"] == "head")
    canvas = Image.new("RGBA", (doc["canvas"]["w"], doc["canvas"]["h"]), (0, 0, 0, 0))
    canvas.alpha_composite(Image.open(view / head["url"]).convert("RGBA"), (head["x"], head["y"]))
    mouth = doc["mouthQA"]["mouth"]
    height, width = doc["canvas"]["h"], doc["canvas"]["w"]
    rx0, ry0, rx1, ry1 = qa._mouth_region(mouth, (height, width))
    base = np.asarray(canvas)[ry0:ry1, rx0:rx1]
    opaque = base[..., 3] > 128
    skin = qa.skin_of(base[..., :3][opaque]) if opaque.any() else np.array([220, 180, 160])
    kernel = np.ones((2, 2), np.uint8)
    yy, xx = np.mgrid[ry0:ry1, rx0:rx1]
    rest = doc["visemes"]["X"]
    sprite = Image.open(view / rest["url"]).convert("RGBA")
    box = sprite.getchannel("A").point(lambda q: 255 if q > 128 else 0).getbbox()
    zx0, zy0, zx1, zy1 = rest["x"] + box[0], rest["y"] + box[1], rest["x"] + box[2], rest["y"] + box[3]
    zw, zh = max(4, zx1 - zx0), max(4, zy1 - zy0)
    zone = ((xx - (zx0 + zx1) / 2) / (zw * 0.65)) ** 2 + ((yy - (zy0 + zy1) / 2) / (zh * 0.8 + 4)) ** 2 <= 1
    counts = {}
    for name, layer in doc["visemes"].items():
        mouth_im = Image.open(view / layer["url"]).convert("RGBA")
        plate = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        plate.alpha_composite(mouth_im, (layer["x"], layer["y"]))
        comp = canvas.copy()
        comp.alpha_composite(plate)
        crop = np.asarray(comp)[ry0:ry1, rx0:rx1]
        alpha = np.asarray(plate)[ry0:ry1, rx0:rx1, 3]
        ink = qa.ink_mask(crop[..., :3], skin) & (crop[..., 3] > 128) & zone
        ink = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_OPEN, kernel).astype(bool)
        counts[name] = int((ink & (alpha < 40)).sum())
    return counts


def test_viseme_b_ink_stays_inside_the_sprite_on_a_moustache(tmp_path):
    """Einstein's viseme B left 15px of lip fringe outside the sprite on a grey moustache."""
    face_h = 280
    cap = {code: 0.95 * share * face_h for code, share in qa.OPEN_DRAWN.items()}
    drawn = mouthset.draw_set(112, _COLOURS, squeeze=0.92, open_cap=cap)
    sample = drawn["B"]
    origin = (24, 24)
    canvas = (sample.width + 48, sample.height + 48)
    view = tmp_path / "left"
    (view / "mouths").mkdir(parents=True)
    Image.new("RGBA", canvas, (186, 174, 166, 255)).save(view / "head.png")
    visemes = {}
    for code, image in drawn.items():
        _ink_inside(image)
        image.save(view / "mouths" / f"{code}.png")
        visemes[code] = {
            "url": f"mouths/{code}.png", "x": origin[0], "y": origin[1],
            "w": image.width, "h": image.height,
        }
    mouth = [origin[0] + 8, origin[1] + 8, origin[0] + 48, origin[1] + 28]
    doc = {
        "canvas": {"w": canvas[0], "h": canvas[1]},
        "layers": [{"name": "head", "url": "head.png", "x": 0, "y": 0, "z": 1}],
        "visemes": visemes,
        "mouthQA": {"mouth": mouth, "mouths": "human", "mouthless": False},
    }
    (view / "view.json").write_text(json.dumps(doc), encoding="utf-8")
    counts = _outside_counts(view)
    assert counts["B"] == 0, counts
    assert all(count == 0 for count in counts.values()), counts
    gate = qa.double_mouth(view)
    assert gate["pass"], gate
    assert "B" not in (gate.get("bad") or {})


def test_south_park_smile_ink_stays_inside_the_png():
    for width in (12, 20, 112):
        drawn = mouthset.draw_expressions(width, _COLOURS, squeeze=0.92, open_cap=40)
        for image in drawn.values():
            _ink_inside(image)
        visemes = mouthset.draw_set(width, _COLOURS, squeeze=0.92)
        for image in visemes.values():
            _ink_inside(image)


def test_drawn_human_mouths_skip_the_skin_rim_and_a_cut_mouth_does_not(tmp_path):
    view = tmp_path / "left"
    view.mkdir()
    Image.new("RGBA", (80, 60), (232, 186, 156, 255)).save(view / "head.png")
    sprite = Image.new("RGBA", (40, 24), (0, 0, 0, 0))
    pixels = sprite.load()
    for y in range(24):
        for x in range(40):
            if 10 <= x < 30 and 8 <= y < 16:
                pixels[x, y] = (154, 78, 76, 255)
            elif 6 <= x < 34 and 5 <= y < 19:
                pixels[x, y] = (154, 78, 76, 80)
    sprite.save(view / "mouth.png")

    def save(mouths: str | None) -> None:
        doc = {
            "canvas": {"w": 80, "h": 60},
            "layers": [{"name": "head", "url": "head.png", "x": 0, "y": 0, "z": 1}],
            "visemes": {"X": {"url": "mouth.png", "x": 20, "y": 18, "w": 40, "h": 24}},
            "mouthQA": {"mouths": mouths} if mouths else {},
        }
        (view / "view.json").write_text(json.dumps(doc), encoding="utf-8")

    save("human")
    drawn = qa.skin_delta_e(view)
    assert drawn["pass"] is True
    assert drawn["value"] == 0.0
    assert "drawn" in drawn["note"]
    assert drawn["limit"] == qa.LIMITS["skinDeltaE"]
    save("cut")
    cut = qa.skin_delta_e(view)
    assert cut["pass"] is False
    assert cut["value"] > qa.LIMITS["skinDeltaE"]
    assert qa.LIMITS["skinDeltaE"] == 6.0
    qa.match_skin_rim(view)
    matched = qa.skin_delta_e(view)
    assert matched["pass"] is True
    assert matched["value"] <= qa.LIMITS["skinDeltaE"]
    assert matched["limit"] == 6.0
    assert "drawn" not in (matched.get("note") or "")
