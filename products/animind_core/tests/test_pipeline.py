"""Generate-then-verify pieces that need no Gemini: parts-sheet slicing/registration and QA gates."""
import json

import numpy as np
from PIL import Image, ImageDraw


def _face(size=(400, 400)):
    im = Image.new("RGBA", size, (230, 190, 160, 255))
    d = ImageDraw.Draw(im)
    d.ellipse((150, 130, 190, 160), fill=(255, 255, 255, 255)); d.ellipse((160, 135, 180, 155), fill=(40, 30, 20, 255))
    d.ellipse((220, 130, 260, 160), fill=(255, 255, 255, 255)); d.ellipse((230, 135, 250, 155), fill=(40, 30, 20, 255))
    d.line((170, 250, 230, 250), fill=(150, 40, 50, 255), width=6)
    d.polygon([(200, 180), (190, 220), (210, 220)], fill=(200, 150, 120, 255))
    return im


def test_sheet_slices_twelve_cells_and_registers_on_master():
    from products.animind_core import partsheet
    base = _face()
    boxes = {"face": (120, 100, 290, 300), "left_eye": (150, 130, 190, 160), "right_eye": (220, 130, 260, 160), "mouth": (165, 240, 235, 260)}
    sheet = Image.new("RGB", (4 * 200, 3 * 150), (255, 0, 255))
    lower = base.crop((140, 190, 260, 290)).convert("RGB").resize((180, 150))
    eyes = base.crop((135, 115, 275, 170)).convert("RGB").resize((180, 70))
    for i in range(12):
        r, c = divmod(i, 4)
        cell = lower if i < 9 else eyes
        sheet.paste(cell, (c * 200 + 10, r * 150 + (0 if i < 9 else 40)))
    cells = partsheet.slice_grid(sheet)
    assert len(cells) == 12
    got = partsheet.load_image(sheet, base, boxes)
    assert got and got["ncc"]["mouth"] > 0.8 and got["ncc"]["eyes"] > 0.8 and set(partsheet.ORDER) <= set(got["good"])
    m = np.asarray(got["mouths"]["X"])
    assert m[250, 200, 3] == 255 and abs(int(m[250, 200, 0]) - 150) < 30  # lip line lands on the master's lip line


def test_qa_gates_score_a_mock_rig(tmp_path):
    from products.animind_core import qa
    from products.animind_core.rig import build_mock_rig
    build_mock_rig("Test", tmp_path, lambda p: str(p))
    if not (tmp_path / "rig.json").exists():
        (tmp_path / "rig.json").write_text(json.dumps({"boxes": {"face": [200, 150, 440, 480], "left_eye": [250, 260, 300, 290],
                                                                  "right_eye": [340, 260, 390, 290], "mouth": [285, 420, 355, 460]}}))
    report = qa.finalize(tmp_path, 0.0, {})
    assert set(report["gates"]) == set(qa.WEIGHTS)
    assert report["gates"]["edges"]["pass"]  # every mouth sprite fades out before its box
    assert (tmp_path / "qa.json").exists() and (tmp_path / "qa_sheet.png").exists()


def _head_view(tmp_path, paint):
    im = Image.new("RGBA", (200, 200), (235, 180, 160, 255))  # pinkish anime skin
    paint(ImageDraw.Draw(im))
    view = tmp_path / "v"; view.mkdir(parents=True)
    im.save(view / "head.png")
    (view / "view.json").write_text(json.dumps({"layers": [{"name": "head", "url": "head.png", "x": 0, "y": 0}]}))
    return view


def test_residual_mouth_gate_ignores_skin_and_beard_but_catches_lips(tmp_path):
    from products.animind_core import qa
    box = [70, 100, 130, 140]
    clean = _head_view(tmp_path / "a", lambda d: d.rectangle((40, 110, 160, 200), fill=(70, 50, 38, 255)))
    lips = _head_view(tmp_path / "b", lambda d: d.ellipse((80, 112, 120, 138), fill=(200, 60, 70, 255)))
    assert qa.residual_mouth(clean, box)["pass"]
    assert not qa.residual_mouth(lips, box)["pass"]
