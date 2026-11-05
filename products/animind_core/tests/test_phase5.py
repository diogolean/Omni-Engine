"""Phase 5: Aiwake-backed avatars, capped providers, director, and the bench."""
import base64
import io

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageOps

from products.animind_core import avatar, director, image_models, sheet, stick, styles
from products.animind_core.tests.fake_face import patch_human_landmarks


def _png_b64() -> str:
    buffer = io.BytesIO()
    Image.new("RGBA", (8, 8), (255, 0, 255, 255)).save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode()


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_run_budget_defaults_to_ten_cents(monkeypatch):
    monkeypatch.delenv("ANIMIND_RUN_BUDGET_USD", raising=False)
    assert image_models.run_budget().cap_usd == 0.10


def test_together_qwen_and_kontext_record_cost_and_stop_at_the_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TOGETHER_API_KEY", "not-a-real-key")
    monkeypatch.delenv("TOGETHER_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("TOGETHER_QWEN_IMAGE_MODEL", raising=False)
    encoded = _png_b64()
    seen = []

    def post(url, **kwargs):
        seen.append((url, kwargs["json"]["model"]))
        return _Response({"data": [{"b64_json": encoded}]})

    monkeypatch.setattr(image_models.httpx, "post", post)
    budget = image_models.RunBudget(0.015)
    dest = tmp_path / "qwen.png"
    result = image_models.provider("together-qwen-image").generate("a bust", destination=dest, budget=budget)
    assert result.model == "Qwen/Qwen-Image" and result.cost_usd == 0.01 and dest.is_file()
    assert seen[0] == ("https://api.together.xyz/v1/images/generations", "Qwen/Qwen-Image")
    with pytest.raises(image_models.BudgetExceeded):
        image_models.provider("together-qwen-image").generate(
            "again", destination=tmp_path / "again.png", budget=budget,
        )
    kontext = image_models.provider("together-flux-kontext")
    assert kontext.spec.model == "black-forest-labs/FLUX.1-kontext-pro"
    assert kontext.spec.keyEnv == "TOGETHER_API_KEY" and kontext.spec.usdPerImage == 0.04
    got = kontext.generate("a bust", destination=tmp_path / "kontext.png", budget=image_models.RunBudget(0.10))
    assert got.cost_usd == 0.04
    assert seen[1][1] == "black-forest-labs/FLUX.1-kontext-pro"
    assert "together-flux-schnell" not in image_models.models()
    assert "not-a-real-key" not in dest.read_bytes().decode("latin1", errors="ignore")


def test_create_avatar_uses_the_image_model_once_and_mirrors_left(tmp_path, monkeypatch):
    patch_human_landmarks(monkeypatch)
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.delenv("ANIMIND_FORCE_MOCK", raising=False)
    # The plate is nano-banana, not the old US$0.01 Qwen call. A redraw needs two plates.
    plate = image_models.selected("nano-banana").usdPerImage
    budget = image_models.RunBudget(plate * 2)
    prompts = []

    def generate(prompt, destination, **options):  # noqa: ARG001
        prompts.append(prompt)
        destination.parent.mkdir(parents=True, exist_ok=True)
        avatar._mock_bust().save(destination)
        return "models/gemini-2.5-flash-image"

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        generate,
    )
    monkeypatch.setattr(stick, "build", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stick primitives")))
    skin = tmp_path / "skin"
    built = avatar.create_avatar(
        "a calm teacher with round glasses", "stick", kind="stick",
        skin=skin, url=lambda path: path.as_uri(), model_id="nano-banana", budget=budget,
    )
    assert prompts
    assert "South Park" in prompts[0] and "stick-figure" in prompts[0]
    assert "NO human mouth" in prompts[0]
    assert "25 degrees" in prompts[0] and "27 percent" in prompts[0]
    assert "black background" in prompts[0].lower()
    assert "lips together" not in prompts[0].lower()
    assert "facing left" in prompts[0].lower()
    assert built["generator"] == "gemini" and built["realImage"] is True
    assert set(built["views"]) == {"front", "left", "right"}
    assert set(built["views"]["left"]["visemes"]) == set("XABCDEFGH")
    assert {"torso", "neck", "head", "eye_white", "iris", "eyes_half", "eyes_closed", "brows"} <= {
        layer["name"] for layer in built["views"]["left"]["layers"]
    }
    assert "happy" in built["views"]["left"]["expressions"]
    assert built["viewStatus"]["right"] == "mirror of left"
    _assert_mouth_inside_face(skin, built["views"]["left"])
    left = Image.open(skin / "views" / "left" / "torso.png").convert("RGBA")
    right = Image.open(skin / "views" / "right" / "torso.png").convert("RGBA")
    assert ImageChops.difference(ImageOps.mirror(left), right).getbbox() is None
    portrait = Image.open(skin / "character.png").convert("RGBA")
    box = portrait.getchannel("A").getbbox()
    figure = portrait.crop(box)
    sample = figure.crop((0, 0, max(1, figure.width // 5), figure.height)).resize((1, 1))
    red, _green, blue, _alpha = sample.getpixel((0, 0))
    assert red > blue
    sent = len(prompts)
    again = avatar.create_avatar(
        "a calm teacher with round glasses", "stick", kind="stick",
        skin=tmp_path / "skin2", url=lambda path: path.as_uri(), model_id="nano-banana", budget=budget,
    )
    assert again["cached"] is True and len(prompts) == sent


def _realistic_bust() -> Image.Image:
    """A bust with a head, two eyes and a mouth above a wider torso."""
    image = Image.new("RGBA", (640, 800), (255, 0, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((170, 390, 470, 820), 40, fill=(36, 78, 150, 255), outline=(20, 20, 24, 255), width=6)
    draw.rectangle((292, 330, 348, 410), fill=(236, 204, 176, 255))
    draw.ellipse((210, 70, 430, 360), fill=(244, 214, 186, 255), outline=(25, 22, 22, 255), width=6)
    for cx in (275, 365):
        draw.ellipse((cx - 22, 175, cx + 22, 215), fill=(250, 250, 248, 255), outline=(25, 22, 22, 255), width=3)
        draw.ellipse((cx - 8, 184, cx + 8, 206), fill=(30, 24, 22, 255))
    draw.arc((285, 255, 355, 295), 15, 165, fill=(90, 40, 45, 255), width=6)
    return image


def _assert_mouth_inside_face(skin, view) -> None:
    meta = __import__("json").loads((skin / "rig.json").read_text(encoding="utf-8"))
    face = meta["boxes"]["face"]
    mouth = meta["boxes"]["mouth"]
    assert face[0] <= mouth[0] and mouth[2] <= face[2] + 4
    assert face[1] <= mouth[1] and mouth[3] <= face[3] + 8
    layer = view["visemes"]["D"]
    cx, cy = layer["x"] + layer["w"] / 2, layer["y"] + layer["h"] / 2
    assert face[0] <= cx <= face[2] and face[1] <= cy <= face[3]


def test_realistic_bust_keeps_the_mouth_on_the_face(tmp_path, monkeypatch):
    patch_human_landmarks(monkeypatch)
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANIMIND_RUN_BUDGET_USD", "0.20")

    def generate(prompt, destination, **options):  # noqa: ARG001
        destination.parent.mkdir(parents=True, exist_ok=True)
        _realistic_bust().save(destination)
        return "models/gemini-2.5-flash-image"

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        generate,
    )
    skin = tmp_path / "bust"
    built = avatar.create_avatar(
        "a teacher", "southpark", kind="rig", skin=skin,
        url=lambda path: path.as_uri(), model_id="together-qwen-image",
    )
    _assert_mouth_inside_face(skin, built["views"]["left"])
    front = __import__("json").loads((skin / "rig_front.json").read_text(encoding="utf-8"))
    assert front["boxes"]["face"][1] < front["boxes"]["mouth"][1]
    assert front["boxes"]["mouth"][3] <= front["boxes"]["face"][3] + 8
    right = built["views"]["right"]["visemes"]["D"]
    width = built["views"]["left"]["canvas"]["w"]
    left = built["views"]["left"]["visemes"]["D"]
    assert right["x"] == width - left["x"] - left["w"]


def test_side_skin_is_pulled_toward_the_front_and_a_high_mouth_drops():
    front = _realistic_bust()
    pink = front.copy()
    px = pink.load()
    for y in range(pink.height // 2):
        for x in range(pink.width):
            r, g, b, a = px[x, y]
            if a > 40 and r > b + 12:
                px[x, y] = (255, 190, 210, a)
    assert avatar.skin_distance(front, pink) > 30
    matched = avatar.match_skin(pink, front)
    assert avatar.skin_distance(front, matched) < 12
    high = _realistic_bust()
    draw = ImageDraw.Draw(high)
    draw.rectangle((285, 255, 355, 295), fill=(244, 214, 186, 255))
    draw.ellipse((300, 188, 340, 206), fill=(20, 16, 16, 255))
    boxes = avatar.bust_boxes(high)
    face, mouth = boxes["face"], boxes["mouth"]
    assert (mouth[1] + mouth[3]) / 2 >= face[1] + (face[3] - face[1]) * 0.62


def _bearded_bust() -> Image.Image:
    image = _realistic_bust()
    draw = ImageDraw.Draw(image)
    draw.rectangle((285, 250, 355, 300), fill=(244, 214, 186, 255))
    draw.polygon(
        [(230, 250), (410, 248), (400, 360), (240, 362)],
        fill=(55, 42, 36, 255),
    )
    draw.ellipse((300, 300, 345, 322), fill=(30, 22, 20, 255))
    return image


def test_beard_does_not_pull_the_mouth_up_to_the_nose():
    boxes = avatar.bust_boxes(_bearded_bust())
    face, mouth = boxes["face"], boxes["mouth"]
    eyes = boxes["left_eye"][3], boxes["right_eye"][3]
    nose = max(eyes) + 0.22 * (face[3] - max(eyes))
    center = (mouth[1] + mouth[3]) / 2
    assert mouth[1] >= nose - 4
    assert center > nose
    assert center > (boxes["left_eye"][1] + boxes["right_eye"][1]) / 2 + 30


def test_side_edit_failure_does_not_reuse_the_front(tmp_path, monkeypatch):
    patch_human_landmarks(monkeypatch)
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANIMIND_RUN_BUDGET_USD", "0.20")

    def generate(prompt, destination, **options):  # noqa: ARG001
        destination.parent.mkdir(parents=True, exist_ok=True)
        _realistic_bust().save(destination)
        return "models/gemini-2.5-flash-image"

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        generate,
    )
    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.auto_rig_character",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("404")),
    )
    with pytest.raises(avatar.SideViewMissing):
        avatar.create_avatar(
            "a teacher", "southpark", skin=tmp_path / "skin",
            url=lambda path: path.as_uri(), model_id="together-qwen-image",
        )


def test_reuse_false_ignores_the_avatar_cache(tmp_path, monkeypatch):
    patch_human_landmarks(monkeypatch)
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANIMIND_RUN_BUDGET_USD", "0.20")
    calls = {"n": 0}

    def generate(prompt, destination, **options):  # noqa: ARG001
        calls["n"] += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        _realistic_bust().save(destination)
        return "models/gemini-2.5-flash-image"

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        generate,
    )
    kwargs = dict(skin=tmp_path / "skin", url=lambda path: path.as_uri(), model_id="together-qwen-image")
    avatar.create_avatar("a teacher", "southpark", **kwargs)
    first = calls["n"]
    avatar.create_avatar("a teacher", "southpark", reuse=False, **kwargs)
    assert calls["n"] == first * 2


def test_visemes_stay_open_on_front_and_left():
    from products.animind_core import frameqa, mouthset, qa
    colours = {
        "outline": (40, 30, 30, 255), "cavity": (20, 12, 14, 255),
        "teeth": (247, 244, 236, 255), "tongue": (190, 86, 92, 255), "lip": (142, 72, 78, 255),
    }
    cap = {code: 0.95 * share * 480 for code, share in qa.OPEN_DRAWN.items()}
    for squeeze in (1.0, 0.85):
        mouths = mouthset.draw_set(110, colours, squeeze=squeeze, open_cap=cap)
        for code, sprite in mouths.items():
            cavity = frameqa.happy_cavity(sprite)
            assert cavity + 1e-6 >= frameqa.VISEME_CAV_MIN[code], (squeeze, code, cavity)
            ceiling = frameqa.VISEME_CAV_MAX.get(code)
            if ceiling is not None:
                assert cavity <= ceiling
        assert abs(frameqa.happy_cavity(mouths["E"]) - 0.36) < 0.08


def test_happy_rest_is_open_enough_for_frame_qa():
    from products.animind_core import frameqa, mouthset
    colours = {
        "outline": (40, 30, 30, 255), "cavity": (20, 12, 14, 255),
        "teeth": (247, 244, 236, 255), "tongue": (190, 86, 92, 255), "lip": (142, 72, 78, 255),
    }
    happy = mouthset.draw_expressions(120, colours, open_cap=90, squeeze=0.85)["happy"]
    cavity = frameqa.happy_cavity(happy)
    assert cavity >= frameqa.HAPPY_REST_FLOOR
    assert abs(cavity - frameqa.HAPPY_REST_CAV) < 0.08


def _ink_widths(image: Image.Image) -> dict[float, int]:
    import numpy as np
    alpha = np.asarray(image.getchannel("A")) > 128
    ys, xs = np.nonzero(alpha)
    y0, y1 = int(ys.min()), int(ys.max())

    def width_at(frac: float) -> int:
        y = min(y1, y0 + int((y1 - y0) * frac))
        row = np.nonzero(alpha[y])[0]
        return 0 if row.size == 0 else int(row.max() - row.min() + 1)

    return {frac: width_at(frac) for frac in (0.15, 0.50, 0.80)}


def test_south_park_smile_stays_under_the_happy_face_share():
    """A wide smile on a short face used to reach ~0.18 of the face height."""
    import numpy as np

    from products.animind_core import frameqa, mouthset, qa
    colours = {
        "outline": (40, 30, 30, 255), "cavity": (20, 12, 14, 255),
        "teeth": (247, 244, 236, 255), "tongue": (190, 86, 92, 255), "lip": (142, 72, 78, 255),
    }
    face_h = 180
    cap = 0.95 * qa.EXPRESSION_OPEN_ABS * face_h
    happy = mouthset.draw_expressions(int(0.42 * 240), colours, squeeze=1.0, open_cap=cap)["happy"]
    sprite = np.asarray(happy.convert("RGBA"))
    under = np.zeros_like(sprite)
    under[..., :3] = (232, 186, 156)
    under[..., 3] = 255
    share = qa.open_height(sprite, under) / face_h
    assert share <= qa.EXPRESSION_OPEN_ABS + qa.OPEN_SLACK, share
    cavity = frameqa.happy_cavity(happy)
    assert cavity >= frameqa.HAPPY_REST_FLOOR
    assert abs(cavity - frameqa.HAPPY_REST_CAV) < 0.08
    widths = _ink_widths(happy)
    assert widths[0.15] > widths[0.50] > widths[0.80]


def test_southpark_is_not_ghibli_and_sheet_survives_a_rig_without_boxes(tmp_path):
    assert styles.pick("southpark") == "southpark"
    assert styles.pick("South Park") == "southpark"
    prompt = styles.prompt_style("southpark").lower()
    assert "south park" in prompt and "ghibli" not in prompt and "1980" not in prompt
    skin = tmp_path / "skin"
    view = skin / "views" / "left"
    view.mkdir(parents=True)
    (skin / "rig.json").write_text('{"type": "avatar"}', encoding="utf-8")
    (view / "view.json").write_text(
        '{"canvas": {"w": 100, "h": 120}, "layers": [{"name": "head", "url": "head.png", "x": 10, "y": 12, "w": 40, "h": 50, "z": 2}], "visemes": {}}',
        encoding="utf-8",
    )
    assert sheet._face_box(skin, "left", (100, 120)) == (10, 12, 50, 62)


def test_director_repairs_only_the_failing_scene_and_lists_cameras(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    topic = "Why students should verify claims made by AI"
    names = ["Ada", "Ben"]
    result = director.direct(topic, names, "debate", lang="en-US", use_llm=False)
    assert result["language"] == "en-US"
    assert set(result["plan"]) >= {"premise", "goals", "conflict", "turn", "ending", "scenes"}
    assert result["scriptQA"]["pass"]
    assert {shot["camera"] for shot in result["shots"]} == {"wide", "close on speaker", "reverse"}
    original = result["scenes"][0]["text"]
    broken = [dict(scene) for scene in result["scenes"]]
    broken[2] = {**broken[2], "text": "Nope"}
    fixed, rewritten = director.repair_scenes(result["plan"], broken, names, use_llm=False)
    assert rewritten == [broken[2]["id"]]
    assert fixed[0]["text"] == original
    assert fixed[1]["text"] == broken[1]["text"]
    assert len(fixed[2]["text"].split()) >= 8
    assert fixed[2]["text"] != "Nope"


def test_bench_without_keys_says_real_results_were_not_produced(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    for name in (
        "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY",
        "TOGETHER_API_KEY", "DASHSCOPE_API_KEY", "FAL_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    from products.animind_core import bench

    report = bench.run(
        ["deepseek", "gemini", "together-qwen-image"],
        tmp_path / "bench",
        budget_usd=0.10,
    )
    text = (tmp_path / "bench" / "report.md").read_text(encoding="utf-8")
    assert report["realResults"] is False
    assert text.startswith("Real results were not produced")
    assert "Why students should verify claims made by AI" in text
    assert "a calm teacher with round glasses" in text
    assert (tmp_path / "bench" / "images" / "together-qwen-image.png").is_file()
    assert any(row["metadata"].get("template") == "debate" for row in report["rows"])
    assert any(row["metadata"].get("template") == "monologue" for row in report["rows"])
    assert "not-a-real-key" not in text
