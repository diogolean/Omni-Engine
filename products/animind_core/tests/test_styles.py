import numpy as np
import pytest
from PIL import Image

from products.animind_core import look, rig, styles


def test_catalog_and_pick():
    ids = [s["id"] for s in styles.catalog()]
    assert {"cartoon", "anime", "retro", "realista", "cyberpunk"} <= set(ids)
    assert styles.pick("nope") == "cartoon" and styles.pick("retro") == "retro"


def test_style_feeds_the_gemini_prompt():
    p = rig.STYLE_PROMPT.format(subject="Sócrates", style=styles.prompt_style("cyberpunk"))
    assert "neon" in p.lower() and "magenta #FF00FF" in p
    assert "{" not in p


@pytest.mark.parametrize("style", ["retro", "cyberpunk", "anime", "aquarela", "realista"])
def test_style_changes_the_look(style):
    base = look.grade_lut("studio")
    assert styles.lut(base, "cartoon") == base
    assert styles.lut(base, style) != base
    plate = Image.new("RGBA", (32, 32), (90, 140, 200, 255))
    tinted = styles.tint_plate(plate, style)
    assert tinted.size == plate.size


def test_front_view_is_rigged_in_mock(tmp_path):
    built = rig.build_mock_rig("X", tmp_path, lambda p: str(p))
    assert (tmp_path / "views" / "front" / "view.json").exists()
    v = rig.load_view(tmp_path, "front")
    assert "front" in v["layers"][0]["url"] and len(v["visemes"]) == 9
    assert built["viewStatus"]["front"] == "mock (front rig)"
    assert np.asarray(Image.open(tmp_path / "character_front.png")).shape[2] == 4


def test_frontality_separates_front_from_three_quarter():
    from products.animind_core import rig
    # boxes measured on real rigs: nietzsche_9833318b front vs socrates_61afcc43 "front" (3/4)
    front = {"face": (100, 100, 300, 340), "left_eye": (140, 180, 190, 200), "right_eye": (210, 180, 260, 200), "mouth": (170, 280, 230, 300)}
    three_q = {"face": (100, 100, 300, 340), "left_eye": (170, 180, 220, 200), "right_eye": (240, 180, 275, 200), "mouth": (200, 280, 260, 300)}
    assert rig.frontality(front)["frontal"] is True
    assert rig.frontality(three_q)["frontal"] is False


def test_realista_and_aquarela_have_their_own_look():
    from PIL import Image
    from products.animind_core import styles
    plate = Image.new("RGBA", (64, 64), (90, 80, 70, 255))
    for st in ("realista", "aquarela"):
        assert styles.texture(st, (64, 64)) is not None
        assert styles.tint_plate(plate, st).size == (64, 64)


def test_squashed_open_mouth_keeps_mustache_rows_intact():
    from PIL import Image, ImageDraw
    from products.animind_core.rig import _mouth_sprite
    base = Image.new("RGBA", (120, 120), (230, 190, 160, 255))
    ImageDraw.Draw(base).rectangle((20, 40, 100, 55), fill=(30, 30, 30, 255))  # mustache
    shot = base.copy()
    ImageDraw.Draw(shot).ellipse((45, 60, 75, 90), fill=(120, 20, 30, 255))  # open mouth
    box = (10, 30, 110, 100)
    for sy in (0.3, 0.45, 0.6):
        spr = np.asarray(_mouth_sprite(base, shot, box, sy=sy))[..., :3].astype(int)
        ref = np.asarray(base.crop(box))[..., :3].astype(int)
        assert np.abs(spr[:20] - ref[:20]).max() == 0  # mustache (rows 10-25) not squashed or doubled
        assert (spr[30:] != ref[30:]).any()  # mouth still opens


def test_rig_has_torso_neck_mouthless_head_and_mouth_layer(tmp_path):
    from products.animind_core.rig import build_mock_rig
    views = build_mock_rig("Test", tmp_path, lambda p: str(p))["views"]
    for name in ("front", "left", "right"):
        layers = {l["name"]: l for l in views[name]["layers"]}
        assert {"torso", "neck", "head"} <= set(layers)
        assert layers["torso"]["z"] < layers["neck"]["z"] < layers["head"]["z"] < views[name]["visemes"]["X"]["z"]
    left = tmp_path / "views" / "left"
    head = Image.open(left / "head.png").convert("RGBA")
    hx, hy = next((l["x"], l["y"]) for l in views["left"]["layers"] if l["name"] == "head")
    # the mock's lip line (dark red at y=440, x 285..355) is gone from the face layer
    px = np.asarray(head)[440 - hy, 320 - hx]
    assert not (px[0] < 160 and px[1] < 80), px
