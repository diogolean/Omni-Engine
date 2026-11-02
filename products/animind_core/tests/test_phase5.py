"""Phase 5: Aiwake-backed avatars, capped providers, director, and the bench."""
import base64
import io

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageOps

from products.animind_core import avatar, director, image_models, stick


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


def test_together_and_openrouter_record_cost_and_stop_at_the_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TOGETHER_API_KEY", "not-a-real-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.delenv("TOGETHER_IMAGE_MODEL", raising=False)
    encoded = _png_b64()
    seen = []

    def post(url, **kwargs):
        seen.append(url)
        if "together" in url:
            return _Response({"data": [{"b64_json": encoded}]})
        return _Response({
            "choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{encoded}"}}]}}],
            "usage": {"cost": 0.02, "prompt_tokens": 12, "completion_tokens": 0},
        })

    monkeypatch.setattr(image_models.httpx, "post", post)
    budget = image_models.RunBudget(0.005)
    dest = tmp_path / "together.png"
    result = image_models.provider("together-flux-schnell").generate("a bust", destination=dest, budget=budget)
    assert result.cost_usd == 0.003 and dest.is_file()
    assert seen[0] == "https://api.together.xyz/v1/images/generations"
    with pytest.raises(image_models.BudgetExceeded):
        image_models.provider("together-flux-schnell").generate(
            "again", destination=tmp_path / "again.png", budget=budget,
        )
    qwen = image_models.provider("openrouter-qwen-image").generate(
        "a bust", destination=tmp_path / "qwen.png", budget=image_models.RunBudget(0.10),
    )
    assert qwen.cost_usd == 0.02
    assert image_models.provider("openrouter-flux-kontext").spec.keyEnv == "OPENROUTER_API_KEY"
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
        skin=skin, url=lambda path: path.as_uri(), model_id="together-flux-schnell",
    )
    assert len(prompts) == 2
    assert "South Park" in prompts[0] and "stick-figure" in prompts[0]
    assert built["generator"] == "together" and built["realImage"] is True
    assert set(built["views"]) == {"front", "left", "right"}
    assert set(built["views"]["left"]["visemes"]) == set("XABCDEFGH")
    assert {"torso", "neck", "head", "eye_white", "iris", "eyes_half", "eyes_closed", "brow_left", "brow_right"} <= {
        layer["name"] for layer in built["views"]["left"]["layers"]
    }
    assert "happy" in built["views"]["left"]["expressions"]
    assert "alegre_left" in built["views"]["left"]["emotionBrows"]
    assert built["views"]["left"]["eyes"]["blink"] == "eyes_closed"
    assert built["viewStatus"]["right"] == "mirror of left"
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
        skin=tmp_path / "skin2", url=lambda path: path.as_uri(), model_id="together-flux-schnell",
    )
    assert again["cached"] is True and len(prompts) == 2


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
        ["deepseek", "gemini", "together-flux-schnell"],
        tmp_path / "bench",
        budget_usd=0.10,
    )
    text = (tmp_path / "bench" / "report.md").read_text(encoding="utf-8")
    assert report["realResults"] is False
    assert text.startswith("Real results were not produced")
    assert "Why students should verify claims made by AI" in text
    assert "a calm teacher with round glasses" in text
    assert (tmp_path / "bench" / "images" / "together-flux-schnell.png").is_file()
    assert any(row["metadata"].get("template") == "debate" for row in report["rows"])
    assert any(row["metadata"].get("template") == "monologue" for row in report["rows"])
    assert "not-a-real-key" not in text
