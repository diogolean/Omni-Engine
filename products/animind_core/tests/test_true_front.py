"""A three-quarter Einstein front becomes a midline bust without an image call."""
import json
from pathlib import Path

from PIL import Image, ImageDraw

from products.animind_core import front_plate, qa, rig
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


def _paint_cheeks(skin: Path) -> tuple[int, int, int]:
    """Blue on the near cheek, red on the far cheek, the same distance off the nose."""
    folder = skin / "views" / "front"
    view = json.loads((folder / "view.json").read_text(encoding="utf-8"))
    boxes = json.loads((skin / "rig_front.json").read_text(encoding="utf-8"))["boxes"]
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
    return axis, int(head["y"]) + y + 5, 48


def test_symmetrize_sprite_copies_the_near_half():
    image = Image.new("RGBA", (40, 10), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((2, 2, 12, 8), fill=(10, 20, 200, 255))
    draw.rectangle((28, 2, 36, 8), fill=(200, 20, 10, 255))
    out, origin = front_plate.symmetrize_sprite(image, 0, 20, keep_left=True)

    def at(canvas_x: int) -> tuple[int, int, int]:
        return out.getpixel((canvas_x - origin, 5))[:3]

    # x=8 is the blue block. Its mirror across 20 is x=32, where the red block sat.
    assert at(8) == (10, 20, 200)
    assert at(32) == (10, 20, 200)


def test_cached_three_quarter_front_becomes_a_midline_bust(tmp_path, monkeypatch):
    _offline(monkeypatch, tmp_path)
    plate = tmp_path / "assets" / "puppets" / rig.EINSTEIN_PLATE_ID
    painted = rig.build_einstein_product(plate, lambda path: path.name)
    assert painted["qa"]["gates"]["front"]["pass"] is True
    face = json.loads((plate / "rig_front.json").read_text(encoding="utf-8"))["boxes"]["face"]
    shift = -int(0.12 * (face[2] - face[0]))
    _shift_front(plate, shift)
    _axis, sample_y, _reach = _paint_cheeks(plate)
    turned = qa.front_symmetry(
        json.loads((plate / "rig_front.json").read_text(encoding="utf-8"))["boxes"],
    )
    assert turned["pass"] is False and turned["value"] > 0.05
    source_head = (plate / "views" / "front" / "head.png").read_bytes()
    left_head = (plate / "views" / "left" / "head.png").read_bytes()

    skin = tmp_path / "product"
    built = rig.build_einstein_product(
        skin, lambda path: path.name, cached=plate, searched=[],
    )
    assert (plate / "views" / "front" / "head.png").read_bytes() == source_head
    assert (skin / "views" / "left" / "head.png").read_bytes() == left_head
    gates = built["qa"]["gates"]
    assert gates["front"]["pass"] is True
    assert gates["front"]["value"] <= 0.05
    assert gates["edges"]["pass"], gates["edges"]
    for key in _KEEP:
        assert gates[key]["pass"], (key, gates[key])
    assert built["qa"]["score"] >= 90
    assert built["qa"]["costUsd"] == 0.0
    assert "mirrored from the three-quarter plate" in built["note"]
    assert built["plateFrontal"] is True

    view = json.loads((skin / "views" / "front" / "view.json").read_text(encoding="utf-8"))
    head = next(layer for layer in view["layers"] if layer["name"] == "head")
    image = Image.open(skin / "views" / "front" / head["url"]).convert("RGBA")
    face_box = json.loads((skin / "rig_front.json").read_text(encoding="utf-8"))["boxes"]["face"]
    centre = (face_box[0] + face_box[2]) / 2
    # The install slides the old midline onto the face centre.
    landed = centre
    # The blue square is 10px starting 48px left of the old midline. Its centre
    # is 43px off that line, and the install slides the line onto the face centre.
    near_x = int(round(landed - 43)) - int(head["x"])
    far_x = int(round(landed + 43)) - int(head["x"])
    row = sample_y - int(head["y"])
    near = image.getpixel((near_x, row))
    far = image.getpixel((far_x, row))
    assert near[:3] == (20, 40, 220)
    assert far[:3] == (20, 40, 220)
    anchor = view["mouthQA"]["anchor"]
    assert anchor["view"] == "front"
    width = max(1, int(view["mouthQA"]["face"][2]) - int(view["mouthQA"]["face"][0]))
    mouth_centre = (int(view["mouthQA"]["face"][0]) + int(view["mouthQA"]["face"][2])) / 2
    assert abs((anchor["x"] - mouth_centre) / width) <= 0.01
