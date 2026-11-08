"""Labeled fixtures for eye control. None of them call an image provider."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from products.animind_core import eye_control


def _layer(path: Path, size: tuple[int, int], paint) -> None:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    paint(ImageDraw.Draw(image))
    image.save(path)


def _view(root: Path, *, shift: tuple[int, int] = (8, 6), iris_at: tuple[int, int] = (40, 24)) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    _layer(root / "head.png", (80, 60), lambda draw: draw.rectangle((0, 0, 79, 59), fill=(220, 190, 160, 255)))
    _layer(root / "eye_white.png", (80, 60), lambda draw: draw.ellipse((30, 16, 50, 34), fill=(250, 250, 250, 255)))

    def socket(draw: ImageDraw.ImageDraw) -> None:
        draw.ellipse((30, 16, 50, 34), fill=(255, 255, 255, 255))

    def iris(draw: ImageDraw.ImageDraw) -> None:
        x, y = iris_at
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(20, 40, 80, 255))

    _layer(root / "eye_socket.png", (80, 60), socket)
    _layer(root / "iris.png", (80, 60), iris)
    view = {
        "canvas": {"w": 80, "h": 60},
        "eyeRig": {"maxShift": list(shift), "irises": [[iris_at[0], iris_at[1], 4]]},
        "layers": [
            {"name": "head", "url": str(root / "head.png"), "x": 0, "y": 0, "w": 80, "h": 60, "z": 1},
            {"name": "eye_white", "url": str(root / "eye_white.png"), "x": 0, "y": 0, "w": 80, "h": 60, "z": 2},
            {"name": "iris", "url": str(root / "iris.png"), "x": 0, "y": 0, "w": 80, "h": 60, "z": 3},
            {"name": "eye_socket", "url": str(root / "eye_socket.png"), "x": 0, "y": 0, "w": 80, "h": 60, "z": 4},
        ],
    }
    return view


def test_movable_iris_is_ready_only_after_four_directions(tmp_path: Path):
    report = eye_control.probe_view(_view(tmp_path / "good"))
    assert report["gaze"] == "separable"
    assert report["reason"] is None
    assert report["travel"][0] >= 1 and report["travel"][1] >= 1
    for name in ("left", "right", "up", "down"):
        assert report["probes"][name]["pass"] is True
    assert report["probes"]["left"]["delta"] < 0
    assert report["probes"]["right"]["delta"] > 0
    assert report["probes"]["up"]["delta"] < 0
    assert report["probes"]["down"]["delta"] > 0


def test_socket_that_cannot_hold_a_shift_is_not_ready(tmp_path: Path):
    root = tmp_path / "tight"
    root.mkdir()
    for name, color in (
        ("eye_white", (250, 250, 250, 255)),
        ("eye_socket", (255, 255, 255, 255)),
        ("iris", (20, 40, 80, 255)),
    ):
        Image.new("RGBA", (3, 3), color).save(root / f"{name}.png")
    view = {
        "canvas": {"w": 3, "h": 3},
        "eyeRig": {"maxShift": [8, 4], "irises": [[1, 1, 1]]},
        "layers": [
            {"name": name, "url": str(root / f"{name}.png"), "x": 0, "y": 0, "w": 3, "h": 3, "z": index}
            for index, name in enumerate(("eye_white", "iris", "eye_socket"))
        ],
    }
    report = eye_control.probe_view(view)
    assert report["gaze"] == "unavailable"
    assert report["reason"] == "the socket cannot hold a visible iris shift"


def test_identical_frames_are_rejected_as_repeated(tmp_path: Path):
    image = Image.new("RGBA", (8, 8), (10, 20, 30, 255))
    samples = {name: (image.copy(), (4.0, 4.0)) for name in eye_control.GAZES}
    socket_image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    ImageDraw.Draw(socket_image).rectangle((2, 2, 5, 5), fill=(255, 255, 255, 255))
    report = eye_control.judge(samples, {"image": socket_image, "x": 0, "y": 0})
    assert report["gaze"] == "unavailable"
    assert report["reason"] == "directions repeated the same pixels"


def test_shifted_head_is_not_eye_control(tmp_path: Path):
    base = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
    ImageDraw.Draw(base).rectangle((1, 1, 10, 10), fill=(200, 100, 50, 255))
    socket_image = Image.new("RGBA", (4, 4), (255, 255, 255, 255))
    samples = {}
    for index, name in enumerate(eye_control.GAZES):
        frame = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
        frame.paste(base, (index, 0))
        samples[name] = (frame, (3.0 + index, 3.0))
    report = eye_control.judge(samples, {"image": socket_image, "x": 4, "y": 4})
    assert report["gaze"] == "unavailable"
    assert report["reason"] == "pixels outside the eyes moved"
    assert report["probes"]["left"]["pass"] is False


def test_offline_stick_rig_passes_the_same_probe(tmp_path: Path):
    from products.animind_core import stick

    skin = tmp_path / "stick"
    stick.build(skin, lambda path: path.name)
    report = eye_control.assess_skin(skin)
    assert report["ready"] is True
    assert set(report["views"]) == {"front", "left", "right"}
    for view in report["views"].values():
        assert view["gaze"] == "separable"
        assert view["probes"]["left"]["delta"] < 0
        assert view["probes"]["right"]["delta"] > 0
        assert view["probes"]["up"]["delta"] < 0
        assert view["probes"]["down"]["delta"] > 0


def test_procedural_einstein_bust_is_not_marked_ready_and_is_not_rewritten(tmp_path: Path):
    """The bust has iris layers, but the dark pixels in the socket do not travel.

    The web's pupil centroid is that dark mass. Marking the bust separable would
    disagree with it. The files stay as the painter wrote them.
    """
    from products.animind_core import rig

    skin = tmp_path / "einstein"
    rig.build_einstein_product(skin, lambda path: path.name)
    before = {path.relative_to(skin).as_posix(): path.read_bytes() for path in skin.rglob("*") if path.is_file()}
    report = eye_control.assess_skin(skin)
    after = {path.relative_to(skin).as_posix(): path.read_bytes() for path in skin.rglob("*") if path.is_file()}
    assert after == before
    assert report["ready"] is False
    assert set(report["views"]) == {"front", "left", "right"}
    for view in report["views"].values():
        assert view["gaze"] == "unavailable"
        assert "pupil" in view["reason"]


def test_upgrade_keeps_original_bytes_and_reports_missing_layers(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    skin = tmp_path / "assets" / "puppets" / "einstein_saved"
    (skin / "views" / "front").mkdir(parents=True)
    head = skin / "views" / "front" / "head.png"
    Image.new("RGBA", (4, 4), (1, 2, 3, 255)).save(head)
    (skin / "views" / "front" / "view.json").write_text("{}", encoding="utf-8")
    (skin / "expressions").mkdir()
    mouth = skin / "expressions" / "happy.png"
    Image.new("RGBA", (2, 2), (9, 9, 9, 255)).save(mouth)
    (skin / "character.json").write_text(json.dumps({"id": "einstein_saved", "name": "Albert Einstein"}), encoding="utf-8")
    before = {path.relative_to(skin).as_posix(): path.read_bytes() for path in skin.rglob("*") if path.is_file()}
    report = eye_control.upgrade_character("einstein_saved")
    assert report["ready"] is False
    assert report["reason"] == "no supplied face view"
    assert "front" not in report["views"]
    assert "left" not in report["views"]
    after = {path.relative_to(skin).as_posix(): path.read_bytes() for path in skin.rglob("*") if path.is_file() and path.name != "eye_control.json"}
    assert after == before
    assert (skin / "eye_control.json").is_file()
