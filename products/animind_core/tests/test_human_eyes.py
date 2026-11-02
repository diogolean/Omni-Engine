"""Human eyelids follow the drawn lid. No image API and no MediaPipe."""
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from products.animind_core import eyes, frameqa, human_eyes, scene


def _portrait() -> tuple[Image.Image, dict, list]:
    image = Image.new("RGBA", (160, 180), (0, 0, 0, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((30, 20, 130, 150), fill=(232, 186, 156, 255))
    # A curved eye, not a rectangle: the lid line dips in the middle.
    upper = [(48, 62), (64, 52), (80, 50), (96, 52), (112, 62)]
    lower = [(48, 70), (64, 78), (80, 80), (96, 78), (112, 70)]
    draw.polygon(upper + list(reversed(lower)), fill=(248, 246, 242, 255))
    draw.ellipse((72, 58, 88, 74), fill=(20, 16, 14, 255))
    brow = [(50, 36), (80, 30), (110, 36), (108, 42), (80, 40), (52, 42)]
    draw.polygon(brow, fill=(40, 28, 22, 255))
    eye = {"upper": upper, "lower": lower, "iris": (80, 66), "radius": 7}
    return image, eye, brow


def test_closed_lid_follows_the_drawn_line_and_uses_the_cheek_colour():
    plate, eye, brow = _portrait()
    built = human_eyes.build(plate, eyes_geom=[eye, eye], brows=[brow, [(120, 34), (140, 30), (150, 36), (148, 42), (130, 40)]])
    skin = built["skin"]
    assert skin != (128, 128, 128)
    assert abs(skin[0] - 232) < 30
    drawn = human_eyes.lid_line_y(eye["upper"], eye["lower"], 1.0, 80)
    edge = human_eyes._alpha_edge_y(built["eyes_closed"], 80, drawn)
    assert human_eyes.lids_near_line(edge, drawn, tol=4)
    # A grey box over the same span is not the lid line.
    box = Image.new("RGBA", plate.size, (0, 0, 0, 0))
    ImageDraw.Draw(box).rectangle((48, 50, 112, 80), fill=(128, 128, 128, 255))
    box_edge = human_eyes._alpha_edge_y(box, 48, 0)
    assert not human_eyes.lids_near_line(box_edge, human_eyes.lid_line_y(eye["upper"], eye["lower"], 1.0, 48), tol=4)


def test_iris_stays_in_the_sclera_and_the_brow_stays_above_the_eye():
    plate, eye, brow = _portrait()
    built = human_eyes.build(plate, eyes_geom=[eye], brows=[brow])
    iris = built["qa"]["iris"][0]
    mask = built["eye_socket"].getchannel("A")
    assert mask.getpixel((iris[0], iris[1])) > 128
    assert human_eyes.brow_above_eye(built["qa"]["browBottom"], built["qa"]["eyeTop"])
    # The hole where the brow was cut is no longer the brow ink.
    bare = built["head"].getpixel((80, 32))
    assert bare[0] > 80  # inpainted toward skin, not the dark brow


def test_emotions_move_the_same_mouth_as_the_brows_and_the_sheet_is_free(tmp_path: Path):
    plate, eye, brow = _portrait()
    for name in human_eyes.EMOTIONS:
        spec = human_eyes.MOUTH[name]
        scripted = scene.EMO_MOUTH[human_eyes.SCRIPT_EMOTION[name]]
        assert spec == scripted
        assert name in scene.EMO_BROW_POSE or human_eyes.SCRIPT_EMOTION[name] in scene.EMO_BROW_POSE
    skin = tmp_path / "albert_einstein"
    skin.mkdir()
    plate.save(skin / "character.png")
    built = human_eyes.build(plate, eyes_geom=[eye], brows=[brow])
    human_eyes.save(skin, built)
    path = human_eyes.render_still_sheet(skin, skin / "emotion_sheet.png")
    assert path.is_file() and path.stat().st_size > 1000
    saved = __import__("json").loads((skin / "human_eyes" / "geometry.json").read_text(encoding="utf-8"))
    assert set(saved["emotions"]) == set(human_eyes.EMOTIONS)
    assert saved["emotions"]["surprised"]["lid"] == 0.0
    assert saved["emotions"]["angry"]["lid"] > saved["emotions"]["neutral"]["lid"]


def test_blink_interval_is_three_to_six_seconds_and_closes_in_about_120ms():
    assert eyes.BLINK_MIN == 3.0 and eyes.BLINK_MAX == 6.0
    assert eyes.BLINK_CLOSE_S == pytest.approx(0.12)
    states = [eyes.blink(i / 24, 7) for i in range(24 * 30)]
    assert "closed" in states and "half" in states


def test_frame_qa_rejects_a_lid_off_the_line_an_escaped_iris_and_a_low_brow():
    good = {
        "lidY": 40, "lineY": 42, "iris": [30, 36, 4], "sclera": [10, 20, 50, 48],
        "browBottom": 16, "eyeTop": 22,
    }
    assert human_eyes.frame_faults(good) == []
    bad, _report = frameqa.facial_check({"frames": [{"t": 0.0, "faces": [{"char": 0, "blink": "open", "emotion": "neutro", "brows": [0, 0], "humanEyes": {**good, "lidY": 60}}]}]})
    assert any("lid" in message for _i, message in bad)
    bad, _report = frameqa.facial_check({"frames": [{"t": 0.0, "faces": [{"char": 0, "blink": "open", "emotion": "neutro", "brows": [0, 0], "humanEyes": {**good, "iris": [2, 2, 4]}}]}]})
    assert any("sclera" in message for _i, message in bad)
    bad, _report = frameqa.facial_check({"frames": [{"t": 0.0, "faces": [{"char": 0, "blink": "open", "emotion": "neutro", "brows": [0, 0], "humanEyes": {**good, "browBottom": 30, "eyeTop": 22}}]}]})
    assert any("brow" in message for _i, message in bad)


def test_still_sheet_command_does_not_import_an_image_client(monkeypatch, tmp_path, capsys):
    plate, eye, brow = _portrait()
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    skin = tmp_path / "assets" / "puppets" / "albert_einstein"
    skin.mkdir(parents=True)
    plate.save(skin / "character.png")
    human_eyes.save(skin, human_eyes.build(plate, eyes_geom=[eye], brows=[brow]))

    def boom(*_a, **_k):
        raise AssertionError("still sheet must not call the image model")

    monkeypatch.setattr("products.aiwake.animator.factory.create_puppet.generate_character_image", boom)
    human_eyes.main(["sheet", "einstein"])
    assert "emotion_sheet.png" in capsys.readouterr().out
