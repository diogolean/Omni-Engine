"""Mouth anchor: nose–chin band on the front, gaze side on a three-quarter view."""
import json
import sys
import types
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from products.animind_core import mouth_anchor, mouthset, qa

_KEYS = (
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "TOGETHER_API_KEY", "OPENROUTER_API_KEY",
    "ANIMIND_IMAGE_MODEL",
)
_IMAGE_MOD = "products.aiwake.animator.factory.create_puppet"


def _refuse_image(*_args, **_kwargs):
    raise AssertionError("image model called")


@pytest.fixture(autouse=True)
def _no_image_keys(monkeypatch):
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    # Seed only the image entry. Stubbing products.aiwake would hide the drawn mouth library.
    if _IMAGE_MOD in sys.modules:
        monkeypatch.setattr(f"{_IMAGE_MOD}.generate_character_image", _refuse_image)
        return
    module = types.ModuleType(_IMAGE_MOD)
    module.__package__ = _IMAGE_MOD.rpartition(".")[0]
    module.generate_character_image = _refuse_image
    monkeypatch.setitem(sys.modules, _IMAGE_MOD, module)


def _portrait(turn: str = "front", *, moustache: bool = True) -> tuple[Image.Image, tuple, tuple, tuple]:
    image = Image.new("RGBA", (320, 400), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((40, 30, 280, 340), fill=(232, 186, 156, 255))
    face = (40, 30, 280, 340)
    shift = {"front": 0, "left": -36, "right": 36}[turn]
    centres = (110 + shift, 210 + shift)
    for cx in centres:
        draw.ellipse((cx - 18, 120, cx + 18, 156), fill=(250, 250, 248, 255))
        draw.ellipse((cx - 6, 130, cx + 6, 146), fill=(24, 16, 14, 255))
    eyes = (
        (centres[0] - 18, 120, centres[0] + 18, 156),
        (centres[1] - 18, 120, centres[1] + 18, 156),
    )
    nose_x = 160 + shift
    draw.polygon([(nose_x, 170), (nose_x - 12, 206), (nose_x + 12, 206)], fill=(210, 156, 128, 255))
    if moustache:
        draw.ellipse((96 + shift, 214, 224 + shift, 250), fill=(181, 168, 161, 255))
    return image, face, eyes[0], eyes[1]


def _along(anchor: mouth_anchor.Anchor) -> float:
    return (anchor.y - anchor.nose[1]) / (anchor.chin[1] - anchor.nose[1])


def test_front_anchor_sits_between_nose_and_chin_and_three_quarter_follows_the_gaze():
    image, face, left, right = _portrait("front")
    front = mouth_anchor.place(image, face, left, right, view="front")
    assert mouth_anchor.ALONG_LO <= _along(front) <= mouth_anchor.ALONG_HI
    assert front.view == "front"
    centre = (face[0] + face[2]) / 2
    assert abs(front.x - centre) <= 4
    assert abs((front.x - centre) / (face[2] - face[0])) == 0
    assert front.y > front.moustache_bottom
    turned, turned_face, turned_left, turned_right = _portrait("left")
    left_anchor = mouth_anchor.place(turned, turned_face, turned_left, turned_right, view="left")
    right_anchor = mouth_anchor.place(turned, turned_face, turned_left, turned_right, view="right")
    centre = (turned_face[0] + turned_face[2]) / 2
    assert left_anchor.x < centre - 8
    assert right_anchor.x > centre + 8
    assert left_anchor.y == right_anchor.y
    assert mouth_anchor.resolve_view("front", turned_face, turned_left, turned_right) == "left"


def test_a_mouth_on_the_nose_or_the_wrong_side_fails_the_gate():
    image, face, left, right = _portrait("front")
    anchor = mouth_anchor.place(image, face, left, right, view="front")
    nose_middle = ((left[1] + right[1]) / 2 + anchor.nose[1]) / 2
    on_nose = mouth_anchor.judge((anchor.x, nose_middle), image, face, left, right, view="front")
    assert on_nose["pass"] is False
    assert "nose" in on_nose["note"] or "eyes" in on_nose["note"]
    between_eyes = mouth_anchor.judge((anchor.x, (left[1] + left[3]) / 2), image, face, left, right, view="front")
    assert between_eyes["pass"] is False
    wrong = mouth_anchor.judge((face[2] - 30, anchor.y), image, face, left, right, view="left")
    assert wrong["pass"] is False
    assert wrong["offset"] > 0
    good = mouth_anchor.judge((anchor.x, anchor.y), image, face, left, right, view="front")
    assert good["pass"] is True


def test_a_sweater_is_not_the_chin_and_the_mouth_stays_under_the_moustache():
    """A face box that runs into the collar used to park the lip on the ribbing."""
    image = Image.new("RGBA", (320, 520), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((40, 36, 280, 360), fill=(232, 186, 156, 255))
    draw.rectangle((118, 330, 202, 410), fill=(214, 166, 140, 255))
    draw.rectangle((24, 392, 296, 510), fill=(176, 178, 184, 255))
    draw.ellipse((70, 150, 118, 186), fill=(250, 250, 248, 255))
    draw.ellipse((190, 150, 238, 186), fill=(250, 250, 248, 255))
    draw.ellipse((88, 158, 100, 174), fill=(24, 16, 14, 255))
    draw.ellipse((208, 158, 220, 174), fill=(24, 16, 14, 255))
    draw.ellipse((96, 230, 224, 278), fill=(236, 236, 238, 255))
    face = (24, 30, 296, 500)
    left, right = (70, 150, 118, 186), (190, 150, 238, 186)
    anchor = mouth_anchor.place(image, face, left, right, view="front")
    assert anchor.moustache_bottom is not None
    assert anchor.moustache_bottom < 300
    assert anchor.y > anchor.moustache_bottom
    assert anchor.y < 392  # above the turtleneck
    assert anchor.chin[1] < 430


def test_moustache_keeps_the_mouth_below_the_hair():
    image, face, left, right = _portrait("front")
    draw = ImageDraw.Draw(image)
    # A long moustache that covers the default 64% mark. The lip has to drop under it.
    draw.ellipse((90, 200, 230, 310), fill=(176, 164, 156, 255))
    anchor = mouth_anchor.place(image, face, left, right, view="front")
    assert anchor.moustache_bottom is not None
    assert anchor.y > anchor.moustache_bottom
    assert anchor.y < anchor.chin[1]
    assert anchor.y > max(left[3], right[3])


def test_visemes_and_expressions_share_the_anchor():
    image, face, left, right = _portrait("front", moustache=False)
    anchor = mouth_anchor.place(image, face, left, right, view="front")
    colours = {
        "outline": (48, 32, 30, 255), "cavity": (36, 16, 18, 255),
        "teeth": (246, 242, 236, 255), "tongue": (186, 86, 90, 255), "lip": (154, 78, 76, 255),
    }
    visemes = mouth_anchor.seat(mouthset.draw_set(80, colours, squeeze=1.0), anchor)
    expressions = mouth_anchor.seat(mouthset.draw_expressions(80, colours, squeeze=1.0, open_cap=40), anchor)

    def centre(pair) -> tuple[float, float]:
        sprite, (x, y) = pair
        box = sprite.getchannel("A").point(lambda a: 255 if a > 128 else 0).getbbox()
        return (x + (box[0] + box[2]) / 2, y + (box[1] + box[3]) / 2)

    origin = centre(visemes["X"])
    for pair in [*visemes.values(), *expressions.values()]:
        cx, cy = centre(pair)
        assert abs(cx - origin[0]) <= 3
        assert abs(cy - origin[1]) <= 3
    assert abs(origin[0] - anchor.x) <= 3
    assert abs(origin[1] - anchor.y) <= 3


def _write_view(skin, name: str, image: Image.Image, face, left, right, mouth_at: tuple[int, int]) -> None:
    folder = skin / "views" / name
    (folder / "mouths").mkdir(parents=True)
    image.save(folder / "head.png")
    mouth = Image.new("RGBA", (36, 16), (0, 0, 0, 0))
    ImageDraw.Draw(mouth).ellipse((4, 4, 32, 12), fill=(150, 60, 64, 255))
    mouth.save(folder / "mouths" / "X.png")
    x = int(mouth_at[0] - mouth.width / 2)
    y = int(mouth_at[1] - mouth.height / 2)
    view = {
        "canvas": {"w": image.width, "h": image.height},
        "layers": [{"name": "head", "url": "head.png", "x": 0, "y": 0, "w": image.width, "h": image.height, "z": 2}],
        "visemes": {"X": {"name": "mouth_X", "url": "mouths/X.png", "x": x, "y": y, "w": mouth.width, "h": mouth.height, "z": 10}},
        "expressions": {},
        "mouthQA": {"face": list(face), "eyes": [list(left), list(right)], "mouth": list(face), "mouthBox": list(face)},
    }
    (folder / "view.json").write_text(json.dumps(view), encoding="utf-8")


def _puppet(root, *, shift: int) -> Path:
    skin = Path(root) / "hero"
    skin.mkdir(parents=True)
    head = Image.new("RGBA", (240, 320), (0, 0, 0, 0))
    draw = ImageDraw.Draw(head)
    draw.ellipse((40, 20, 200, 250), fill=(232, 186, 156, 255))
    for cx in (90 + shift, 150 + shift):
        draw.ellipse((cx - 14, 80, cx + 14, 108), fill=(248, 248, 246, 255))
        draw.ellipse((cx - 5, 88, cx + 5, 100), fill=(24, 16, 14, 255))
    body = Image.new("RGBA", (240, 320), (0, 0, 0, 0))
    ImageDraw.Draw(body).rectangle((50, 30, 190, 300), fill=(32, 48, 96, 255))
    head.save(skin / "head.png")
    body.save(skin / "body.png")
    manifest = {
        "anchors": {
            "left_eye": [90 + shift, 94],
            "right_eye": [150 + shift, 94],
            "neck_pivot": [120, 240],
            "eye_radius": 14,
        },
        "calibration": {"head_bbox": [40, 20, 200, 250]},
        "framing": {"target_eye_y": 220, "body_anchor_y": 520},
        "canvas_size": [240, 320],
    }
    (skin / "puppet.json").write_text(json.dumps(manifest), encoding="utf-8")
    (skin / "views" / "front").mkdir(parents=True)
    (skin / "views" / "front" / "stale.txt").write_text("old", encoding="utf-8")
    (skin / "rig_front.json").write_text("{}", encoding="utf-8")
    return skin


def _front_view(face: list[int], *, view: str, mouth_x: float, url: str = "head.png") -> dict:
    return {
        "canvas": {"w": 240, "h": 320},
        "layers": [{
            "name": "head", "url": url, "x": 0, "y": 0, "w": 240, "h": 320, "z": 2,
        }],
        "visemes": {},
        "expressions": {},
        "mouthQA": {
            "face": face,
            "anchor": {
                "x": mouth_x, "y": 180, "view": view, "along": 0.647,
                "nose": [120, 140], "chin": [120, 240],
            },
        },
    }


def test_a_frontal_plate_is_stored_as_front_and_a_three_quarter_is_not(tmp_path):
    from products.animind_core import puppet_v3

    front_skin = _puppet(tmp_path / "front", shift=0)
    views = puppet_v3.adapt(front_skin, lambda path: path.name)
    saved = json.loads((front_skin / "views" / "front" / "view.json").read_text(encoding="utf-8"))
    anchor = saved["mouthQA"]["anchor"]
    face = saved["mouthQA"]["face"]
    assert "front" in views
    assert anchor["view"] == "front"
    assert abs((anchor["x"] - (face[0] + face[2]) / 2) / (face[2] - face[0])) <= mouth_anchor.FRONT_MAX

    turned = _puppet(tmp_path / "turned", shift=-36)
    turned_views = puppet_v3.adapt(turned, lambda path: path.name)
    assert "front" not in turned_views
    assert not (turned / "views" / "front").exists()
    assert not (turned / "rig_front.json").is_file()
    left = json.loads((turned / "views" / "left" / "view.json").read_text(encoding="utf-8"))
    assert left["mouthQA"]["anchor"]["view"] == "left"


def test_adapt_keeps_a_valid_front_when_the_master_is_three_quarter(tmp_path):
    """A midline front already on disk survives adapt of a 3/4 plate."""
    from products.animind_core import puppet_v3

    skin = _puppet(tmp_path / "kept", shift=-36)
    front = skin / "views" / "front"
    face = [40, 20, 200, 250]
    centre = (face[0] + face[2]) / 2
    Image.new("RGBA", (8, 8), (20, 220, 40, 255)).save(front / "head.png")
    (front / "stale.txt").write_text("stay", encoding="utf-8")
    stage_url = "/v1/files/hero/_front_stage/front/views/front/head.png"
    (front / "view.json").write_text(
        json.dumps(_front_view(face, view="front", mouth_x=centre, url=stage_url)),
        encoding="utf-8",
    )
    (skin / "rig_front.json").write_text(json.dumps({
        "boxes": {"face": face, "mouth": [100, 170, 140, 190]},
    }), encoding="utf-8")

    def url(path: Path) -> str:
        return "/v1/files/" + path.as_posix()

    views = puppet_v3.adapt(skin, url)
    assert "front" in views
    assert (front / "stale.txt").read_text(encoding="utf-8") == "stay"
    assert (skin / "rig_front.json").is_file()
    saved = json.loads((front / "view.json").read_text(encoding="utf-8"))
    anchor = saved["mouthQA"]["anchor"]
    assert anchor["view"] == "front"
    assert abs((anchor["x"] - centre) / (face[2] - face[0])) <= 0.01
    assert saved["layers"][0]["url"] == "head.png"
    assert "_front_stage" not in (front / "view.json").read_text(encoding="utf-8")
    public = views["front"]["layers"][0]["url"]
    assert public.endswith("views/front/head.png")
    assert "_front_stage" not in public
    assert "_front_stage" not in json.dumps(views["front"])


def test_adapt_keeps_a_front_recorded_only_on_rig_front(tmp_path):
    from products.animind_core import puppet_v3

    skin = _puppet(tmp_path / "rigonly", shift=-36)
    front = skin / "views" / "front"
    face = [40, 20, 200, 250]
    Image.new("RGBA", (4, 4), (0, 0, 0, 255)).save(front / "head.png")
    (front / "view.json").write_text(json.dumps({
        "canvas": {"w": 240, "h": 320},
        "layers": [{"name": "head", "url": "head.png", "x": 0, "y": 0, "w": 4, "h": 4, "z": 2}],
        "visemes": {},
        "expressions": {},
    }), encoding="utf-8")
    (skin / "rig_front.json").write_text(json.dumps({
        "mouthQA": {"face": face, "anchor": {"view": "front", "x": 120, "y": 180}},
    }), encoding="utf-8")
    views = puppet_v3.adapt(skin, lambda path: "/v1/files/" + path.as_posix())
    assert (front / "view.json").is_file()
    assert (skin / "rig_front.json").is_file()
    assert "front" in views
    assert views["front"]["layers"][0]["url"].endswith("views/front/head.png")
    assert "_front_stage" not in views["front"]["layers"][0]["url"]


def test_adapt_drops_a_three_quarter_plate_tagged_as_front(tmp_path):
    from products.animind_core import puppet_v3

    skin = _puppet(tmp_path / "lie", shift=-36)
    front = skin / "views" / "front"
    face = [40, 20, 200, 250]
    (front / "view.json").write_text(
        json.dumps(_front_view(face, view="left", mouth_x=face[0] + 8)),
        encoding="utf-8",
    )
    (skin / "rig_front.json").write_text(json.dumps({
        "mouthQA": {"face": face, "anchor": {"view": "front", "x": 120, "y": 180}},
    }), encoding="utf-8")
    views = puppet_v3.adapt(skin, lambda path: path.name)
    assert "front" not in views
    assert not front.exists()
    assert not (skin / "rig_front.json").is_file()


def test_reposition_drops_a_three_quarter_plate_filed_as_front(tmp_path):
    image, face, left, right = _portrait("left")
    skin = tmp_path / "hero"
    _write_view(skin, "front", image, face, left, right, (160, 240))
    (skin / "rig_front.json").write_text("{}", encoding="utf-8")
    mouth_anchor.reposition_skin(skin)
    assert not (skin / "views" / "front").exists()
    assert not (skin / "rig_front.json").is_file()


def test_gate_fails_a_nose_mouth_and_reposition_fixes_it_without_an_image_call(tmp_path):
    image, face, left, right = _portrait("front")
    skin = tmp_path / "hero"
    nose_y = (left[3] + right[3]) / 2
    _write_view(skin, "front", image, face, left, right, (160, nose_y))
    turned, turned_face, tl, tr = _portrait("left")
    _write_view(skin, "left", turned, turned_face, tl, tr, ((turned_face[0] + turned_face[2]) / 2 + 40, nose_y))
    _write_view(skin, "right", turned.transpose(Image.Transpose.FLIP_LEFT_RIGHT), turned_face, tl, tr, (160, nose_y))
    (skin / "rig.json").write_text(json.dumps({
        "boxes": {"face": list(turned_face), "left_eye": list(tl), "right_eye": list(tr), "mouth": [140, 200, 180, 220]},
    }), encoding="utf-8")
    (skin / "rig_front.json").write_text((skin / "rig.json").read_text(encoding="utf-8"), encoding="utf-8")
    before = qa.mouth_anchor(skin)
    assert before["pass"] is False
    assert qa.status_for(qa.approval_failures(skin)) == "rejected"
    shifts = mouth_anchor.reposition_skin(skin)
    assert any(shifts.values())
    after = qa.mouth_anchor(skin)
    assert after["pass"] is True, after
    assert qa.status_for([]) == "ready"


def test_mock_rig_passes_the_mouth_anchor_gate(tmp_path):
    from products.animind_core.rig import build_mock_rig

    built = build_mock_rig("Ada", tmp_path, lambda path: path.name)
    assert "mouthAnchor" not in built["qa"]["failed"]
    assert "landmarks" not in built["qa"]["failed"]
    assert "mouthOpen" not in built["qa"]["failed"]
    gate = qa.mouth_anchor(tmp_path)
    assert gate["pass"] is True, gate
    assert built["qa"]["score"] == 100
