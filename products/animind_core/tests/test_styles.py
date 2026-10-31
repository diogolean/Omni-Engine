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
        spr = np.asarray(_mouth_sprite(base, shot, box, (30, 45, 90, 70), sy=sy))[..., :3].astype(int)
        ref = np.asarray(base.crop(box))[..., :3].astype(int)
        assert np.abs(spr[:20] - ref[:20]).max() == 0  # mustache (rows 10-25) not squashed or doubled
        assert (spr[30:] != ref[30:]).any()  # mouth still opens


def test_rig_has_torso_neck_mouthless_head_and_mouth_layer(tmp_path):
    from products.animind_core.rig import build_mock_rig
    views = build_mock_rig("Test", tmp_path, lambda p: str(p))["views"]
    for name in ("front", "left", "right"):
        layers = {l["name"]: l for l in views[name]["layers"]}
        assert {"torso", "neck", "head", "brows"} <= set(layers)
        assert layers["torso"]["z"] < layers["neck"]["z"] < layers["head"]["z"] < views[name]["visemes"]["X"]["z"]
    left = tmp_path / "views" / "left"
    head = Image.open(left / "head.png").convert("RGBA")
    hx, hy = next((l["x"], l["y"]) for l in views["left"]["layers"] if l["name"] == "head")
    # the mock's lip line (dark red at y=440, x 285..355) is gone from the face layer
    px = np.asarray(head)[440 - hy, 320 - hx]
    assert not (px[0] < 160 and px[1] < 80), px
    brow = Image.open(left / "brows.png").convert("RGBA")
    assert np.asarray(brow)[..., 3].max() == 255


def test_mouth_sprite_soft_alpha_matches_base_and_hides_seam():
    from PIL import Image, ImageDraw

    from products.animind_core.rig import _mouth_sprite, _mouthless
    base = Image.new("RGBA", (160, 160), (230, 190, 160, 255))
    ImageDraw.Draw(base).rectangle((50, 70, 110, 80), fill=(40, 30, 30, 255))  # mustache
    shot = base.copy()
    ImageDraw.Draw(shot).rectangle((0, 0, 159, 159), fill=(250, 215, 185, 255))  # lighter redraw
    ImageDraw.Draw(shot).ellipse((65, 82, 95, 105), fill=(120, 20, 30, 255))  # open cavity
    box, mouth = (20, 50, 140, 130), (50, 68, 110, 90)
    spr = _mouth_sprite(base, shot, box, mouth)
    a = np.asarray(spr.getchannel("A"))
    assert a[0].max() == 0 and a[:, 0].max() == 0  # no rectangle edge
    rgb = np.asarray(spr)[..., :3].astype(int)
    ref = np.asarray(base.crop(box))[..., :3].astype(int)
    ring = (a > 30) & (a < 250)
    ring[28:60, 40:80] = False  # the open cavity itself (shot coords 65-95 x 82-105)
    assert np.abs(rgb[ring] - ref[ring]).max() <= 2  # soft rim = base colours, no lighter patch
    head = np.asarray(_mouthless(base, box, mouth))
    # where the sprite is not fully opaque the head is untouched, so no second mustache shows
    full = np.zeros(head.shape[:2], bool)
    full[50:130, 20:140] = a > 250
    assert (head[~full] == np.asarray(base)[~full]).all()


def test_viseme_hard_cut_and_iris_clamp():
    from products.animind_core.scene import viseme_mix
    cues = [{"t": 0.0, "v": "X"}, {"t": 1.0, "v": "D"}]
    assert viseme_mix(cues, 0.5) == ("X", "X", 1.0)
    # one frame after a change only the new mouth is drawn: blending two mouth shapes shows two mouths
    cur, _prev, w = viseme_mix(cues, 1.02)
    assert cur == "D" and w == 1.0


def test_short_open_cues_are_softened():
    from products.animind_core.render import smooth_visemes
    out = smooth_visemes([{"t": 0.0, "v": "X"}, {"t": 0.1, "v": "D"}, {"t": 0.15, "v": "B"},
                          {"t": 0.4, "v": "D"}, {"t": 0.6, "v": "E"}, {"t": 0.62, "v": "C"}, {"t": 0.8, "v": "X"}])
    assert [c["v"] for c in out] == ["X", "C", "B", "D", "C", "X"]


def test_closed_lids_are_sharp():
    from PIL import Image

    from products.animind_core.rig import _eyes_closed
    base = Image.new("RGBA", (200, 120), (230, 190, 160, 255))
    lids = np.asarray(_eyes_closed(base, [(40, 30, 100, 70)], (230, 190, 160), (30, 20, 20)).getchannel("A")).astype(int)
    row = lids[50, 30:110]
    ramp = ((row > 10) & (row < 245)).sum()
    assert ramp <= 6  # edge within ~2-3 px per side, not a blurry blob


def test_frameqa_sees_two_stacked_mouths():
    from PIL import Image, ImageDraw

    from products.animind_core.frameqa import measure
    one = Image.new("RGB", (120, 80), (235, 190, 170))
    ImageDraw.Draw(one).ellipse((30, 30, 90, 50), fill=(90, 20, 30))
    two = one.copy()
    ImageDraw.Draw(two).ellipse((32, 56, 88, 72), fill=(90, 20, 30))
    a, b = measure(np.asarray(one), (0, 0, 120, 80)), measure(np.asarray(two), (0, 0, 120, 80))
    assert a["blobs"] == 1 and b["blobs"] == 2 and b["cav"] > a["cav"]
