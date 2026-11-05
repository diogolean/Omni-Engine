"""A three-quarter Einstein front becomes a frontal camera without an image call."""
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from products.animind_core import avatar, front_plate, qa, rig
from products.animind_core.tests.test_einstein_product import _offline

_KEEP = ("mouthAnchor", "mouthOpen", "doubleMouth", "skin", "landmarks")


def _shift_front(skin: Path, dx: int) -> None:
    """Slide the front features off the face centre. The face box stays."""
    folder = skin / "views" / "front"
    view = json.loads((folder / "view.json").read_text(encoding="utf-8"))
    for layer in view.get("layers") or []:
        layer["x"] = int(layer["x"]) + dx
    for family in ("visemes", "expressions"):
        for layer in (view.get(family) or {}).values():
            layer["x"] = int(layer["x"]) + dx
    qa_doc = view.setdefault("mouthQA", {})
    for eye in qa_doc.get("eyes") or []:
        eye[0] += dx
        eye[2] += dx
    for box_key in ("mouth", "mouthBox"):
        box = qa_doc.get(box_key)
        if box:
            box[0] += dx
            box[2] += dx
    anchor = qa_doc.get("anchor") or {}
    if "x" in anchor:
        anchor["x"] = int(anchor["x"]) + dx
        anchor["view"] = "left"
    for iris in (view.get("eyeRig") or {}).get("irises") or []:
        iris[0] = float(iris[0]) + dx
    (folder / "view.json").write_text(json.dumps(view, indent=2), encoding="utf-8")
    rig_doc = json.loads((skin / "rig_front.json").read_text(encoding="utf-8"))
    for key in ("left_eye", "right_eye", "mouth"):
        box = rig_doc["boxes"][key]
        box[0] += dx
        box[2] += dx
    rig_doc["mouthBox"] = rig_doc["boxes"]["mouth"]
    (skin / "rig_front.json").write_text(json.dumps(rig_doc, indent=2), encoding="utf-8")


def _paint_cheeks(skin: Path, view_name: str) -> None:
    """Blue on the viewer's left cheek, red on the right. The two sides stay distinct."""
    folder = skin / "views" / view_name
    view = json.loads((folder / "view.json").read_text(encoding="utf-8"))
    rig_name = "rig_front.json" if view_name == "front" else "rig.json"
    boxes = json.loads((skin / rig_name).read_text(encoding="utf-8"))["boxes"]
    axis = int(round(front_plate._axis(boxes)))
    head = next(layer for layer in view["layers"] if layer["name"] == "head")
    image = Image.open(folder / head["url"]).convert("RGBA")
    draw = ImageDraw.Draw(image)
    y = int(boxes["left_eye"][3]) + 36 - int(head["y"])
    near = axis - 48 - int(head["x"])
    far = axis + 48 - int(head["x"])
    draw.rectangle((near, y, near + 10, y + 10), fill=(20, 40, 220, 255))
    draw.rectangle((far, y, far + 10, y + 10), fill=(220, 30, 40, 255))
    image.save(folder / head["url"])


def _colors(image: Image.Image) -> set[tuple[int, int, int]]:
    arr = np.asarray(image.convert("RGBA"))
    found: set[tuple[int, int, int]] = set()
    for rgb in ((20, 40, 220), (220, 30, 40)):
        hit = np.all(arr[..., :3] == rgb, axis=-1) & (arr[..., 3] > 200)
        if int(hit.sum()) > 8:
            found.add(rgb)
    return found


def test_frontalize_sprite_keeps_both_cheeks():
    image = Image.new("RGBA", (40, 10), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((2, 2, 12, 8), fill=(10, 20, 200, 255))
    draw.rectangle((28, 2, 36, 8), fill=(200, 20, 10, 255))
    # Stretch the right half. The red block must not become a copy of the blue one.
    src = np.array([0.0, 20.0, 39.0])
    dst = np.array([0.0, 16.0, 39.0])
    out, origin, _y = front_plate.frontalize_sprite(image, 0, 0, src, dst)
    arr = np.asarray(out)
    blue = np.all(arr[..., :3] == (10, 20, 200), axis=-1) & (arr[..., 3] > 200)
    red = np.all(arr[..., :3] == (200, 20, 10), axis=-1) & (arr[..., 3] > 200)
    assert int(blue.sum()) > 8 and int(red.sum()) > 8
    assert origin <= 0


def test_cached_three_quarter_front_becomes_a_camera_front(tmp_path, monkeypatch):
    _offline(monkeypatch, tmp_path)
    plate = tmp_path / "assets" / "puppets" / rig.EINSTEIN_PLATE_ID
    painted = rig.build_einstein_product(plate, lambda path: path.name)
    assert painted["qa"]["gates"]["front"]["pass"] is True
    face = json.loads((plate / "rig_front.json").read_text(encoding="utf-8"))["boxes"]["face"]
    shift = -int(0.12 * (face[2] - face[0]))
    _shift_front(plate, shift)
    _paint_cheeks(plate, "left")
    turned = qa.front_symmetry(
        json.loads((plate / "rig_front.json").read_text(encoding="utf-8"))["boxes"],
    )
    assert turned["pass"] is False and turned["value"] > 0.05
    source_head = (plate / "views" / "front" / "head.png").read_bytes()
    left_head = (plate / "views" / "left" / "head.png").read_bytes()
    plate_png = (plate / "character.png").read_bytes()

    skin = tmp_path / "product"
    built = rig.build_einstein_product(
        skin, lambda path: path.name, cached=plate, searched=[],
    )
    assert (plate / "views" / "front" / "head.png").read_bytes() == source_head
    assert (plate / "character.png").read_bytes() == plate_png
    assert (skin / "views" / "left" / "head.png").read_bytes() == left_head
    gates = built["qa"]["gates"]
    assert gates["front"]["pass"] is True
    assert gates["front"]["value"] <= 0.05
    assert gates["edges"]["pass"], gates["edges"]
    for key in _KEEP:
        assert gates[key]["pass"], (key, gates[key])
    assert built["qa"]["score"] >= 90
    assert built["qa"]["costUsd"] == 0.0
    assert "camera warped from the left view" in built["note"]
    assert built["plateFrontal"] is True

    view = json.loads((skin / "views" / "front" / "view.json").read_text(encoding="utf-8"))
    head = next(layer for layer in view["layers"] if layer["name"] == "head")
    image = Image.open(skin / "views" / "front" / head["url"]).convert("RGBA")
    # The front is the left view seen by a frontal camera. Both cheeks survive.
    # A near-half mirror would have painted the red cheek blue.
    assert _colors(image) == {(20, 40, 220), (220, 30, 40)}
    anchor = view["mouthQA"]["anchor"]
    assert anchor["view"] == "front"
    width = max(1, int(view["mouthQA"]["face"][2]) - int(view["mouthQA"]["face"][0]))
    mouth_centre = (int(view["mouthQA"]["face"][0]) + int(view["mouthQA"]["face"][2])) / 2
    assert abs((anchor["x"] - mouth_centre) / width) <= 0.01


def test_rerig_of_a_three_quarter_plate_warps_without_create_puppet(tmp_path, monkeypatch):
    _offline(monkeypatch, tmp_path)
    calls = {"n": 0}

    def create_puppet(*_args, **_kwargs):
        calls["n"] += 1
        raise AssertionError("create_puppet must not run on a complete plate")

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.create_puppet",
        create_puppet,
    )
    plate = tmp_path / "assets" / "puppets" / rig.EINSTEIN_PLATE_ID
    rig.build_einstein_product(plate, lambda path: path.name)
    face = json.loads((plate / "rig_front.json").read_text(encoding="utf-8"))["boxes"]["face"]
    _shift_front(plate, -int(0.12 * (face[2] - face[0])))
    plate_png = (plate / "character.png").read_bytes()
    left_head = (plate / "views" / "left" / "head.png").read_bytes()
    built = avatar.rerig_from_plate(
        plate,
        description="Albert Einstein",
        url=lambda path: path.name,
        character_id=rig.EINSTEIN_PLATE_ID,
        name="Albert Einstein",
        edit=False,
    )
    assert calls["n"] == 0
    assert built["qa"]["costUsd"] == 0.0
    assert built["frontShot"] == "facing_front"
    assert (plate / "character.png").read_bytes() == plate_png
    assert (plate / "views" / "left" / "head.png").read_bytes() == left_head
    gate = qa.front_symmetry(json.loads((plate / "rig_front.json").read_text(encoding="utf-8"))["boxes"])
    assert gate["pass"] is True and gate["value"] <= 0.05
