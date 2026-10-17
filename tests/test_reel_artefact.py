# -*- coding: utf-8 -*-
"""Commons photographs for a named artefact. The provider chain is a later flag."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from agents.media.free_images.http import HttpResponse
from modules.reel_artefact.crop import prepare_reel_frame
from modules.reel_artefact.fetch import fetch_artefact
from modules.reel_artefact.motion import motion_override, write_motion_sidecar
from modules.reel_artefact.policy import artefact_mode
from modules.reel_artefact.pricing import hybrid_reel_usd, price_for_model
from modules.reel_artefact.role import image_role_for
from modules.reel_artefact.speech import spoken_numbers, subtitle_text
from modules.shot_planner.entities import Entity, load_entities


def _jpeg(width: int, height: int, color: tuple[int, int, int] = (180, 140, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="JPEG")
    return buf.getvalue()


def _page(title: str, width: int, height: int, license_name: str, url: str) -> dict:
    return {
        "title": title,
        "imageinfo": [
            {
                "url": url,
                "descriptionurl": "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_"),
                "width": width,
                "height": height,
                "mime": "image/jpeg",
                "extmetadata": {
                    "ObjectName": {"value": title.replace("File:", "")},
                    "LicenseShortName": {"value": license_name},
                    "Artist": {"value": "Example Photographer"},
                    "ImageDescription": {"value": title},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
                },
            }
        ],
    }


class _Client:
    def __init__(self, pages: dict, files: dict[str, bytes]) -> None:
        self.pages = pages
        self.files = files

    def get(self, url, params=None, headers=None):
        del headers
        params = params or {}
        if url in self.files:
            return HttpResponse(200, self.files[url])
        if params.get("titles"):
            payload = {"query": {"pages": self.pages["titles"]}}
            return HttpResponse(200, json.dumps(payload).encode())
        if params.get("gsrsearch"):
            payload = {"query": {"pages": self.pages["search"]}}
            return HttpResponse(200, json.dumps(payload).encode())
        return HttpResponse(404, b"")


def _entity(**kwargs) -> Entity:
    base = dict(
        id="sample",
        name="Sample artefact",
        aliases=("sample",),
        kind="artifact",
        canonical_visual="the object",
        era="ancient",
        setting="museum case",
        confusions=(),
        channels=("ancient_knowledge",),
        canonical_shape="a distinct shape",
        commons_search=("sample artefact",),
        commons_titles=(),
    )
    base.update(kwargs)
    return Entity(**base)


def test_hybrid_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REEL_ARTIFACT_IMAGES", raising=False)
    monkeypatch.delenv("REEL_ARTIFACT_IMAGE_PROVIDER", raising=False)
    assert artefact_mode() is None


def test_chain_is_the_default_when_the_flag_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REEL_ARTIFACT_IMAGES", "1")
    monkeypatch.delenv("REEL_ARTIFACT_IMAGE_PROVIDER", raising=False)
    assert artefact_mode() == "chain"
    monkeypatch.setenv("REEL_ARTIFACT_IMAGE_PROVIDER", "commons")
    assert artefact_mode() == "commons"


def test_quimbaya_shape_is_the_pendant_not_a_jet() -> None:
    entities = load_entities("ancient_knowledge")
    quimbaya = next(item for item in entities if item.id == "quimbaya_ornaments")
    assert "delta wings" in quimbaya.canonical_shape
    assert "tail fin" in quimbaya.canonical_shape
    assert "jet" not in quimbaya.canonical_visual.lower()
    assert quimbaya.commons_titles


def test_year_range_is_spoken_with_to_and_the_subtitle_keeps_digits() -> None:
    original = "It dates from 600-1000 AD and was buried."
    spoken = spoken_numbers(original)
    assert spoken == "It dates from 600 to 1000 AD and was buried."
    assert "600" in subtitle_text(original)
    assert "1000" in subtitle_text(original)
    assert "-" in subtitle_text(original)


def test_landscape_photo_becomes_a_blurred_9_16_frame() -> None:
    frame, how = prepare_reel_frame(Image.new("RGB", (1600, 900), (10, 20, 30)))
    assert how == "blur_fill"
    assert frame.size == (1080, 1920)


def test_portrait_photo_is_smart_cropped() -> None:
    frame, how = prepare_reel_frame(Image.new("RGB", (800, 1400), (10, 20, 30)))
    assert how == "smart_crop"
    assert frame.size == (1080, 1920)


def test_title_lock_beats_a_larger_unrelated_hit(tmp_path: Path) -> None:
    small = "https://example.test/quimbaya.jpg"
    army = "https://example.test/army.jpg"
    client = _Client(
        pages={
            "titles": {
                "1": _page("File:Precolombina cultura prc.jpg", 480, 320, "CC BY-SA 4.0", small),
            },
            "search": {
                "2": _page(
                    "File:US Army Battery Baghdad.jpg",
                    1800,
                    1200,
                    "Public domain",
                    army,
                ),
            },
        },
        files={small: _jpeg(480, 320)},
    )
    entity = _entity(
        id="quimbaya_ornaments",
        name="Quimbaya golden ornaments",
        commons_titles=("File:Precolombina cultura prc.jpg",),
        canonical_shape="a small gold pendant with delta wings, a tail fin, and a stylised head",
    )
    fetched = fetch_artefact(
        entity,
        channel="ancient_knowledge",
        cache_dir=tmp_path / "cache",
        client=client,
    )
    assert fetched is not None
    assert fetched.usable_as_still is False
    assert fetched.reference_path.is_file()
    assert fetched.credit_line
    assert "CC BY-SA" in fetched.credit_line


def test_large_public_domain_photo_is_the_still_without_a_caption_credit(tmp_path: Path) -> None:
    url = "https://example.test/gears.jpg"
    client = _Client(
        pages={
            "titles": {"1": _page("File:Antikythera.jpg", 2000, 1400, "CC0", url)},
            "search": {},
        },
        files={url: _jpeg(2000, 1400)},
    )
    entity = _entity(
        id="antikythera_mechanism",
        name="Antikythera Mechanism",
        commons_titles=("File:Antikythera.jpg",),
        canonical_shape="corroded bronze gear fragments",
    )
    dest = tmp_path / "beat.jpg"
    fetched = fetch_artefact(
        entity,
        channel="ancient_knowledge",
        dest=dest,
        cache_dir=tmp_path / "cache",
        client=client,
    )
    assert fetched is not None and dest.is_file()
    assert Image.open(dest).size == (1080, 1920)
    assert fetched.credit_line == ""
    assert motion_override(dest)["profile"] == "slow_push"
    side = json.loads(Path(str(dest) + ".source.json").read_text(encoding="utf-8"))
    assert side["provider"] == "commons"
    assert side["canonical_shape"].startswith("corroded bronze")


def test_close_artefact_beat_uses_a_photograph() -> None:
    entity = _entity(kind="artifact")
    assert image_role_for(entity, shot_type="close", narration="The pendant.") == "photo"
    assert image_role_for(
        entity,
        shot_type="wide",
        narration="An archaeologist at dusk.",
        camera="open sky",
        setting="landscape",
    ) == "flux"


def test_motion_sidecar_selects_a_slow_push(tmp_path: Path) -> None:
    src = tmp_path / "frame.jpg"
    Image.new("RGB", (100, 200), (1, 2, 3)).save(src, format="JPEG")
    write_motion_sidecar(src)
    assert motion_override(src)["zoom_end"] == 1.10
    assert motion_override(src)["zoom_start"] == 1.00


def test_hybrid_cost_uses_commons_at_zero_and_nanobanana_above_flux() -> None:
    assert price_for_model("gemini-2.5-flash-image") == 0.039
    assert price_for_model("gemini-nano-banana-2.1") == 0.0336
    quote = hybrid_reel_usd(photo_beats=14, generic_beats=5, nano_images=1, nano_usd=0.039)
    assert quote["commons_stills"] == 13
    assert quote["flux_usd"] == pytest.approx(0.015)
    assert quote["image_usd"] == pytest.approx(0.054)
