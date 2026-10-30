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
