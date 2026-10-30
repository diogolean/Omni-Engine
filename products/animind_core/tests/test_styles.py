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
