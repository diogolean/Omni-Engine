# -*- coding: utf-8 -*-
"""Free-license image sourcing: licenses, policy, quality, and Flux fallback."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from production.providers.image.free.attribution import append_credit, credit_for_image, credit_line
from production.providers.image.free.http import HttpResponse
from production.providers.image.free.integrate import FreeImageUnavailable, acquire_free_file, load_card_source, wrap_image_generate
from production.providers.image.free.licenses import classify_license, license_allowed
from production.providers.image.free.models import Candidate, FreeImageHit, SourceDecision
from production.providers.image.free.policy import default_policy, resolve_policy
from production.providers.image.free.providers import PexelsProvider, SmithsonianProvider, UnsplashProvider, default_providers, parse_loc, parse_nasa, parse_openverse, parse_pexels, parse_smithsonian, parse_unsplash, parse_wikimedia
from production.providers.image.free.quality import image_has_watermark, prepare_portrait, rank_candidates
from production.providers.image.free.query import build_query
from production.providers.image.free.sourcer import resolve_image_source
from production.formats.pins.image_transformer import PinTransformer


def _jpeg(width: int, height: int, color=(30, 80, 120), watermark: bool = False) -> bytes:
    image = Image.new("RGB", (width, height), color)
    if watermark:
        for y in range(12, 90):
            for x in range(12, 220):
                if (x // 4 + y) % 3 == 0:
                    image.putpixel((x, y), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, height // 2, width - 40, height // 2 + 8), fill=(200, 160, 40))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class _MapClient:
    def __init__(self, mapping: dict[str, tuple[int, bytes]]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    def get(self, url, *, params=None, headers=None):
        self.calls.append(url)
        status, content = self.mapping[url]
        return HttpResponse(status, content)


class _Provider:
    name = "fake"

    def __init__(self, rows: list[Candidate]) -> None:
        self.rows = rows
        self.searches = 0

    def enabled(self) -> bool:
        return True

    def search(self, query, client, *, limit=10):
        self.searches += 1
        return list(self.rows)[:limit]


def _candidate(**overrides) -> Candidate:
    data = dict(
        provider="fake",
        title="Baalbek trilithon stones",
        description="Quarry stones at Baalbek",
        author="Ada Lovelace",
        license_raw="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        source_url="https://example.test/baalbek",
        download_url="https://example.test/baalbek.jpg",
        width=1600,
        height=2400,
    )
    data.update(overrides)
    return Candidate(**data)


def test_license_allowlist_rejects_nc_and_nd() -> None:
    assert classify_license("CC BY 4.0") == "cc_by"
    assert classify_license("CC BY-SA 2.0") == "cc_by_sa"
    assert classify_license("CC0 1.0") == "cc0"
    assert classify_license("Public Domain") == "public_domain"
    assert classify_license("No known restrictions on publication.") == "public_domain"
    assert classify_license("Pexels License") == "pexels"
    assert classify_license("Unsplash License") == "unsplash"
    assert classify_license("CC BY-NC 4.0") is None
    assert classify_license("CC BY-ND 4.0") is None
    assert classify_license("CC BY-NC-SA 4.0") is None
    assert classify_license("All rights reserved") is None
    assert classify_license("") is None
    assert license_allowed("CC BY 4.0", {"public_domain", "cc0"}) is None
    assert license_allowed("Public Domain", {"public_domain"}) == "public_domain"


def test_policy_defaults_and_override() -> None:
    assert default_policy("ancient_knowledge") == "free_first"
    assert default_policy("amazing_feed") == "free_first"
    assert default_policy("anna_protocol") == "free_first"
    assert default_policy("annas_garden") == "free_first"
    assert default_policy("wonder_feed") == "free_first"
    assert default_policy("aiwake") == "ai_only"
    assert default_policy("endless_summer_paradise") == "ai_only"
    assert default_policy("master_mei") == "ai_only"
    assert default_policy("momma_circle") == "ai_only"
    assert resolve_policy("master_mei", {"IMAGE_SOURCE_POLICY": "free_only"}) == "free_only"
    assert resolve_policy(
        "ancient_knowledge",
        {"image_source_policy": "not-a-policy"},
    ) == "free_first"


def test_channel_page_configs_match_defaults() -> None:
    from channels_config.ancient_knowledge.page_config import IMAGE_SOURCE_POLICY as ancient
    from channels_config.anna_protocol.page_config import IMAGE_SOURCE_POLICY as anna
    from channels_config.endless_summer_paradise.page_config import IMAGE_SOURCE_POLICY as esp
    from channels_config.master_mei.page_config import IMAGE_SOURCE_POLICY as mei
    from channels_config.wonder_feed.page_config import IMAGE_SOURCE_POLICY as wonder

    assert ancient == "free_first"
    assert anna == "free_first"
    assert wonder == "free_first"
    assert mei == "ai_only"
    assert esp == "ai_only"


def test_query_prefers_the_named_entity() -> None:
    from production.providers.image.free.query import query_variants

    query = build_query(
        topic="ancient quarry",
        entity="Baalbek",
        sentence="A cinematic photograph of dramatic lighting at the temple",
    )
    assert query == "baalbek"
    assert "cinematic" not in query
    assert "lighting" not in query
    assert "quarry" not in query
    noisy = build_query(
        topic="Voynich Manuscript world's most mysterious book",
        sentence="polish american researchers debate the pages",
    )
    assert noisy == "voynich manuscript"
    assert "polish" not in noisy
    assert "mysterious" not in noisy
    variants = query_variants(
        entity="Easter Island moai",
        sentence="world's most mysterious statues polish american",
    )
    assert variants == ["easter island moai", "easter island"]
    assert query_variants(entity="Oak Island Nova Scotia") == ["oak island nova scotia"]


def test_rank_prefers_title_match_and_resolution() -> None:
    weak = _candidate(title="Unrelated portrait", description="", width=1200, height=1800)
    strong = _candidate(title="Baalbek trilithon", width=2400, height=3600)
    ranked = rank_candidates([weak, strong], "baalbek trilithon")
    assert ranked[0] is strong


def test_orientation_and_size_gate() -> None:
    portrait = Image.new("RGB", (1200, 2000), (10, 10, 10))
    kept, cropped = prepare_portrait(portrait, min_short_side=1000, allow_smart_crop=True)
    assert kept is not None and cropped is False
    landscape = Image.new("RGB", (2000, 1200), (10, 10, 10))
    cropped_img, did_crop = prepare_portrait(landscape, min_short_side=1000, allow_smart_crop=True)
    assert did_crop is True
    assert cropped_img is not None
    assert cropped_img.size[1] > cropped_img.size[0]
    assert min(cropped_img.size) >= 1000
    too_short = Image.new("RGB", (2000, 800), (10, 10, 10))
    assert prepare_portrait(too_short, min_short_side=1000, allow_smart_crop=True) is None
    tiny = Image.new("RGB", (800, 1400), (10, 10, 10))
    assert prepare_portrait(tiny, min_short_side=1000, allow_smart_crop=True) is None


def test_watermark_heuristic_rejects_corner_logo() -> None:
    clean = Image.open(io.BytesIO(_jpeg(1600, 2200)))
    marked = Image.open(io.BytesIO(_jpeg(1600, 2200, watermark=True)))
    assert image_has_watermark(clean) is False
    assert image_has_watermark(marked) is True


def test_mocked_provider_payloads() -> None:
    wiki = parse_wikimedia(
        {
            "query": {
                "pages": {
                    "1": {
                        "title": "File:Baalbek.jpg",
                        "imageinfo": [
                            {
                                "url": "https://upload.example/baalbek.jpg",
                                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Baalbek.jpg",
                                "width": 2000,
                                "height": 3000,
                                "extmetadata": {
                                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
                                    "Artist": {"value": "<a href='#'>Ada</a>"},
                                    "ObjectName": {"value": "Baalbek stones"},
                                    "ImageDescription": {"value": "Trilithon"},
                                },
                            }
                        ],
                    }
                }
            }
        }
    )
    assert wiki[0].license_raw == "CC BY-SA 4.0"
    assert wiki[0].author == "Ada"
    assert wiki[0].download_url.endswith("baalbek.jpg")

    opened = parse_openverse(
        {
            "results": [
                {
                    "title": "Temple",
                    "url": "https://images.example/t.jpg",
                    "foreign_landing_url": "https://openverse.example/t",
                    "license": "by-nc",
                    "license_version": "4.0",
                    "creator": "Sam",
                    "width": 1000,
                    "height": 1500,
                }
            ]
        }
    )
    assert license_allowed(opened[0].license_raw) is None

    nasa = parse_nasa(
        {
            "collection": {
                "items": [
                    {
                        "href": "https://images-api.nasa.gov/asset/ABC",
                        "data": [
                            {
                                "nasa_id": "ABC",
                                "title": "Earth",
                                "description": "Orbit",
                                "photographer": "NASA",
                            }
                        ],
                        "links": [{"href": "https://images.example/thumb.jpg"}],
                    }
                ]
            }
        }
    )
    assert nasa[0].license_raw == "Public Domain"
    assert nasa[0].extra["nasa_id"] == "ABC"

    loc = parse_loc(
        {
            "results": [
                {
                    "title": "Stone wall",
                    "rights_advisory": "No known restrictions on publication.",
                    "image": {"full": "https://tile.loc.gov/full.jpg", "thumb": "https://tile.loc.gov/t.jpg"},
                    "links": {"item": "https://www.loc.gov/item/1/"},
                }
            ]
        }
    )
    assert classify_license(loc[0].license_raw) == "public_domain"

    smith = parse_smithsonian(
        {
            "response": {
                "rows": [
                    {
                        "content": {
                            "descriptiveNonRepeating": {
                                "title": {"content": "Mask"},
                                "record_link": "https://si.edu/object/1",
                                "online_media": {
                                    "media": [{"type": "Images", "content": "https://ids.si.edu/1.jpg"}]
                                },
                            }
                        }
                    }
                ]
            }
        }
    )
    assert smith[0].license_raw == "CC0"
    assert smith[0].download_url.endswith("1.jpg")

    pexels = parse_pexels(
        {
            "photos": [
                {
                    "alt": "Herb garden",
                    "photographer": "Lee",
                    "url": "https://www.pexels.com/photo/1/",
                    "width": 1200,
                    "height": 1800,
                    "src": {"original": "https://images.pexels.com/1.jpg"},
                }
            ]
        }
    )
    assert pexels[0].license_raw == "Pexels License"
    unsplash = parse_unsplash(
        {
            "results": [
                {
                    "alt_description": "Moss",
                    "width": 2000,
                    "height": 3000,
                    "urls": {"raw": "https://images.unsplash.com/1"},
                    "user": {"name": "Jo"},
                    "links": {"html": "https://unsplash.com/photos/1"},
                }
            ]
        }
    )
    assert unsplash[0].author == "Jo"


def test_keyed_providers_skip_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("UNSPLASH_ACCESS_KEY", raising=False)
    monkeypatch.delenv("SMITHSONIAN_API_KEY", raising=False)
    names = {provider.name for provider in default_providers()}
    assert "pexels" not in names
    assert "unsplash" not in names
    assert "smithsonian" not in names
    assert "wikimedia" in names
    monkeypatch.setenv("PEXELS_API_KEY", "pex")
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "un")
    monkeypatch.setenv("SMITHSONIAN_API_KEY", "si")
    assert PexelsProvider().enabled()
    assert UnsplashProvider().enabled()
    assert SmithsonianProvider().enabled()


def test_sourcer_filters_license_watermark_and_size(tmp_path: Path) -> None:
    good_url = "https://cdn.example/good.jpg"
    nc_url = "https://cdn.example/nc.jpg"
    marked_url = "https://cdn.example/mark.jpg"
    small_url = "https://cdn.example/small.jpg"
    rows = [
        _candidate(title="Stock Baalbek", download_url=marked_url, source_url="https://www.shutterstock.com/1"),
        _candidate(title="Baalbek NC", license_raw="CC BY-NC 4.0", download_url=nc_url),
        _candidate(title="Baalbek tiny", download_url=small_url, width=640, height=480),
        _candidate(title="Baalbek trilithon", download_url=good_url),
    ]
    client = _MapClient(
        {
            good_url: (200, _jpeg(1600, 2400)),
            nc_url: (200, _jpeg(1600, 2400, color=(1, 2, 3))),
            marked_url: (200, _jpeg(1600, 2400, watermark=True)),
            small_url: (200, _jpeg(640, 480)),
        }
    )
    provider = _Provider(rows)
    dest = tmp_path / "picked.jpg"
    decision = resolve_image_source(
        channel="ancient_knowledge",
        dest=dest,
        topic="Baalbek trilithon",
        policy="free_first",
        providers=[provider],
        client=client,
        cache_dir=tmp_path / "cache",
    )
    assert decision.reason == "free_image"
    assert decision.hit is not None
    assert decision.hit.raw_publish_allowed is False
    assert client.calls == [good_url]
    sidecar = json.loads(decision.hit.sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["license"] == "cc_by"
    assert sidecar["author"] == "Ada Lovelace"
    assert sidecar["source_url"] == "https://example.test/baalbek"
    assert sidecar["raw_publish_allowed"] is False
    assert "Ada Lovelace" in decision.hit.credit_line
    with pytest.raises(RuntimeError):
        decision.hit.as_publishable()
    width, height = Image.open(dest).size
    assert height > width
    assert min(width, height) >= 1000


def test_sourcer_smart_crops_landscape(tmp_path: Path) -> None:
    url = "https://cdn.example/wide.jpg"
    provider = _Provider([_candidate(download_url=url, width=2400, height=1600, title="Baalbek")])
    client = _MapClient({url: (200, _jpeg(2400, 1600))})
    decision = resolve_image_source(
        channel="ancient_knowledge",
        dest=tmp_path / "wide.jpg",
        topic="Baalbek",
        policy="free_only",
        providers=[provider],
        client=client,
        cache_dir=tmp_path / "cache",
    )
    assert decision.hit is not None
    assert decision.hit.smart_cropped is True
    image = Image.open(decision.hit.path)
    assert image.size[1] > image.size[0]


def test_query_cache_skips_the_second_search(tmp_path: Path) -> None:
    url = "https://cdn.example/good.jpg"
    provider = _Provider([_candidate(download_url=url)])
    client = _MapClient({url: (200, _jpeg(1400, 2000))})
    cache = tmp_path / "cache"
    for index in range(2):
        resolve_image_source(
            channel="wonder_feed",
            dest=tmp_path / f"out{index}.jpg",
            topic="Baalbek trilithon",
            policy="free_first",
            providers=[provider],
            client=client,
            cache_dir=cache,
        )
    assert provider.searches == 1
    assert client.calls == [url, url]


def test_ai_only_does_not_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom():
        raise AssertionError("providers should not load")

    monkeypatch.setattr("agents.media.free_images.sourcer.default_providers", _boom)
    assert acquire_free_file(tmp_path / "a.jpg", channel="master_mei", topic="temple") is False
    decision = resolve_image_source(
        channel="aiwake",
        dest=tmp_path / "b.jpg",
        topic="consciousness",
        policy="ai_only",
        providers=[_Provider([_candidate()])],
        client=_MapClient({}),
        cache_dir=tmp_path / "cache",
    )
    assert decision.reason == "ai_only"
    assert decision.hit is None


def test_wrap_falls_back_to_flux_and_free_only_does_not() -> None:
    calls: list[str] = []

    def generate(prompt, output_stem=None, output_directory=None, **kwargs):
        calls.append(prompt)
        return Path(output_directory or ".") / f"{output_stem}.png"

    original = generate
    untouched = wrap_image_generate(
        generate,
        channel="master_mei",
        page_cfg={"IMAGE_SOURCE_POLICY": "ai_only"},
    )
    assert untouched is original

    def hit(*_args, **_kwargs):
        return SourceDecision(
            policy="free_first",
            query="baalbek",
            reason="free_image",
            hit=FreeImageHit(
                path=Path("stones.jpg"),
                sidecar_path=Path("stones.jpg.attribution.json"),
                attribution=None,  # type: ignore[arg-type]
                credit_line="Photo: stones",
                width=1000,
                height=1500,
                score=1.0,
                provider="wikimedia",
            ),
        )

    wrapped = wrap_image_generate(
        generate,
        channel="ancient_knowledge",
        page_cfg={"IMAGE_SOURCE_POLICY": "free_first", "REEL_FREE_IMAGES": "1"},
        producer="reel_image",
    )
    # Patch the name the wrapper closes over.
    import production.providers.image.free.integrate as integrate

    previous = integrate.resolve_image_source
    integrate.resolve_image_source = hit
    try:
        path = wrapped("draw a temple", output_stem="act", output_directory=".")
        assert path == Path("stones.jpg")
        assert calls == []
        integrate.resolve_image_source = lambda **_kwargs: SourceDecision(
            policy="free_first", query="q", reason="none_passed"
        )
        fallback = wrapped(
            "draw a temple", output_stem="act", output_directory="/tmp"
        )
        assert fallback == Path("/tmp/act.png")
        assert calls == ["draw a temple"]
        only = wrap_image_generate(
            generate,
            channel="ancient_knowledge",
            page_cfg={"IMAGE_SOURCE_POLICY": "free_only"},
            producer="facebook_card",
        )
        with pytest.raises(FreeImageUnavailable):
            only("draw a temple", output_stem="card", output_directory="/tmp")
    finally:
        integrate.resolve_image_source = previous
    assert calls == ["draw a temple"]


def test_credit_line_is_reusable() -> None:
    line = credit_line(
        title="Baalbek stones",
        author="Ada",
        license_name="cc_by",
        source_url="https://example.test/a",
    )
    assert line == "Ada, CC BY"
    assert "Baalbek" not in line
    assert "https://" not in line
    assert append_credit("A short caption.", line).endswith(line)
    long_caption = "word " * 200
    fitted = append_credit(long_caption, line, limit=120)
    assert line in fitted
    assert len(fitted) <= 120


def test_pin_template_composites_a_free_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_load(record, work_dir, **_kwargs):
        record["image_credit"] = 'Photo: "Stone" by Ada, CC BY — https://example.test/a'
        return Image.new("RGB", (1400, 2000), (12, 90, 40))

    monkeypatch.setattr("agents.media.free_images.integrate.load_card_source", fake_load)
    transformer = PinTransformer(outputs_dir=tmp_path / "out", method="cover")
    raw = transformer.get_pin_bytes(
        {
            "channel_id": "ancient_knowledge",
            "local_image_path": "",
            "topic": "Baalbek",
            "visual_hook": "STONES",
            "subject_slug": "baalbek",
        }
    )
    assert raw
    card = Image.open(io.BytesIO(raw))
    assert card.size == (1000, 1500)
    plain = io.BytesIO()
    Image.new("RGB", (1400, 2000), (12, 90, 40)).save(plain, format="JPEG")
    assert raw != plain.getvalue()


def test_owned_pin_photo_is_not_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("free search should not run when a local photo exists")

    monkeypatch.setattr("agents.media.free_images.integrate.load_card_source", _forbidden)
    source = tmp_path / "owned.jpg"
    Image.new("RGB", (1200, 1800), (80, 20, 20)).save(source, format="JPEG")
    transformer = PinTransformer(outputs_dir=tmp_path / "out", method="cover")
    raw = transformer.get_pin_bytes(
        {
            "channel_id": "ancient_knowledge",
            "local_image_path": str(source),
            "visual_hook": "OWNED",
            "topic": "Baalbek",
        }
    )
    assert raw
    assert Image.open(io.BytesIO(raw)).size == (1000, 1500)


def test_vision_check_can_reject(tmp_path: Path) -> None:
    url = "https://cdn.example/logo.jpg"
    provider = _Provider([_candidate(download_url=url, title="Baalbek")])
    client = _MapClient({url: (200, _jpeg(1600, 2200))})
    decision = resolve_image_source(
        channel="anna_protocol",
        dest=tmp_path / "logo.jpg",
        topic="Baalbek",
        policy="free_only",
        providers=[provider],
        client=client,
        cache_dir=tmp_path / "cache",
        vision_check=lambda _image: True,
    )
    assert decision.hit is None
    assert decision.reason == "none_passed"


def test_load_card_source_records_credit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "outputs"))
    url = "https://cdn.example/herb.jpg"
    provider = _Provider(
        [_candidate(title="Herb garden", download_url=url, source_url="https://example.test/herb")]
    )

    def _providers():
        return [provider]

    monkeypatch.setattr("agents.media.free_images.sourcer.default_providers", _providers)
    monkeypatch.setattr(
        "agents.media.free_images.sourcer.RequestsClient",
        lambda: _MapClient({url: (200, _jpeg(1500, 2200))}),
    )
    record = {"channel_id": "anna_protocol", "topic": "Herb garden", "subject_slug": "herb"}
    image = load_card_source(record, tmp_path)
    assert image is not None
    assert image.size[1] > image.size[0]
    assert record["image_credit"] == "Ada Lovelace, CC BY"
    assert "Herb garden" not in record["image_credit"]
    assert credit_for_image(tmp_path / "herb_free_source.jpg") == record["image_credit"]


def test_short_query_broadens_when_the_first_search_is_empty(tmp_path: Path) -> None:
    url = "https://cdn.example/moai.jpg"
    seen: list[str] = []

    class _Selective:
        name = "fake"

        def enabled(self) -> bool:
            return True

        def search(self, query, client, *, limit=10):
            del client, limit
            seen.append(query)
            if query == "easter island":
                return [_candidate(title="Moai", download_url=url)]
            return []

    client = _MapClient({url: (200, _jpeg(1400, 2000))})
    decision = resolve_image_source(
        channel="ancient_knowledge",
        dest=tmp_path / "moai.jpg",
        entity="Easter Island moai",
        sentence="world's most mysterious statues polish american",
        policy="free_first",
        providers=[_Selective()],
        client=client,
        cache_dir=tmp_path / "cache",
    )
    assert seen == ["easter island moai", "easter island"]
    assert decision.reason == "free_image"
    assert decision.query == "easter island"


def test_missing_stock_keys_are_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    from production.providers.image.free import providers as providers_mod

    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("UNSPLASH_ACCESS_KEY", raising=False)
    monkeypatch.delenv("SMITHSONIAN_API_KEY", raising=False)
    providers_mod._MISSING_KEY_LOGGED.clear()
    with caplog.at_level(logging.INFO, logger=providers_mod.logger.name):
        default_providers()
        default_providers()
        assert PexelsProvider().search("moai", None) == []
        assert UnsplashProvider().search("moai", None) == []
        assert SmithsonianProvider().search("moai", None) == []
    logs = [record.message for record in caplog.records if "API key is not set" in record.message]
    assert logs.count("Skipping image provider pexels — API key is not set.") == 1
    assert logs.count("Skipping image provider unsplash — API key is not set.") == 1
    assert logs.count("Skipping image provider smithsonian — API key is not set.") == 1


def test_free_image_cache_stays_out_of_drive_outputs_on_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from production.providers.image.free.sourcer import _cache_dir, free_image_cache_dir

    monkeypatch.delenv("FREE_IMAGE_CACHE_DIR", raising=False)
    monkeypatch.delenv("PIN_DRY_RUN", raising=False)
    drive = tmp_path / "outputs" / "ancient_knowledge"
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path / "outputs"))
    live = _cache_dir("ancient_knowledge", None)
    assert "outputs" in live.parts
    dry = free_image_cache_dir("ancient_knowledge", dry_run=True)
    assert dry is not None
    assert "outputs" not in dry.parts
    monkeypatch.setenv("FREE_IMAGE_CACHE_DIR", str(tmp_path / "local-cache"))
    chosen = free_image_cache_dir("ancient_knowledge", dry_run=True)
    assert chosen == tmp_path / "local-cache"
    assert drive.is_dir() is False or "local-cache" not in drive.parts


def test_relevance_rejects_the_wrong_place_and_book_scans() -> None:
    from production.providers.image.free.relevance import relevance_verdict, select_relevant

    capitol = _candidate(
        title="The United States Capitol",
        description="Illustration from a 1917 book of the capitol building in Washington",
    )
    assert relevance_verdict(
        capitol,
        subject="Paititi Peru",
        required_terms=("paititi",),
        reject_terms=("capitol", "washington"),
    ) == "reject"
    carolina = _candidate(
        title="Oak Island, North Carolina",
        description="A beach town in North Carolina",
    )
    assert relevance_verdict(
        carolina,
        subject="Oak Island Nova Scotia",
        required_terms=("nova scotia",),
        reject_terms=("north carolina",),
    ) == "reject"
    scotia = _candidate(
        title="Oak Island, Nova Scotia",
        description="The money pit on Oak Island, Nova Scotia",
    )
    assert relevance_verdict(
        scotia,
        subject="Oak Island Nova Scotia",
        required_terms=("nova scotia",),
        reject_terms=("north carolina",),
    ) == "accept"
    engraving = _candidate(
        title="Paititi",
        description="An engraving of a stone city",
    )
    assert relevance_verdict(
        engraving,
        subject="Paititi",
        required_terms=("paititi",),
    ) == "reject"
    page = _candidate(
        title="Voynich manuscript",
        description="A scanned page of botanical drawings",
    )
    assert relevance_verdict(
        page,
        subject="Voynich manuscript",
        required_terms=("voynich",),
        allow_illustration=True,
    ) == "accept"

    calls = {"n": 0}

    def judge(subject, candidate):
        del subject, candidate
        calls["n"] += 1
        return True

    ambiguous = _candidate(
        title="Oak Island lighthouse",
        description="A wooden lighthouse on the shore",
    )
    assert relevance_verdict(
        ambiguous,
        subject="Oak Island Nova Scotia",
        required_terms=("nova scotia",),
    ) == "ambiguous"
    judged = select_relevant(
        [ambiguous],
        subject="Oak Island Nova Scotia",
        required_terms=("nova scotia",),
        judge=judge,
    )
    assert calls["n"] == 1
    assert judged == [ambiguous]
    calls["n"] = 0
    kept = select_relevant(
        [scotia, carolina, capitol],
        subject="Oak Island Nova Scotia",
        required_terms=("nova scotia",),
        reject_terms=("north carolina", "capitol"),
        judge=judge,
    )
    assert calls["n"] == 0
    assert kept == [scotia]

    frog = _candidate(
        title="Scinax granulatus (Hensel, 1867) at Reserva Natural Paititi",
        description="A frog species photographed at the reserve",
    )
    paititi_terms = {
        "subject": "Paititi",
        "required_terms": ("paititi",),
        "required_any": ("ruins", "inca", "stone", "city", "jungle"),
        "reject_terms": ("species", "frog", "animal", "bird", "insect", "plant"),
    }
    assert relevance_verdict(frog, **paititi_terms) == "reject"
    binomial = _candidate(
        title="Scinax granulatus at Reserva Natural Paititi",
        description="Reserva Natural Paititi",
    )
    assert relevance_verdict(
        binomial,
        subject="Paititi",
        required_terms=("paititi",),
    ) == "reject"
    ruins = _candidate(
        title="Stone ruins in the jungle near Paititi",
        description="Inca stone city under the canopy",
    )
    assert relevance_verdict(ruins, **paititi_terms) == "accept"
    calls["n"] = 0
    rejected = select_relevant([frog, binomial], judge=judge, **paititi_terms)
    assert calls["n"] == 0
    assert rejected == []
    living = relevance_verdict(
        binomial,
        subject="Scinax granulatus",
        allow_biological=True,
    )
    assert living == "accept"


def test_unrelated_hit_is_not_downloaded(tmp_path: Path) -> None:
    url = "https://cdn.example/capitol.jpg"
    provider = _Provider(
        [
            _candidate(
                title="Capitol building",
                description="1917 book illustration of the capitol in Washington",
                download_url=url,
            )
        ]
    )
    client = _MapClient({url: (200, _jpeg(1600, 2200))})
    decision = resolve_image_source(
        channel="ancient_knowledge",
        dest=tmp_path / "paititi.jpg",
        entity="Paititi Peru",
        required_terms=("paititi",),
        reject_terms=("capitol", "washington"),
        policy="free_only",
        providers=[provider],
        client=client,
        cache_dir=tmp_path / "cache",
        relevance_judge=False,
    )
    assert decision.hit is None
    assert decision.reason == "no_hits"
    assert client.calls == []

    frog_url = "https://cdn.example/frog.jpg"
    frog_provider = _Provider(
        [
            _candidate(
                title="Scinax granulatus (Hensel, 1867) at Reserva Natural Paititi",
                description="A frog species at the reserve",
                download_url=frog_url,
            )
        ]
    )
    frog_client = _MapClient({frog_url: (200, _jpeg(1600, 2200))})
    frog_decision = resolve_image_source(
        channel="ancient_knowledge",
        dest=tmp_path / "paititi-frog.jpg",
        entity="Paititi",
        required_terms=("paititi",),
        required_any=("ruins", "inca", "stone", "city", "jungle"),
        reject_terms=("species", "frog", "animal", "bird", "insect", "plant"),
        policy="free_only",
        providers=[frog_provider],
        client=frog_client,
        cache_dir=tmp_path / "frog-cache",
        relevance_judge=False,
    )
    assert frog_decision.hit is None
    assert frog_decision.reason == "no_hits"
    assert frog_client.calls == []


def test_credit_line_drops_html_and_extra_languages() -> None:
    huge = (
        "English: Voynich Manuscript / Français: Manuscrit de Voynich / "
        "Deutsch: Voynich-Manuskript " * 8
    )
    line = credit_line(
        title=f"<b>{huge}</b>",
        author="English: Wilfrid Voynich / Français: Wilfrid Voynich",
        license_name="cc_by_sa",
    )
    assert line == "Wilfrid Voynich, CC BY-SA"
    assert "<" not in line
    assert "Français" not in line
    assert len(line) <= 80
    unknown = credit_line(title=huge, author="Unknown author Unknown author", license_name="cc_by")
    assert unknown == "Unknown author, CC BY"
    assert unknown.count("Unknown author") == 1
    assert credit_line(title="Stones", author="", license_name="public_domain") == "Public Domain"
    yale = credit_line(
        title="Yale University Library : Home page – Source – Image",
        author="Yale University Library : Home page – Source – Image",
        license_name="public_domain",
    )
    assert yale == "Yale University Library, Public Domain"
    assert "Home page" not in yale
    assert "Source" not in yale
    from production.providers.image.free.providers import clean_stored_title

    stored = clean_stored_title(huge)
    assert stored == "Voynich Manuscript"
    assert "Français" not in stored
    assert "<" not in stored
    assert len(stored) <= 80
    labeled = clean_stored_title("Yale University Library : Home page – Source – Image")
    assert labeled == "Yale University Library"
