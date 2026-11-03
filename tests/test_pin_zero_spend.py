# -*- coding: utf-8 -*-
"""Pins with a zero cap never call a paid provider."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from PIL import Image

from distribution.scheduling.inventory import (
    build_caption_regex,
    ensure_writer_pin_copy,
)
from distribution.scheduling.scheduler import PinterestScheduler
from production.cost.spend_ledger import SpendCapExceeded, pin_cap_scope, reset_all
from production.formats.economic_reel.artefact.provider_health import reset_providers
from production.formats.pins.flux_photo import generate_flux_photo
from production.formats.pins.image_pool import reuse_pool_image
from production.formats.pins.spend import (
    guard_pin_spend,
    paid_image_fallback,
    spend_cap_usd,
    text_model,
    text_model_chain,
)
from production.providers.llm import cheap_llm
from shared.channel_flags import flag, value

_FREE_MODEL = "google/gemma-4-31b-it:free"
_FREE_CHAIN = [
    ("openrouter", "google/gemma-4-31b-it:free"),
    ("openrouter", "google/gemma-4-26b-a4b-it:free"),
    ("openrouter", "xingchen-agi/xing4.0-29b-a4b:free"),
    ("openrouter", "liquid/lfm-2.5-2.6b:free"),
]


def _photo(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (64, 96))
    pixels = img.load()
    for y in range(96):
        for x in range(64):
            pixels[x, y] = ((x * 13 + y * 7) % 256, (x * 3 + 40) % 256, (y * 5 + 20) % 256)
    img.save(path, "JPEG")


def _claude_entry(**extra: object) -> dict:
    entry = {
        "post_id": "post_paititi",
        "topic": "Paititi",
        "subject_slug": "paititi",
        "original_caption": "The city is still under the canopy.",
        "pinterest_title": "Claude title",
        "pinterest_caption": "Claude caption.",
        "visual_hook": "OLD HOOK",
        "copy_provider": "anthropic",
        "copy_model": "claude-sonnet-4-5",
        "copy_generated_at": "2026-10-19T00:00:00Z",
        "variant_index": 0,
        "publication_status": {"posted_on_pinterest": False},
    }
    entry.update(extra)
    return entry


def test_zero_spend_flags_are_per_channel() -> None:
    assert flag("ancient_knowledge", "PIN_PAID_IMAGE_FALLBACK", True) is False
    assert paid_image_fallback("ancient_knowledge") is False
    assert flag("wonder_feed", "PIN_PAID_IMAGE_FALLBACK", True) is True
    assert paid_image_fallback("wonder_feed") is True
    assert value("ancient_knowledge", "PIN_SPEND_CAP_USD") == 0.0
    assert spend_cap_usd("ancient_knowledge") == 0.0
    assert spend_cap_usd("wonder_feed") is None
    provider, model = text_model("ancient_knowledge")
    assert provider == "openrouter"
    assert model == _FREE_MODEL
    assert text_model_chain("ancient_knowledge") == _FREE_CHAIN
    assert all(":free" in item[1] for item in _FREE_CHAIN)
    assert "llama-3.3-70b" not in model
    assert text_model("wonder_feed") is None
    assert text_model_chain("wonder_feed") == []


def test_free_model_estimates_zero() -> None:
    assert cheap_llm.estimate_usd("openrouter", _FREE_MODEL, 1000, 1000) == 0.0
    assert cheap_llm.price("openrouter", _FREE_MODEL) == (0.0, 0.0)


def test_zero_cap_blocks_a_paid_call_and_allows_a_free_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_all()
    reset_providers()
    calls: list[str] = []

    def _post(url: str, key: str, body: dict, timeout: float) -> dict:
        model = str(body.get("model") or "")
        calls.append(model)
        if ":free" not in model:
            raise AssertionError(model)
        return {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "cost": 1.5},
        }

    monkeypatch.setattr(cheap_llm, "_post_json", _post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key")
    with pin_cap_scope(0.0):
        with pytest.raises(SpendCapExceeded, match="PIN SPEND CAP"):
            cheap_llm.complete(
                "Write the pin.",
                source="pin_copy",
                chain=[("deepinfra", "deepseek-ai/DeepSeek-V4-Flash")],
                max_tokens=16,
            )
        result = cheap_llm.complete(
            "Write the pin.",
            source="pin_copy",
            chain=[("openrouter", _FREE_MODEL)],
            max_tokens=16,
        )
    assert calls == [_FREE_MODEL]
    assert result.cost_usd == 0.0
    with pytest.raises(SpendCapExceeded, match="no request sent"):
        guard_pin_spend(
            "ancient_knowledge",
            0.003,
            provider="deepinfra",
            model="black-forest-labs/FLUX.1-schnell",
            source="pin_image",
        )


def test_flux_is_not_called_when_the_pin_cap_is_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: list[str] = []

    def _adapter(**_kwargs: object):
        called.append("adapter")

        class _Adapter:
            def generate(self, *_args: object, **_kwargs: object) -> Path:
                called.append("generate")
                raise AssertionError("paid image")

        return _Adapter()

    monkeypatch.setattr(
        "production.providers.image.image_provider.get_image_adapter", _adapter
    )
    with pytest.raises(SpendCapExceeded, match="PIN SPEND CAP"):
        generate_flux_photo(
            topic="Paititi",
            channel_id="ancient_knowledge",
            dest_dir=tmp_path,
            stem="paititi",
        )
    assert called == []


def test_free_text_failure_uses_the_template_and_not_deepseek(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    chains: list[object] = []

    def _complete(*_args: object, **kwargs: object) -> object:
        chains.append(kwargs.get("chain"))
        raise RuntimeError("free model down")

    def _writer(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("paid writer")

    monkeypatch.setattr("production.providers.llm.cheap_llm.complete", _complete)
    monkeypatch.setattr("agents.mcp.text_model.complete_writer_primary", _writer)
    entry = _claude_entry(channel_id="ancient_knowledge")
    with caplog.at_level(logging.WARNING):
        assert ensure_writer_pin_copy(entry, use_ai=True) is True
    assert chains == [[spec] for spec in _FREE_CHAIN]
    assert "PIN TEXT FALLBACK" in caplog.text
    assert "using template" in caplog.text
    assert entry["copy_provider"] == "template"
    assert entry["copy_model"] == "template"
    assert entry["pinterest_caption"] == build_caption_regex(
        "The city is still under the canopy.", 0
    )


def test_free_chain_skips_404_and_429_and_never_calls_paid(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A removed model and a rate limit try the next :free id. Paid stays idle."""
    calls: list[str] = []

    def _complete(*_args: object, **kwargs: object) -> object:
        chain = kwargs.get("chain") or []
        model = chain[0][1]
        calls.append(model)
        if not str(model).endswith(":free"):
            raise AssertionError(model)
        if model == _FREE_CHAIN[0][1]:
            from production.providers.llm.cheap_llm import CheapLLMUnavailable

            raise CheapLLMUnavailable(f"openrouter:{model}:HTTP404")
        if model == _FREE_CHAIN[1][1]:
            from production.providers.llm.cheap_llm import CheapLLMUnavailable

            raise CheapLLMUnavailable(f"openrouter:{model}:HTTP429")

        class _Result:
            text = json.dumps(
                {
                    "pinterest_title": "Nazca lines from the air",
                    "pinterest_caption": "The figures are still there.",
                    "visual_hook": "Why the Nazca lines still point up",
                }
            )
            provider = "openrouter"

        result = _Result()
        result.model = model
        return result

    def _writer(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("paid writer")

    monkeypatch.setattr("production.providers.llm.cheap_llm.complete", _complete)
    monkeypatch.setattr("agents.mcp.text_model.complete_writer_primary", _writer)
    entry = _claude_entry(channel_id="ancient_knowledge", topic="Nazca Lines")
    with caplog.at_level(logging.WARNING):
        assert ensure_writer_pin_copy(entry, use_ai=True) is True
    assert calls == [spec[1] for spec in _FREE_CHAIN[:3]]
    assert entry["copy_model"] == _FREE_CHAIN[2][1]
    assert "HTTP 404" in caplog.text
    assert "HTTP 429" in caplog.text
    assert "using template" not in caplog.text


def test_paid_and_claude_ids_are_not_pin_fallbacks() -> None:
    from production.formats.pins import spend

    dropped = spend.text_model_chain("ancient_knowledge")
    assert all(model.endswith(":free") for _provider, model in dropped)
    assert spend._is_free_model("google/gemma-4-31b-it:free")
    assert spend._is_claude("anthropic", "claude-sonnet-4-5")
    refused = spend._parse_model("anthropic:claude-sonnet-4-5")
    assert refused is not None
    assert spend._is_claude(*refused)


def test_named_model_is_opt_in_and_does_not_fall_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A channel that names DeepSeek calls that model and no other."""
    chains: list[object] = []

    def _complete(*_args: object, **kwargs: object) -> object:
        chains.append(kwargs.get("chain"))

        class _Result:
            text = json.dumps(
                {
                    "pinterest_title": "Stone still holds the road",
                    "pinterest_caption": "The pit is still open.",
                    "visual_hook": "Who dug the pit",
                }
            )
            provider = "deepinfra"
            model = "deepseek-ai/DeepSeek-V4-Flash"

        return _Result()

    def _writer(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("default writer")

    monkeypatch.setattr(
        "production.formats.pins.spend.text_model_chain",
        lambda _channel: [("deepinfra", "deepseek-ai/DeepSeek-V4-Flash")],
    )
    monkeypatch.setattr(
        "production.formats.pins.spend.pin_text_configured",
        lambda _channel: True,
    )
    monkeypatch.setattr("production.providers.llm.cheap_llm.complete", _complete)
    monkeypatch.setattr("agents.mcp.text_model.complete_writer_primary", _writer)
    entry = _claude_entry(channel_id="wonder_feed", topic="Oak Island")
    assert ensure_writer_pin_copy(entry, use_ai=True) is True
    assert chains == [[("deepinfra", "deepseek-ai/DeepSeek-V4-Flash")]]
    assert entry["copy_provider"] == "deepinfra"
    assert entry["copy_model"] == "deepseek-ai/DeepSeek-V4-Flash"


def test_pool_token_match_copies_an_owned_still(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    library = tmp_path / "scene"
    source = library / "paititi_ruins.jpg"
    _photo(source)
    (library / "index.json").write_text(
        json.dumps(
            {
                "version": 1,
                "reels": 3,
                "items": [
                    {
                        "file": "paititi_ruins.jpg",
                        "sha": "abc",
                        "scene_type": "landscape",
                        "geography": "andes",
                        "prompt": "stone city in the jungle",
                        "tags": ["paititi", "jungle", "stone"],
                        "uses": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("REEL_SCENE_LIBRARY_DIR", str(library))
    entry = {"post_id": "post_paititi", "topic": "Paititi", "subject_slug": "paititi"}
    found = reuse_pool_image("ancient_knowledge", entry, outputs_dir=tmp_path / "out")
    assert found is not None and found.is_file()
    assert found.resolve() != source.resolve()
    assert source.is_file()
    assert entry["image_source"] == "pool"
    assert entry["image_credit_required"] is False
    assert entry["generated_by"] == "pool"
    missed = reuse_pool_image(
        "ancient_knowledge",
        {"post_id": "post_other", "topic": "Voynich manuscript"},
        outputs_dir=tmp_path / "out",
    )
    assert missed is None
    assert reuse_pool_image("ancient_knowledge", {"topic": ""}, outputs_dir=None) is None


def test_ancient_knowledge_pin_calls_no_paid_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Library miss, free-license miss, pool miss: skip. Flux and DeepSeek stay idle."""
    paid: list[str] = []
    chains: list[object] = []

    def _complete(*_args: object, **kwargs: object) -> object:
        chains.append(kwargs.get("chain"))
        raise RuntimeError("free model down")

    def _writer(*_args: object, **_kwargs: object) -> object:
        paid.append("writer")
        raise AssertionError("paid writer")

    def _flux(*_args: object, **_kwargs: object) -> object:
        paid.append("flux")
        raise AssertionError("flux")

    def _generate(*_args: object, **_kwargs: object) -> object:
        paid.append("image-generate")
        raise AssertionError("image generate")

    def _post(*_args: object, **_kwargs: object) -> dict:
        paid.append("http")
        raise AssertionError("http")

    monkeypatch.setattr("production.formats.pins.config.CHANNEL_ID", "ancient_knowledge")
    monkeypatch.setattr("production.formats.pins.config.TARGET_URL", "")
    monkeypatch.setattr("production.providers.llm.cheap_llm.complete", _complete)
    monkeypatch.setattr("production.providers.llm.cheap_llm._post_json", _post)
    monkeypatch.setattr("agents.mcp.text_model.complete_writer_primary", _writer)
    monkeypatch.setattr("production.formats.pins.flux_photo.ensure_flux_photo", _flux)
    monkeypatch.setattr("production.formats.pins.flux_photo.generate_flux_photo", _flux)
    monkeypatch.setattr(
        "production.providers.image.together_image.TogetherImageAdapter.generate",
        _generate,
    )
    monkeypatch.setattr(
        "production.formats.pins.articles.article_link_required",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "distribution.scheduling.library_source.ensure_local_library",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        PinterestScheduler,
        "_free_license_photo",
        lambda self, entry, channel: None,
    )

    def _load(self: object) -> list[dict]:
        self.rows = []  # type: ignore[attr-defined]
        return []

    monkeypatch.setattr("distribution.scheduling.ledger.PinLedger.load", _load)
    monkeypatch.setattr(
        "production.formats.pins.image_transformer.PinTransformer.get_pin_bytes",
        lambda self, record: None,
    )

    entry = _claude_entry(
        channel_id="ancient_knowledge",
        target_url="https://ancientknowledge.example/paititi/",
    )
    (tmp_path / "master_inventory.json").write_text(
        json.dumps({"schema_version": "2.0", "entries": [entry]}),
        encoding="utf-8",
    )
    scheduler = PinterestScheduler(
        outputs_dir=tmp_path,
        pins_per_run=1,
        dry_run=True,
        no_wait=True,
        context={"skip_board_fetch": True, "account_id": "ancient_knowledge"},
    )
    with caplog.at_level(logging.INFO):
        stats = scheduler.run()
    assert stats["published"] == 0
    assert stats["skipped"] == 1
    assert paid == []
    assert chains == [[spec] for spec in _FREE_CHAIN]
    assert "no free image" in caplog.text
    saved = json.loads((tmp_path / "master_inventory.json").read_text(encoding="utf-8"))
    assert saved["entries"][0]["publication_status"]["posted_on_pinterest"] is False


def test_paid_fallback_still_calls_flux_when_the_flag_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def _flux(*_args: object, **_kwargs: object) -> Path:
        calls.append("flux")
        dest = tmp_path / "library" / "images" / "oak.jpg"
        _photo(dest)
        return dest

    def _pool(*_args: object, **_kwargs: object) -> None:
        calls.append("pool")
        return None

    monkeypatch.setattr("production.formats.pins.config.CHANNEL_ID", "wonder_feed")
    monkeypatch.setattr("production.formats.pins.flux_photo.ensure_flux_photo", _flux)
    monkeypatch.setattr("production.formats.pins.image_pool.reuse_pool_image", _pool)
    monkeypatch.setattr(
        PinterestScheduler,
        "_free_license_photo",
        lambda self, entry, channel: None,
    )
    scheduler = PinterestScheduler(outputs_dir=tmp_path, dry_run=True, no_wait=True)
    path, delete_after = scheduler._ensure_photo(
        {"post_id": "post_oak", "topic": "Oak Island"},
        "wonder_feed/library/",
    )
    assert calls == ["flux"]
    assert path is not None and path.is_file()
    assert delete_after is False


def test_zero_fallback_reuses_the_pool_before_skipping(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    library = tmp_path / "scene"
    source = library / "paititi.jpg"
    _photo(source)
    (library / "index.json").write_text(
        json.dumps(
            {
                "version": 1,
                "items": [
                    {
                        "file": "paititi.jpg",
                        "tags": ["paititi"],
                        "prompt": "paititi",
                        "scene_type": "landscape",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("REEL_SCENE_LIBRARY_DIR", str(library))
    monkeypatch.setattr("production.formats.pins.config.CHANNEL_ID", "ancient_knowledge")
    flux_calls: list[str] = []
    monkeypatch.setattr(
        "production.formats.pins.flux_photo.ensure_flux_photo",
        lambda *_args, **_kwargs: flux_calls.append("flux"),
    )
    monkeypatch.setattr(
        PinterestScheduler,
        "_free_license_photo",
        lambda self, entry, channel: None,
    )
    scheduler = PinterestScheduler(outputs_dir=tmp_path, dry_run=True, no_wait=True)
    entry = {"post_id": "post_paititi", "topic": "Paititi", "subject_slug": "paititi"}
    path, delete_after = scheduler._ensure_photo(entry, "library/")
    assert flux_calls == []
    assert path is not None and path.is_file()
    assert path.resolve() != source.resolve()
    assert source.is_file()
    assert delete_after is True
    assert entry["image_source"] == "pool"

    with caplog.at_level(logging.INFO):
        missed, _flag = scheduler._ensure_photo(
            {"post_id": "post_voynich", "topic": "Voynich manuscript"},
            "library/",
        )
    assert missed is None
    assert flux_calls == []
    assert "no free image" in caplog.text
