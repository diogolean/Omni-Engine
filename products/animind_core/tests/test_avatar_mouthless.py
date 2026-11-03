"""Mouthless heads: the animated mouth is drawn on blank skin, never inpainted over a lip."""
import json
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


def test_white_islands_on_the_border_are_cleared_and_an_interior_tooth_stays():
    from products.animind_core import puppet_v3

    image = Image.new("RGBA", (80, 60), (0, 0, 0, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 20, 8, 36), fill=(250, 250, 250, 255))
    draw.rectangle((40, 24, 48, 32), fill=(248, 248, 246, 255))
    fixed = puppet_v3.clean_border_white(image)
    assert fixed.getpixel((2, 28))[3] == 0
    assert fixed.getpixel((44, 28))[3] == 255


def test_rerig_blanks_lips_once_and_no_edit_skips_the_call(tmp_path, monkeypatch):
    patch_human_landmarks(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANIMIND_RUN_BUDGET_USD", "0.20")
    calls = {"n": 0}

    def generate(prompt, destination, **options):
        calls["n"] += 1
        assert "Remove the mouth" in prompt
        assert options.get("reference_path")
        destination.parent.mkdir(parents=True, exist_ok=True)
        _head(mouth=False).save(destination)
        return "models/gemini-2.5-flash-image"

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        generate,
    )
    skin = tmp_path / "albert_einstein"
    skin.mkdir()
    _head(mouth=True).save(skin / "_gemini.png")
    built = avatar.rerig_from_plate(
        skin, description="Albert Einstein, bushy white moustache",
        url=lambda path: path.name, character_id="albert_einstein", name="Albert Einstein",
        edit=True, budget=image_models.RunBudget(0.20),
    )
    assert calls["n"] == 1
    assert built["qa"]["costUsd"] > 0
    view = built["views"]["left"]
    assert qa.visemes_share_one_scale(view)
    assert (view.get("mouthQA") or {}).get("mouths") == "human"
    skipped = tmp_path / "plain" / "albert_einstein"
    skipped.mkdir(parents=True)
    _head(mouth=True).save(skipped / "_gemini.png")
    calls["n"] = 0
    avatar.rerig_from_plate(
        skipped, description="Albert Einstein", url=lambda path: path.name,
        character_id="albert_einstein", name="Albert Einstein", edit=False,
    )
    assert calls["n"] == 0
    price = image_models.selected("nano-banana").usdPerImage
    refused = tmp_path / "capped" / "albert_einstein"
    refused.mkdir(parents=True)
    _head(mouth=True).save(refused / "_gemini.png")
    calls["n"] = 0
    with pytest.raises(image_models.BudgetExceeded):
        avatar.rerig_from_plate(
            refused, description="Albert Einstein", url=lambda path: path.name,
            character_id="albert_einstein", name="Albert Einstein",
            edit=True, budget=image_models.RunBudget(price * 0.2),
        )
    assert calls["n"] == 0


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
    report = __import__("json").loads((skin / "qa.json").read_text(encoding="utf-8"))
    assert saved["id"] == "albert_einstein" and saved["name"] == "Albert Einstein"
    assert saved["status"] == "ready" and (skin / "puppet.json").is_file()
    assert (skin / "qa.json").is_file() and report["gates"]
    assert saved["qa"]["score"] == report["score"] == built["qa"]["score"]
    assert saved["qa"]["gates"]["mouthAnchor"]["pass"] is True
    assert built["qa"]["costUsd"] == 0.0


def test_cached_plate_prefers_mouthless_then_closed_then_front(tmp_path):
    skin = tmp_path / "cache"
    skin.mkdir()

    def png(name: str) -> None:
        Image.new("RGBA", (4, 4), (8, 8, 8, 255)).save(skin / name)

    png("source_mouthless.png")
    png("source_closed.png")
    png("source_front.png")
    png("_gemini.png")
    png("source_original.png")
    png("_plate.png")
    assert avatar.cached_plate(skin).name == "source_mouthless.png"
    (skin / "source_mouthless.png").unlink()
    assert avatar.cached_plate(skin).name == "source_closed.png"
    (skin / "source_closed.png").unlink()
    assert avatar.cached_plate(skin).name == "source_front.png"
    (skin / "source_front.png").write_bytes(b"")
    assert avatar.cached_plate(skin).name == "_gemini.png"


def _marked_plate(*, eyes: tuple[tuple[int, int], tuple[int, int]], marker: tuple[int, int, int]) -> Image.Image:
    """Magenta bust. The cheek block is the only way to tell the two plates apart."""
    image = Image.new("RGBA", (256, 256), (255, 0, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((48, 28, 208, 220), fill=(232, 186, 156, 255))
    draw.ellipse((78, 118, 178, 156), fill=(181, 168, 161, 255))
    for cx, cy in eyes:
        draw.ellipse((cx - 14, cy - 14, cx + 14, cy + 14), fill=(248, 248, 246, 255))
        draw.ellipse((cx - 5, cy - 6, cx + 5, cy + 6), fill=(20, 16, 14, 255))
    draw.rectangle((148, 168, 176, 196), fill=marker + (255,))
    return image


def _iris_landmarks(image: Image.Image, *, require_human_face: bool = False):
    """Eyes follow the painted irises, so a 3/4 plate is not reported as frontal."""
    import cv2

    from animation.parametric.factory.landmark_detector import EyeLandmark, FacialLandmarks, Point3D
    from products.animind_core.tests.fake_face import fake_human_landmarks

    del require_human_face
    arr = np.asarray(image.convert("RGBA"))
    height, width = arr.shape[:2]
    alpha = arr[:, :, 3] > 8
    ys, xs = np.nonzero(alpha)
    if xs.size == 0:
        return fake_human_landmarks(image)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    head_bottom = y0 + max(8, int((y1 - y0) * 0.90))
    head = (x0, y0, x1, min(height, head_bottom))
    span_x = max(1, head[2] - head[0])
    span_y = max(1, head[3] - head[1])
    lum = arr[:, :, :3].astype(np.int16).mean(axis=2)
    dark = (alpha & (lum < 40)).astype(np.uint8)
    dark[int(head[1] + 0.62 * span_y):, :] = 0
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(dark, connectivity=8)
    blobs = []
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if 6 <= area <= 800:
            blobs.append((area, centroids[index]))
    if len(blobs) < 2:
        return fake_human_landmarks(image)
    pair = sorted(blobs, key=lambda item: item[0], reverse=True)[:2]
    pair.sort(key=lambda item: item[1][0])
    left = (int(round(pair[0][1][0])), int(round(pair[0][1][1])))
    right = (int(round(pair[1][1][0])), int(round(pair[1][1][1])))
    mouth = (head[0] + span_x // 2, head[1] + int(span_y * 0.72))
    chin = (mouth[0], head[3] - 1)

    def uv(px: int, py: int) -> Point3D:
        return Point3D((px - head[0]) / span_x, (py - head[1]) / span_y, 0.0)

    return FacialLandmarks(
        left_eye=EyeLandmark(uv(*left), 0.04),
        right_eye=EyeLandmark(uv(*right), 0.04),
        mouth_center=uv(*mouth),
        mouth_width=0.16,
        jawline=(),
        chin_base=uv(*chin),
        neck_pivot=uv(chin[0], min(height - 1, head[3] + 2)),
        head_bbox=head,
        confidence=0.95,
        backend="mediapipe_tasks",
    )


def _view_has(folder: Path, kind: str) -> bool:
    for path in folder.rglob("*.png"):
        arr = np.asarray(Image.open(path).convert("RGBA"))
        rgb = arr[:, :, :3].astype(np.int16)
        alpha = arr[:, :, 3] > 128
        if kind == "blue":
            hit = alpha & (rgb[:, :, 2] > 200) & (rgb[:, :, 0] < 80) & (rgb[:, :, 1] < 120)
        else:
            hit = alpha & (rgb[:, :, 1] > 180) & (rgb[:, :, 0] < 80) & (rgb[:, :, 2] < 100)
        if bool(hit.any()):
            return True
    return False


def test_rerig_fills_front_from_source_front_and_leaves_the_three_quarter_on_the_left(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "animation.parametric.factory.landmark_detector.detect_landmarks",
        _iris_landmarks,
    )
    monkeypatch.setattr(
        "animation.parametric.factory.rigger.detect_landmarks",
        _iris_landmarks,
    )

    def generate(*_args, **_kwargs):
        raise AssertionError("re-rig must not call the image model when source_front.png exists")

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        generate,
    )
    for key in (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "TOGETHER_API_KEY",
        "OPENROUTER_API_KEY", "ANIMIND_IMAGE_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    skin = tmp_path / "albert_einstein"
    skin.mkdir()
    blue = (20, 40, 240)
    green = (20, 220, 40)
    _marked_plate(eyes=((78, 92), (118, 92)), marker=blue).save(skin / "source_mouthless.png")
    _marked_plate(eyes=((104, 92), (156, 92)), marker=green).save(skin / "source_front.png")

    def url(path: Path) -> str:
        return "/v1/files/" + path.as_posix()

    built = avatar.rerig_from_plate(
        skin, description="Albert Einstein, bushy white moustache",
        url=url, character_id="albert_einstein", name="Albert Einstein",
        edit=False,
    )
    assert built["qa"]["costUsd"] == 0.0
    assert built["frontShot"] == "facing_front"
    assert _view_has(skin / "views" / "left", "blue")
    assert not _view_has(skin / "views" / "left", "green")
    assert _view_has(skin / "views" / "front", "green")
    assert not _view_has(skin / "views" / "front", "blue")
    front_boxes = json.loads((skin / "rig_front.json").read_text(encoding="utf-8"))["boxes"]
    assert qa.front_symmetry(front_boxes)["pass"] is True
    saved = json.loads((skin / "views" / "front" / "view.json").read_text(encoding="utf-8"))
    anchor = saved["mouthQA"]["anchor"]
    face = saved["mouthQA"]["face"]
    width = max(1, face[2] - face[0])
    centre = (face[0] + face[2]) / 2
    assert anchor["view"] == "front"
    assert abs(anchor["x"] - centre) / width <= 0.01
    left = json.loads((skin / "views" / "left" / "view.json").read_text(encoding="utf-8"))
    assert left["mouthQA"]["anchor"]["view"] == "left"
    for layer in built["views"]["front"]["layers"]:
        assert layer["url"].endswith(f"views/front/{layer['name']}.png")
        assert "_front_stage" not in layer["url"]
    for code, layer in built["views"]["front"]["visemes"].items():
        assert layer["url"].endswith(f"views/front/mouths/{code}.png")
        assert "_front_stage" not in layer["url"]
    for name, layer in built["views"]["front"].get("expressions", {}).items():
        assert layer["url"].endswith(f"views/front/expressions/{name}.png")
        assert "_front_stage" not in layer["url"]
    on_disk = json.loads((skin / "views" / "front" / "view.json").read_text(encoding="utf-8"))
    assert on_disk["layers"][0]["url"] == f"{on_disk['layers'][0]['name']}.png"
    assert "_front_stage" not in (skin / "views" / "front" / "view.json").read_text(encoding="utf-8")
    assert "_front_stage" not in (skin / "character.json").read_text(encoding="utf-8")
    assert "_front_stage" not in json.dumps(built)


def _three_quarter_master(root: Path) -> Path:
    """Side plate and a different ``source_front.png``. That split used to force a second puppet."""
    skin = root / "albert_einstein"
    skin.mkdir()
    Image.new("RGBA", (8, 8), (20, 40, 240, 255)).save(skin / "source_mouthless.png")
    Image.new("RGBA", (8, 8), (20, 220, 40, 255)).save(skin / "source_front.png")
    return skin


def _seat_front(skin: Path, *, view: str, mouth_x: float, url: str = "head.png") -> Path:
    front = skin / "views" / "front"
    (front / "mouths").mkdir(parents=True)
    Image.new("RGBA", (4, 4), (20, 220, 40, 255)).save(front / "head.png")
    Image.new("RGBA", (2, 2), (150, 60, 64, 255)).save(front / "mouths" / "X.png")
    (front / "kept.txt").write_text("stay", encoding="utf-8")
    face = [40, 20, 200, 250]
    doc = {
        "canvas": {"w": 240, "h": 320},
        "layers": [{"name": "head", "url": url, "x": 0, "y": 0, "w": 4, "h": 4, "z": 2}],
        "visemes": {
            "X": {"name": "mouth_X", "url": "mouths/X.png", "x": 90, "y": 170, "w": 2, "h": 2, "z": 10},
        },
        "expressions": {},
        "mouthQA": {
            "face": face,
            "anchor": {
                "x": mouth_x, "y": 180, "view": view, "along": 0.647,
                "nose": [120, 140], "chin": [120, 240],
            },
        },
    }
    (front / "view.json").write_text(json.dumps(doc), encoding="utf-8")
    return front


def test_ensure_front_keeps_a_valid_front_and_does_not_create_puppet(tmp_path, monkeypatch):
    """``--rerig --no-edit`` must not rig ``source_front.png`` again when the slot is already frontal."""
    calls = {"n": 0}

    def create_puppet(*_args, **_kwargs):
        calls["n"] += 1
        raise AssertionError("create_puppet must not run when views/front is already frontal")

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.create_puppet",
        create_puppet,
    )
    for key in (
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "TOGETHER_API_KEY",
        "OPENROUTER_API_KEY", "ANIMIND_IMAGE_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    skin = _three_quarter_master(tmp_path)
    face = [40, 20, 200, 250]
    centre = (face[0] + face[2]) / 2
    front = _seat_front(
        skin, view="front", mouth_x=centre,
        url="/v1/files/albert_einstein/_front_stage/front/views/front/head.png",
    )
    views: dict = {}

    def url(path: Path) -> str:
        return "/v1/files/" + path.as_posix()

    origin = avatar._ensure_front(
        skin, views, url, description="Albert Einstein, bushy white moustache",
        budget=image_models.RunBudget(0.0), spent=0.0, generate=False,
    )
    assert calls["n"] == 0
    assert origin == "kept"
    assert (front / "kept.txt").read_text(encoding="utf-8") == "stay"
    saved = json.loads((front / "view.json").read_text(encoding="utf-8"))
    anchor = saved["mouthQA"]["anchor"]
    width = max(1, face[2] - face[0])
    assert anchor["view"] == "front"
    assert abs((anchor["x"] - centre) / width) <= 0.01
    assert saved["layers"][0]["url"] == "head.png"
    assert "_front_stage" not in (front / "view.json").read_text(encoding="utf-8")
    assert views["front"]["layers"][0]["url"].endswith("views/front/head.png")
    assert views["front"]["visemes"]["X"]["url"].endswith("views/front/mouths/X.png")
    assert "_front_stage" not in json.dumps(views["front"])
    assert not (skin / "_front_stage").exists()
    again = avatar.install_cached_front(skin, url, description="Albert Einstein")
    assert calls["n"] == 0
    assert again is not None
    assert again["layers"][0]["url"].endswith("views/front/head.png")
    assert (front / "kept.txt").read_text(encoding="utf-8") == "stay"


def test_ensure_front_still_rebuilds_a_three_quarter_plate_tagged_as_front(tmp_path, monkeypatch):
    calls = {"n": 0}

    def create_puppet(*_args, **_kwargs):
        calls["n"] += 1
        raise RuntimeError("staged front refused")

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.create_puppet",
        create_puppet,
    )
    skin = _three_quarter_master(tmp_path)
    face = [40, 20, 200, 250]
    front = _seat_front(skin, view="left", mouth_x=face[0] + 8)
    views = {"front": {"stale": True}}

    def url(path: Path) -> str:
        return "/v1/files/" + path.as_posix()

    origin = avatar._ensure_front(
        skin, views, url, description="Albert Einstein",
        budget=image_models.RunBudget(0.0), spent=0.0, generate=False,
    )
    assert calls["n"] == 1
    assert origin == "missing"
    assert "front" not in views
    assert not front.exists()
    assert not (skin / "_front_stage").exists()


def test_assemble_refuses_a_three_quarter_plate_in_the_front_slot(tmp_path):
    image = Image.new("RGBA", (200, 240), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((40, 20, 160, 180), fill=(232, 186, 156, 255))
    closed = tmp_path / "closed.png"
    image.save(closed)
    boxes = {
        "face": (40, 20, 160, 180),
        "left_eye": (50, 60, 78, 88),
        "right_eye": (84, 60, 104, 88),
        "mouth": (70, 120, 110, 140),
    }
    skin = tmp_path / "skin"
    views = rig.assemble(
        skin, {"closed": closed}, boxes, lambda path: path.name,
        matte=lambda im: im.convert("RGBA"), front=True,
    )
    assert views == {}
    assert not (skin / "views" / "front").exists()


def test_front_prompt_asks_for_a_dead_front_face():
    from products.animind_core import puppet_v3

    front = puppet_v3.avatar_prompt("a person", stick=False, facing="front").lower()
    assert "dead-front" in front and "zero degrees" in front
    assert "never dead-front" not in front
    side = puppet_v3.avatar_prompt("a person", stick=False, facing="left").lower()
    assert "never dead-front" in side and "25 degrees" in side
