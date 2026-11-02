"""Phase 5: Aiwake-backed avatars, capped providers, director, and the bench."""
import base64
import io

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageOps

from products.animind_core import avatar, director, image_models, sheet, stick, styles


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
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TOGETHER_API_KEY", "not-a-real-key")
    monkeypatch.delenv("ANIMIND_FORCE_MOCK", raising=False)
    prompts = []

    def generate(self, prompt, **options):  # noqa: ARG001
        prompts.append(prompt)
        dest = options["destination"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        avatar._mock_bust().save(dest)
        return image_models.ImageResult(dest, "together", "black-forest-labs/FLUX.1-schnell", 0.003)

    monkeypatch.setattr(image_models.TogetherImageProvider, "generate", generate)
    monkeypatch.setattr(stick, "build", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stick primitives")))
    skin = tmp_path / "skin"
    built = avatar.create_avatar(
        "a calm teacher with round glasses", "stick", kind="stick",
        skin=skin, url=lambda path: path.as_uri(), model_id="together-qwen-image",
    )
    assert len(prompts) == 2
    assert "South Park" in prompts[0] and "stick-figure" in prompts[0]
    assert built["generator"] == "together" and built["realImage"] is True
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
    again = avatar.create_avatar(
        "a calm teacher with round glasses", "stick", kind="stick",
        skin=tmp_path / "skin2", url=lambda path: path.as_uri(), model_id="together-qwen-image",
    )
    assert again["cached"] is True and len(prompts) == 2


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
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TOGETHER_API_KEY", "not-a-real-key")

    def generate(self, prompt, **options):  # noqa: ARG001
        dest = options["destination"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        _realistic_bust().save(dest)
        return image_models.ImageResult(dest, "together", "Qwen/Qwen-Image", 0.01)

    monkeypatch.setattr(image_models.TogetherImageProvider, "generate", generate)
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
