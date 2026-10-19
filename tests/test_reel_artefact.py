# -*- coding: utf-8 -*-
"""Commons photographs for a named artefact. The provider chain is a later flag."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from agents.media.free_images.http import HttpResponse
from modules.reel_artefact.crop import anchor_for, prepare_reel_frame, usable_as_still
from modules.reel_artefact.fetch import fetch_artefact, fetch_gallery, list_candidates
from modules.reel_artefact.motion import motion_override, write_motion_sidecar
from modules.reel_artefact.policy import artefact_mode
from modules.reel_artefact.pricing import hybrid_reel_usd, price_for_model
from modules.reel_artefact.role import image_role_for
from modules.reel_artefact.speech import spoken_numbers, subtitle_text
from modules.shot_planner.entities import Entity, load_entities


def _jpeg(width: int, height: int, color: tuple[int, int, int] = (180, 140, 40)) -> bytes:
    buf = io.BytesIO()
    _textured(width, height, color).save(buf, format="JPEG")
    return buf.getvalue()


def _textured(width: int, height: int, color: tuple[int, int, int]) -> Image.Image:
    """A noisy frame, not a checkerboard. The validator rejects that pattern."""
    import random

    rng = random.Random(7)
    image = Image.new("RGB", (width, height), color)
    pixels = image.load()
    step = max(1, min(width, height) // 40)
    for y in range(0, height, step):
        for x in range(0, width, step):
            shift = rng.randrange(-60, 60)
            tone = tuple(
                max(0, min(255, channel + shift + (x * 40 // max(width, 1))))
                for channel in color
            )
            for dy in range(step):
                if y + dy >= height:
                    break
                for dx in range(step):
                    if x + dx >= width:
                        break
                    pixels[x + dx, y + dy] = tone
    return image


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
    negatives = " ".join(quimbaya.edit_negatives).lower()
    assert "propeller" in negatives
    assert "straight wings" in negatives
    assert "cockpit" in negatives
    assert quimbaya.text_only_safe is False
    baghdad = next(item for item in entities if item.id == "baghdad_battery")
    assert baghdad.text_only_safe is True
    antikythera = next(item for item in entities if item.id == "antikythera_mechanism")
    assert antikythera.text_only_safe is False
    assert "intact gears" in " ".join(antikythera.edit_negatives).lower()
    assert any("airplane" in item.lower() for item in quimbaya.commons_search)
    assert quimbaya.commons_categories
    assert baghdad.commons_categories
    assert antikythera.commons_categories


def test_year_range_is_spoken_with_to_and_the_subtitle_keeps_digits() -> None:
    original = "It dates from 600-1000 AD and was buried."
    spoken = spoken_numbers(original)
    assert spoken == "It dates from 600 to 1000 AD and was buried."
    assert "600" in subtitle_text(original)
    assert "1000" in subtitle_text(original)
    assert "-" in subtitle_text(original)


def test_landscape_photo_becomes_a_vertical_crop() -> None:
    frame, how = prepare_reel_frame(Image.new("RGB", (1600, 900), (10, 20, 30)))
    assert how == "window"
    assert frame.size == (1080, 1920)


def test_portrait_photo_is_smart_cropped() -> None:
    frame, how = prepare_reel_frame(Image.new("RGB", (800, 1400), (10, 20, 30)))
    assert how == "smart_crop"
    assert frame.size == (1080, 1920)


def test_a_720px_photo_is_upscaled_locally_and_a_400px_file_stays_a_reference() -> None:
    frame, how = prepare_reel_frame(Image.new("RGB", (900, 600), (10, 20, 30)))
    assert how.startswith("upscale_")
    assert frame.size == (1080, 1920)
    assert usable_as_still(900, 600) is True
    assert usable_as_still(400, 203) is False


def test_a_wide_photo_slides_when_the_anchor_changes() -> None:
    image = Image.new("RGB", (2000, 800), (20, 20, 220))
    image.paste(Image.new("RGB", (1000, 800), (220, 20, 20)), (0, 0))
    left, left_how = prepare_reel_frame(image, anchor="left")
    right, right_how = prepare_reel_frame(image, anchor="right")
    assert left_how == "window" and right_how == "window"
    assert left.getpixel((540, 960)) != right.getpixel((540, 960))
    assert anchor_for(0) == "center"
    assert anchor_for(1) != anchor_for(0)


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


def test_thumbnail_is_downloaded_and_the_original_is_not(tmp_path: Path) -> None:
    from agents.media.free_images.http import USER_AGENT

    original = "https://upload.example/original.jpg"
    thumb = "https://upload.example/thumb-1280.jpg"
    seen: list[tuple] = []

    class _ThumbClient:
        def get(self, url, params=None, headers=None):
            seen.append((url, params or {}, headers or {}))
            params = params or {}
            if params.get("titles"):
                page = _page("File:Antikythera.jpg", 4000, 3000, "CC0", original)
                page["imageinfo"][0]["thumburl"] = thumb
                payload = {"query": {"pages": {"1": page}}}
                return HttpResponse(200, json.dumps(payload).encode())
            if url == thumb:
                return HttpResponse(200, _jpeg(1280, 720))
            return HttpResponse(404, b"missing")

    fetched = fetch_artefact(
        _entity(
            id="antikythera_mechanism",
            name="Antikythera Mechanism",
            commons_titles=("File:Antikythera.jpg",),
        ),
        channel="ancient_knowledge",
        cache_dir=tmp_path / "cache",
        client=_ThumbClient(),
    )
    assert fetched is not None and fetched.reference_path.is_file()
    assert any(url == thumb for url, _, _ in seen)
    assert all(url != original for url, _, _ in seen)
    assert any(params.get("iiurlwidth") == "1280" for _, params, _ in seen)
    assert USER_AGENT.startswith("OmniEngine/1.0")
    assert "https://github.com/diogolean/Omni-Engine" in USER_AGENT
    assert any(
        "https://github.com/diogolean/Omni-Engine" in (headers.get("User-Agent") or "")
        for _, _, headers in seen
    )


def test_commons_429_retries_then_logs_the_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    monkeypatch.setattr("modules.reel_artefact.fetch._pause", lambda _seconds: None)
    url = "https://example.test/gears.jpg"
    hits = {"n": 0}

    class _RetryClient:
        def get(self, request_url, params=None, headers=None):
            del headers
            params = params or {}
            if params.get("titles"):
                payload = {"query": {"pages": {"1": _page("File:Gears.jpg", 900, 700, "CC0", url)}}}
                return HttpResponse(200, json.dumps(payload).encode())
            if request_url != url:
                return HttpResponse(404, b"")
            hits["n"] += 1
            if hits["n"] < 3:
                return HttpResponse(429, b"<html>slow</html>", {"Retry-After": "0"})
            return HttpResponse(200, _jpeg(900, 700))

    with caplog.at_level(logging.WARNING):
        fetched = fetch_artefact(
            _entity(commons_titles=("File:Gears.jpg",)),
            channel="ancient_knowledge",
            cache_dir=tmp_path / "cache",
            client=_RetryClient(),
        )
    assert fetched is not None
    assert hits["n"] == 3
    assert "status=429" in caplog.text


def test_a_cached_reference_is_not_downloaded_again(tmp_path: Path) -> None:
    url = "https://example.test/jar.jpg"
    hits = {"n": 0}

    class _Once:
        def get(self, request_url, params=None, headers=None):
            del headers
            hits["n"] += 1
            params = params or {}
            if params.get("titles"):
                payload = {"query": {"pages": {"1": _page("File:Jar.jpg", 500, 400, "CC0", url)}}}
                return HttpResponse(200, json.dumps(payload).encode())
            if request_url != url:
                return HttpResponse(404, b"")
            return HttpResponse(200, _jpeg(500, 400))

    entity = _entity(commons_titles=("File:Jar.jpg",))
    cache = tmp_path / "cache"
    first = fetch_artefact(entity, channel="ancient_knowledge", cache_dir=cache, client=_Once())
    after = hits["n"]
    second = fetch_artefact(entity, channel="ancient_knowledge", cache_dir=cache, client=_Once())
    assert first is not None and second is not None
    assert second.reference_path == first.reference_path
    assert hits["n"] == after


def test_default_cache_lives_in_the_channel_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from modules.reel_artefact.fetch import artefact_cache_dir

    monkeypatch.setenv("CHANNEL_STORE_ROOT", str(tmp_path))
    path = artefact_cache_dir("ancient_knowledge", "quimbaya_ornaments")
    assert path.parts[-3:] == ("store", "artefact_cache", "quimbaya_ornaments")
    assert "_tmp" not in path.parts


def test_two_licensed_photos_are_both_saved(tmp_path: Path) -> None:
    first = "https://example.test/pendant-a.jpg"
    second = "https://example.test/pendant-b.jpg"
    client = _Client(
        pages={
            "titles": {
                "1": _page("File:Quimbaya pendant a.jpg", 1600, 1200, "CC0", first),
                "2": _page("File:Quimbaya pendant b.jpg", 1400, 1000, "CC BY 4.0", second),
            },
            "search": {},
        },
        files={first: _jpeg(1600, 1200), second: _jpeg(1400, 1000, (40, 80, 120))},
    )
    entity = _entity(
        id="quimbaya_ornaments",
        name="Quimbaya golden ornaments",
        commons_titles=("File:Quimbaya pendant a.jpg", "File:Quimbaya pendant b.jpg"),
        commons_search=("Quimbaya gold pendant",),
    )
    gallery = fetch_gallery(
        entity,
        channel="ancient_knowledge",
        cache_dir=tmp_path / "cache",
        client=client,
    )
    assert len(gallery) == 2
    assert all(photo.usable_as_still for photo in gallery)
    assert {photo.reference_path.name for photo in gallery} == {"photo_00.jpg", "photo_01.jpg"}


def test_an_openverse_hit_is_kept_when_commons_has_nothing(tmp_path: Path) -> None:
    image_url = "https://images.example/quimbaya.jpg"

    class _Openverse:
        def get(self, url, params=None, headers=None):
            del headers
            params = params or {}
            if url == image_url:
                return HttpResponse(200, _jpeg(1600, 1100))
            if "openverse.org" in url:
                payload = {
                    "results": [
                        {
                            "title": "Quimbaya gold pendant",
                            "creator": "Museum photographer",
                            "license": "cc0",
                            "url": image_url,
                            "foreign_landing_url": "https://museum.example/quimbaya",
                            "width": 1600,
                            "height": 1100,
                        }
                    ]
                }
                return HttpResponse(200, json.dumps(payload).encode())
            return HttpResponse(404, b"")

    gallery = fetch_gallery(
        _entity(
            id="quimbaya_ornaments",
            name="Quimbaya golden ornaments",
            commons_titles=(),
            commons_search=("Quimbaya gold pendant",),
        ),
        channel="ancient_knowledge",
        cache_dir=tmp_path / "cache",
        client=_Openverse(),
    )
    assert len(gallery) == 1
    assert gallery[0].provider == "openverse"
    assert gallery[0].usable_as_still is True


def test_met_410_is_skipped_without_a_second_try(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    url = "https://example.test/gears.jpg"
    hits = {"met": 0}

    class _MetGone:
        def get(self, request_url, params=None, headers=None):
            del headers
            params = params or {}
            if "metmuseum.org" in request_url:
                hits["met"] += 1
                return HttpResponse(410, b"gone")
            if params.get("titles"):
                page = _page("File:Gears.jpg", 2000, 1400, "CC0", url)
                payload = {"query": {"pages": {"1": page}}}
                return HttpResponse(200, json.dumps(payload).encode())
            if request_url == url:
                return HttpResponse(200, _jpeg(2000, 1400))
            return HttpResponse(404, b"")

    with caplog.at_level(logging.INFO):
        fetched = fetch_artefact(
            _entity(
                commons_titles=("File:Gears.jpg",),
                commons_search=("Antikythera mechanism",),
            ),
            channel="ancient_knowledge",
            cache_dir=tmp_path / "cache",
            client=_MetGone(),
        )
    assert fetched is not None and fetched.usable_as_still is True
    assert hits["met"] == 1
    assert "status=410" in caplog.text


def test_a_figure_pendant_title_is_not_the_quimbaya_object() -> None:
    from agents.media.free_images.models import Candidate
    from modules.reel_artefact.fetch import _accept

    entity = _entity(
        name="Quimbaya golden ornaments",
        aliases=("quimbaya", "golden jet"),
        canonical_shape="a small gold pendant with delta wings, a tail fin, and a stylised head",
    )
    figure = Candidate(
        provider="wikimedia",
        title="Gold Figure Pendant",
        description="Quimbaya culture, Museo del Oro",
        license_raw="CC0",
        download_url="https://example.test/figure.jpg",
        width=2000,
        height=2000,
    )
    airplane = Candidate(
        provider="wikimedia",
        title="Quimbaya airplanes pendant",
        description="",
        license_raw="CC0",
        download_url="https://example.test/plane.jpg",
        width=1600,
        height=1200,
    )
    assert _accept(figure, entity, title_lock=False) is False
    assert _accept(airplane, entity, title_lock=False) is True


def test_a_modern_golden_jet_is_not_kept_as_the_artefact(monkeypatch: pytest.MonkeyPatch) -> None:
    del monkeypatch
    jet = "https://images.example/rose-jet.jpg"
    pendant = "https://images.example/quimbaya-plane.jpg"

    class _Mixed:
        def get(self, url, params=None, headers=None):
            del headers
            params = params or {}
            if "openverse.org" in url and "golden jets" in str(params.get("q") or ""):
                payload = {
                    "results": [
                        {
                            "title": "Rose Golden Jet 20070601",
                            "license": "cc0",
                            "url": jet,
                            "width": 3456,
                            "height": 2304,
                        },
                        {
                            "title": "Quimbaya airplanes pendant",
                            "license": "cc0",
                            "url": pendant,
                            "width": 1600,
                            "height": 1200,
                        },
                    ]
                }
                return HttpResponse(200, json.dumps(payload).encode())
            return HttpResponse(404, b"")

    found = list_candidates(
        _entity(
            name="Quimbaya golden ornaments",
            aliases=("quimbaya", "golden jet", "jet planes"),
            commons_titles=(),
            commons_search=("golden jets",),
        ),
        client=_Mixed(),
    )
    urls = {item.download_url for item in found}
    assert pendant in urls
    assert jet not in urls


def test_europeana_public_domain_zero_is_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    image_url = "https://images.example/fragment.jpg"
    monkeypatch.setenv("EUROPEANA_API_KEY", "test-key")

    class _Europeana:
        def get(self, url, params=None, headers=None):
            del headers
            if "europeana.eu" in url:
                payload = {
                    "items": [
                        {
                            "title": ["Antikythera mechanism fragments"],
                            "dcCreator": ["National Museum"],
                            "rights": ["http://creativecommons.org/publicdomain/zero/1.0/"],
                            "edmIsShownBy": [image_url],
                            "guid": "https://europeana.example/item",
                        }
                    ]
                }
                return HttpResponse(200, json.dumps(payload).encode())
            return HttpResponse(404, b"")

    found = list_candidates(
        _entity(
            name="Antikythera Mechanism",
            commons_titles=(),
            commons_search=("Antikythera mechanism fragments",),
        ),
        client=_Europeana(),
    )
    assert any(item.provider == "europeana" and item.download_url == image_url for item in found)


def test_hybrid_cost_uses_commons_at_zero_and_nanobanana_above_flux() -> None:
    assert price_for_model("gemini-2.5-flash-image") == 0.039
    assert price_for_model("gemini-nano-banana-2.1") == 0.0336
    quote = hybrid_reel_usd(photo_beats=14, generic_beats=5, nano_images=1, nano_usd=0.039)
    assert quote["commons_stills"] == 13
    assert quote["flux_usd"] == pytest.approx(0.015)
    assert quote["image_usd"] == pytest.approx(0.054)
