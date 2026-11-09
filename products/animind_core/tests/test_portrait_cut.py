"""Native portrait gate and the existing cutout, with no paid generation."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from products.animind_core import characters, portrait, rig

H = {"Authorization": "Bearer test-key"}


def test_birefnet_backend_is_explicit_and_never_describes_chroma_fallback(monkeypatch):
    class Runtime:
        @staticmethod
        def get_available_providers():
            return ["CPUExecutionProvider", "CUDAExecutionProvider"]

    assert rig._matte_providers(Runtime, "cpu") == ["CPUExecutionProvider"]
    assert rig._matte_providers(Runtime, "cuda") == [
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]
    with pytest.raises(RuntimeError, match="must be cpu, cuda, rocm, or auto"):
        rig._matte_providers(Runtime, "chroma")
    worker = type("Worker", (), {"exitcode": -9})()
    failure = rig._matte_failure(worker)
    assert "OOM/SIGKILL" in failure
    assert "no chroma fallback was used" in failure
    assert "16 GiB" in failure
    monkeypatch.setenv("ANIMIND_MATTE_MODE", "chroma")
    with pytest.raises(RuntimeError, match="must be isolated or in_process"):
        rig.BiRefNetMatte()(Image.new("RGB", (1, 1)))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "1")
    from products.animind_core.app import app

    return TestClient(app)


def _plate(size, color, *, checks=False):
    image = Image.new("RGBA", size, color + (255,))
    if checks:
        paint = ImageDraw.Draw(image)
        for y in range(0, size[1], 8):
            for x in range(0, size[0], 8):
                if (x // 8 + y // 8) % 2:
                    paint.rectangle((x, y, x + 8, y + 8), fill=(255, 255, 0, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 120, 200, 360), fill=(210, 170, 140, 255))
    for x in range(100, 180, 3):
        draw.line((x, 120, x, 70), fill=(80, 60, 40, 255), width=5)
    draw.rectangle((120, 250, 160, 258), fill=(140, 70, 50, 255))
    return image, {"hair": (109, 80), "moustache": (130, 254)}


def test_flat_backdrops_keep_hair_and_moustache_without_softness(tmp_path):
    colors = {
        "black": (0, 0, 0),
        "white": (255, 255, 255),
        "saturated": (255, 0, 0),
        "probe_blue": (16, 32, 96),
    }
    for name, color in colors.items():
        image, probes = _plate((1024, 1280), color)
        before = hashlib.sha256(image.tobytes()).hexdigest()
        out = portrait.extract(image, tmp_path / name, probes=probes)
        assert hashlib.sha256(image.tobytes()).hexdigest() == before
        assert out["ready"] is True
        assert out["status"] == "ready"
        assert out["qa"]["pass"] is True
        assert out["qa"]["probes"]["hair"] > 200
        assert out["qa"]["probes"]["moustache"] > 200
        assert out["source"]["width"] == 1024
        assert out["cutout"]["width"] == 1024
        assert out["source"]["height"] > out["source"]["width"]
        assert out["useful"]["width"] < 1024
        assert out["thumb"]["width"] <= portrait.THUMB_EDGE
        assert out["scenario"] == "separate"
        assert out["stages"][2]["applied"] is False
        assert out["stages"][0]["navyRgb"] is None
        assert Image.open(tmp_path / name / "source.png").size == (1024, 1280)


def test_near_black_hair_on_a_black_stage_is_not_called_ready(tmp_path):
    """The existing key tolerance treats that hair as stage. This unit does not retune it."""
    image = Image.new("RGBA", (1024, 1280), (0, 0, 0, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 120, 200, 360), fill=(210, 170, 140, 255))
    draw.line((109, 120, 109, 70), fill=(20, 20, 20, 255))
    out = portrait.extract(image, tmp_path / "dark-hair", probes={"hair": (109, 80)})
    assert out["qa"]["probes"]["hair"] <= 200
    assert out["ready"] is False
    assert out["status"] == "rejected"


def test_checkerboard_is_not_ready(tmp_path):
    image, probes = _plate((1024, 1280), (0, 0, 0), checks=True)
    out = portrait.extract(image, tmp_path / "checks", probes=probes)
    assert out["qa"]["pass"] is False
    assert out["ready"] is False
    assert out["status"] == "rejected"


def test_legacy_plate_is_not_upscaled(tmp_path):
    image, _probes = _plate((768, 1344), (0, 0, 0))
    path = tmp_path / "legacy.png"
    image.save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    facts = portrait.describe_legacy(Image.open(path))
    assert facts["meetsNativePortrait1k"] is False
    assert facts["limitation"] == "below_native_portrait_1k"
    assert facts["upgrade"] == "regenerate"
    assert facts["rewritten"] is False
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    upscaled = portrait.measure(image.resize((1024, 1792)), provenance="upscaled")
    assert upscaled["meetsNativePortrait1k"] is False


def test_resolution_request_does_not_generate(client, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("paid image call")

    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        forbidden,
    )
    body = {
        "name": "Ada",
        "type": "rig",
        "source": {"kind": "prompt", "prompt": "a person"},
        "requestedResolution": "4k",
        "premium": True,
    }
    refused = client.post("/v1/characters", headers=H, json=body)
    assert refused.status_code == 409
    decision = refused.json()["detail"]
    assert decision["consume"] is False
    assert decision["charged"] is False
    assert decision["mappedPreset"] is None
    assert decision["entitlement"] == "absent"
    assert decision["clientPremiumIgnored"] is True
    assert "adapter_has_no_4k_preset" in decision["reasons"]
    assert "prices" not in decision
    assert decision["upscale"] == "refused"
    plan = client.post("/v1/portraits/plan", headers=H, json={"requestedResolution": "1k"})
    assert plan.status_code == 200
    published = plan.text + refused.text
    assert "production/cost" not in published
    assert "usdPerImage" not in published
    assert "gemini" not in published.lower()
    plan_body = plan.json()
    assert plan_body["productPreset"] == "1k"
    assert plan_body["mappedPreset"] == "1K"
    assert plan_body["consume"] is False
    assert plan_body["actualPixels"] is None
    assert "factory_call_omits_image_size" in plan_body["reasons"]
    with pytest.raises(portrait.PortraitRefused):
        characters.create_character({"requestedResolution": "1k", "name": "Ada"}, "http://test")


def test_factory_and_adapter_sources_match_the_contract():
    factory = Path("products/aiwake/animator/factory/create_puppet.py").read_text(encoding="utf-8")
    chunk = factory.split("def generate_character_image", 1)[1].split("\ndef ", 1)[0]
    assert "image_size" not in chunk
    assert "black background" in factory.lower()
    settings = Path("production/providers/settings.py").read_text(encoding="utf-8")
    assert '{"1K", "2K"}' in settings
    assert "4K" not in settings
    provider = Path("production/providers/image/image_provider.py").read_text(encoding="utf-8")
    assert 'if size not in {"1K", "2K"}' in provider
    assert portrait.stages()[1]["tool"].endswith("chroma_key_cutout")
    assert portrait.stages()[2]["parameter"] is None
