"""Mouthless heads: the animated mouth is drawn on blank skin, never inpainted over a lip."""
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from products.animind_core import assemble_body, avatar, frameqa, image_models, mouthset, qa, rig
from products.animind_core.tests.fake_face import patch_human_landmarks


def _head(*, mouth: bool) -> Image.Image:
    image = Image.new("RGBA", (256, 256), (255, 0, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((48, 28, 208, 220), fill=(232, 186, 156, 255))
    draw.ellipse((78, 118, 178, 156), fill=(181, 168, 161, 255))  # warm-grey moustache
    for cx in (104, 156):
        draw.ellipse((cx - 14, 78, cx + 14, 106), fill=(248, 248, 246, 255))
        draw.ellipse((cx - 5, 86, cx + 5, 98), fill=(20, 16, 14, 255))
    if mouth:
        draw.ellipse((108, 168, 152, 188), fill=(170, 60, 64, 255))
    return image


def _body() -> Image.Image:
    image = Image.new("RGBA", (220, 360), (255, 0, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30, 24, 190, 350), 18, fill=(32, 48, 96, 255))
    draw.rectangle((70, 8, 150, 48), fill=(232, 186, 156, 255))  # empty collar
    return image


def test_warm_grey_moustache_is_not_a_mouth_and_a_lip_is():
    assert qa.lower_face_has_ink(_head(mouth=False)) is False
    assert qa.lower_face_has_ink(_head(mouth=True)) is True


def test_head_sinks_into_the_collar_and_reports_the_eye_line():
    fitted = assemble_body.assemble(_head(mouth=False), _body(), "facing_front")
    head_xy, body_xy = fitted["headXY"], fitted["bodyXY"]
    head = assemble_body.fit_puppet(_head(mouth=False), _body())[0]
    chin = head_xy[1] + int(head.shape[0] * 0.95)
    collar = body_xy[1] + int(assemble_body.fit_puppet(_head(mouth=False), _body())[1].shape[0] * 0.17)
    assert chin > collar  # the chin is sunk into the collar, not floating above it
    assert fitted["eyeY"] == head_xy[1] + int(head.shape[0] * 0.42)
    assert fitted["mouthless"] is True
    assert fitted["headBox"][1] < fitted["eyeY"] < fitted["headBox"][3]


def test_blank_mouth_sits_under_the_moustache_and_the_iris_is_not_the_wrinkle():
    head = _head(mouth=False)
    draw = ImageDraw.Draw(head)
    draw.ellipse((96, 114, 124, 136), fill=(96, 70, 58, 255))  # under-eye wrinkle, darker than skin
    face = (48, 28, 208, 220)
    mouth = rig.blank_mouth_box(head, face)
    hair = rig._facial_hair(np.asarray(head), face)
    stache = face[1] + int(np.nonzero(hair[face[1]:face[3], face[0]:face[2]].any(axis=1))[0].max())
    assert mouth[1] >= stache
    low_box = (88, 80, 120, 148)  # the iris, plus the wrinkle; the box centre sits ~20px low
    moved = rig.iris_eye_box(head, low_box)
    iris = rig.iris_point(head, (90, 78, 118, 110))
    assert (low_box[1] + low_box[3]) / 2 - iris[1] >= 15
    assert qa.lids_within_iris(moved, iris, tol=4)
    assert frameqa.lids_on_iris(moved, iris, tol=4)
    trace = {"frames": [{"t": 0.0, "faces": [{"char": 0, "blink": "open", "emotion": "neutro",
                                              "brows": [0, 0], "eyes": [{"lid": list(low_box), "iris": list(iris)}]}]}]}
    low_bad, _report = frameqa.facial_check(trace)
    assert any("iris" in message for _index, message in low_bad)
    trace["frames"][0]["faces"][0]["eyes"] = [{"lid": list(moved), "iris": list(iris)}]
    seated_bad, _report = frameqa.facial_check(trace)
    assert not any("iris" in message for _index, message in seated_bad)
    assert frameqa.rest_blank_has_ink(head, mouth) is False
    placed = mouthset.place(
        mouth,
        {"X": Image.new("RGBA", (mouth[2] - mouth[0], 16), (20, 10, 10, 255))},
        top=mouth[1],
    )
    assert placed["X"][1][1] == mouth[1]
    assert placed["X"][0].width == mouth[2] - mouth[0]
    assert qa.blank_area_has_ink(head, mouth) is False


def test_fake_model_builds_a_mouthless_einstein_without_network(tmp_path, monkeypatch):
    patch_human_landmarks(monkeypatch)
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANIMIND_RUN_BUDGET_USD", "0.20")
    monkeypatch.delenv("ANIMIND_FORCE_MOCK", raising=False)
    prompts: list[str] = []
    heads = {"n": 0}

    def generate(prompt, destination, **options):  # noqa: ARG001
        prompts.append(prompt)
        destination.parent.mkdir(parents=True, exist_ok=True)
        heads["n"] += 1
        _head(mouth=heads["n"] == 1).save(destination)  # first plate has a lip and is redrawn
        return "models/gemini-2.5-flash-image"

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        generate,
    )
    skin = tmp_path / "einstein"
    built = avatar.create_avatar(
        "Albert Einstein, wild white hair, bushy white moustache",
        "cartoon",
        skin=skin,
        url=lambda path: path.name,
        model_id="together-qwen-image",
        budget=image_models.RunBudget(0.20),
    )
    assert built["qa"]["costUsd"] <= 0.15
    assert built["qa"]["costUsd"] <= 0.30
    assert all("lips together" not in prompt.lower() for prompt in prompts)
    assert any("NO human mouth" in prompt for prompt in prompts)
    assert any("25 degrees" in prompt and "27 percent" in prompt for prompt in prompts)
    assert any("black background" in prompt.lower() for prompt in prompts)
    assert any("Remove the mouth" in prompt for prompt in prompts)
    assert heads["n"] == 2  # the mouthed plate is redrawn once
    front = __import__("json").loads((skin / "rig_front.json").read_text(encoding="utf-8"))
    assert front["boxes"]["mouth"][1] >= front["boxes"]["face"][1]
    assert front["pipeline"] == "aiwake-v3"
    manifest = __import__("json").loads((skin / "puppet.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 3
    eye_mid = (front["boxes"]["left_eye"][1] + front["boxes"]["left_eye"][3]) / 2
    assert abs(eye_mid - manifest["framing"]["target_eye_y"]) <= 2
    assert built["frontShot"] == "aiwake v3 mouthless"


def test_the_run_cap_is_refused_before_the_call(tmp_path, monkeypatch):
    patch_human_landmarks(monkeypatch)
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    calls = {"n": 0}

    def generate(prompt, destination, **options):  # noqa: ARG001
        calls["n"] += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        _head(mouth=True).save(destination)
        return "models/gemini-2.5-flash-image"

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        generate,
    )
    price = image_models.selected("nano-banana").usdPerImage
    with pytest.raises(image_models.BudgetExceeded):
        avatar.create_avatar(
            "Albert Einstein", "cartoon", skin=tmp_path / "skin", url=lambda path: path.name,
            model_id="nano-banana", budget=image_models.RunBudget(price),
        )
    assert calls["n"] == 1  # the mouth redraw is refused before it is sent


def test_avatar_module_entry_defines_the_fingerprint(tmp_path):
    """``python -m`` runs main only after the module body, including ``_fingerprint``."""
    env = os.environ.copy()
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "TOGETHER_API_KEY", "OPENROUTER_API_KEY"):
        env.pop(key, None)
    env["ANIMIND_DATA_DIR"] = str(tmp_path)
    env["ANIMIND_RUN_BUDGET_USD"] = "0.20"
    root = Path(__file__).resolve().parents[3]
    help_run = subprocess.run(
        [sys.executable, "-m", "products.animind_core.avatar", "--help"],
        cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    assert help_run.returncode == 0, help_run.stderr
    assert "usage" in help_run.stdout.lower()
    assert "rerig" in help_run.stdout.lower()
    run = subprocess.run(
        [sys.executable, "-m", "products.animind_core.avatar", "einstein"],
        cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    assert "NameError" not in run.stderr
    assert "_fingerprint" not in run.stderr
    assert run.returncode != 0
    assert "no image provider is configured" in run.stderr


def test_pink_lips_under_the_moustache_count_even_when_the_jacket_is_clean():
    image = Image.new("RGBA", (120, 300), (0, 0, 0, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((20, 20, 100, 150), fill=(232, 186, 156, 255))
    draw.ellipse((36, 78, 84, 108), fill=(181, 168, 161, 255))
    draw.ellipse((48, 112, 76, 132), fill=(220, 150, 160, 255))
    draw.rectangle((15, 160, 105, 290), fill=(32, 40, 90, 255))
    assert qa.lower_face_has_ink(image) is False
    assert qa.mouth_under_moustache_has_ink(image, (20, 20, 100, 150)) is True
    assert qa.mouth_under_moustache_has_ink(_head(mouth=False), (48, 28, 208, 210)) is False


def test_a_white_footer_is_replaced_so_the_torso_reaches_the_bottom():
    image = Image.new("RGBA", (80, 100), (255, 255, 255, 255))
    ImageDraw.Draw(image).rectangle((8, 4, 72, 78), fill=(30, 40, 90, 255))
    fixed = __import__("products.animind_core.puppet_v3", fromlist=["drop_white_footer"]).drop_white_footer(image)
    bottom = np.asarray(fixed)[-1, :, :3]
    assert int(bottom.min()) < 200


def test_a_person_refuses_the_robotic_landmark_fallback(monkeypatch):
    from animation.parametric.factory import landmark_detector

    monkeypatch.setattr(landmark_detector, "_mediapipe_tasks", lambda *_a, **_k: None)
    monkeypatch.setattr(landmark_detector, "_mediapipe_face_mesh", lambda *_a, **_k: None)
    plate = Image.new("RGBA", (64, 80), (20, 20, 20, 255))
    with pytest.raises(landmark_detector.RoboticLandmarkRefused, match="robotic"):
        landmark_detector.detect_landmarks(plate, require_human_face=True)
    robot = landmark_detector.detect_landmarks(plate)
    assert robot.backend == "opencv_robotic_fallback"


def test_tasks_landmarker_is_preferred_and_legacy_solutions_are_the_fallback(monkeypatch):
    from animation.parametric.factory import landmark_detector
    from products.animind_core.tests.fake_face import fake_human_landmarks

    plate = Image.new("RGBA", (64, 80), (30, 30, 30, 255))
    human = fake_human_landmarks(plate)
    legacy = landmark_detector.FacialLandmarks(
        left_eye=human.left_eye, right_eye=human.right_eye, mouth_center=human.mouth_center,
        mouth_width=human.mouth_width, jawline=(), chin_base=human.chin_base,
        neck_pivot=human.neck_pivot, head_bbox=human.head_bbox, confidence=0.9,
        backend="mediapipe_face_mesh",
    )
    monkeypatch.setattr(landmark_detector, "_mediapipe_tasks", lambda *_a, **_k: human)
    monkeypatch.setattr(landmark_detector, "_mediapipe_face_mesh", lambda *_a, **_k: legacy)
    assert landmark_detector.detect_landmarks(plate).backend == "mediapipe_tasks"
    monkeypatch.setattr(landmark_detector, "_mediapipe_tasks", lambda *_a, **_k: None)
    assert landmark_detector.detect_landmarks(plate, require_human_face=True).backend == "mediapipe_face_mesh"


def test_a_mouth_on_the_collar_or_an_eye_off_the_iris_fails_frame_qa():
    face = {"face": [40, 20, 180, 160], "left_eye": [60, 50, 90, 80], "right_eye": [120, 50, 150, 80],
            "mouth": [80, 220, 140, 260]}
    assert qa.features_on_face(face)["pass"] is False
    assert frameqa.feature_positions(face)["pass"] is False
    seated = {**face, "mouth": [80, 110, 140, 145]}
    assert qa.features_on_face(seated)["pass"] is True
    assert qa.features_on_face(seated, irises=[[200, 10], [10, 10]])["pass"] is False
    trace = {"size": [16, 16], "frames": [], "boxes": face}
    assert frameqa.check(Path("unused.mp4"), trace)["pass"] is False


def test_rerig_reuses_the_cached_plate_and_writes_character_json(tmp_path, monkeypatch):
    patch_human_landmarks(monkeypatch)

    def generate(*_args, **_kwargs):  # noqa: ARG001
        raise AssertionError("rerig must not call the image model")

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        generate,
    )
    skin = tmp_path / "albert_einstein"
    skin.mkdir()
    _head(mouth=False).save(skin / "_gemini.png")
    built = avatar.rerig_from_plate(
        skin, description="Albert Einstein, bushy white moustache",
        url=lambda path: path.name, character_id="albert_einstein", name="Albert Einstein",
    )
    saved = __import__("json").loads((skin / "character.json").read_text(encoding="utf-8"))
    assert saved["id"] == "albert_einstein" and saved["name"] == "Albert Einstein"
    assert saved["status"] == "ready" and (skin / "puppet.json").is_file()
    assert built["qa"]["costUsd"] == 0.0
