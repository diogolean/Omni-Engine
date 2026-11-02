"""Mouthless heads: the animated mouth is drawn on blank skin, never inpainted over a lip."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from products.animind_core import assemble_body, avatar, frameqa, image_models, mouthset, qa, rig


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
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TOGETHER_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANIMIND_RUN_BUDGET_USD", "0.20")
    monkeypatch.delenv("ANIMIND_FORCE_MOCK", raising=False)
    prompts: list[str] = []
    heads = {"n": 0}

    def generate(self, prompt, **options):
        prompts.append(prompt)
        dest = Path(options["destination"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if "Headless" in prompt or "headless" in prompt:
            _body().save(dest)
        else:
            heads["n"] += 1
            _head(mouth=heads["n"] == 1).save(dest)  # first head has a lip and is redrawn
        return image_models.ImageResult(dest, "together", self.spec.model, self.spec.usdPerImage)

    monkeypatch.setattr(image_models.TogetherImageProvider, "generate", generate)
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
    assert any("Headless" in prompt or "headless" in prompt for prompt in prompts)
    assert heads["n"] == 3  # front redraws once, side head is clean
    front = __import__("json").loads((skin / "rig_front.json").read_text(encoding="utf-8"))
    assert front["boxes"]["mouth"][1] >= front["boxes"]["face"][1]
    assert built["frontShot"] == "mouthless head and body"


def test_the_run_cap_is_refused_before_the_call(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TOGETHER_API_KEY", "not-a-real-key")
    calls = {"n": 0}

    def generate(self, prompt, **options):  # noqa: ARG002
        calls["n"] += 1
        dest = Path(options["destination"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        _head(mouth=False).save(dest)
        return image_models.ImageResult(dest, "together", self.spec.model, 0.01)

    monkeypatch.setattr(image_models.TogetherImageProvider, "generate", generate)
    with pytest.raises(image_models.BudgetExceeded):
        avatar.create_avatar(
            "Albert Einstein", "cartoon", skin=tmp_path / "skin", url=lambda path: path.name,
            model_id="together-qwen-image", budget=image_models.RunBudget(0.01),
        )
    assert calls["n"] == 1  # the body is refused before it is sent
