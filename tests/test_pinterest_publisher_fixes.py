# -*- coding: utf-8 -*-
"""Article links, Claude pin copy, lazy inventory, and dry-run side effects."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from agents.posting.pinterest_engine.articles import (
    Article,
    attach_article_url,
    fetch_wordpress_catalog,
    match_catalog_article,
)
from agents.posting.pinterest_engine.image_transformer import PinTransformer, _hook_lines
from agents.posting.pinterest_engine.inventory import (
    _generate_ai,
    reset_ai_circuit,
)
from agents.posting.pinterest_engine.publisher import _pin_link
from omni.pinterest_drip import publish_account
from tests.test_pinterest_vm import (
    FakeS3,
    _ancient,
    _library_record,
    _photo,
    _prepare_channel,
    _Response,
)

_SITE = "https://ancientknowledge.eokviral.com"


def _catalog() -> list[Article]:
    rows = [
        ("oak-island", "Oak Island"),
        ("giants", "Giants"),
        ("tartaria", "Tartaria and the Mudflood"),
        ("underwater-cities", "Ancient Underwater Cities"),
        ("easter-island-moai", "Easter Island Moai"),
    ]
    return [
        Article(url=f"{_SITE}/{slug}", topic=title, body=title, slug=slug)
        for slug, title in rows
    ]


def test_topics_match_site_slugs_and_titles() -> None:
    catalog = _catalog()
    assert match_catalog_article("Oak Island", catalog).slug == "oak-island"
    assert match_catalog_article("The Money Pit at Oak Island", catalog).slug == "oak-island"
    assert match_catalog_article("Giants", catalog).slug == "giants"
    assert match_catalog_article("Tartaria/Mudflood", catalog).slug == "tartaria"
    assert match_catalog_article("Ancient Underwater Cities", catalog).slug == "underwater-cities"
    assert match_catalog_article("Easter Island moai", catalog).slug == "easter-island-moai"
    long_oak = "Oak Island and the 200-year mystery that still has no answer"
    assert match_catalog_article(long_oak, catalog).slug == "oak-island"
    giants_sentence = "The giants who left bones that do not fit the graves"
    assert match_catalog_article(giants_sentence, catalog).slug == "giants"
    tartaria_sentence = "Tartaria and the mudflood that buried an older map"
    assert match_catalog_article(tartaria_sentence, catalog).slug == "tartaria"
    underwater = "Ancient underwater cities and the streets that are still down there"
    assert match_catalog_article(underwater, catalog).slug == "underwater-cities"
    moai = "Easter Island moai and the quarry that never closed"
    assert match_catalog_article(moai, catalog).slug == "easter-island-moai"
    longer = list(catalog) + [
        Article(url=f"{_SITE}/easter-island/", topic="Easter Island", body="", slug="easter-island")
    ]
    assert match_catalog_article(moai, longer).slug == "easter-island-moai"
    assert match_catalog_article("giants tomb sardinia", catalog) is None


def test_yaml_override_is_the_fallback_when_the_catalog_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = {"subject_slug": "baalbek-trilithon", "topic": "Stones in the quarry"}
    linked = attach_article_url(
        entry,
        [],
        "ancient_knowledge",
        site_url=_SITE,
    )
    assert linked is None
    assert entry["target_url"] == f"{_SITE}/baalbek/"

    skipped = {"subject_slug": "no-such-place", "topic": "Garden roses"}
    assert attach_article_url(skipped, [], "ancient_knowledge", site_url=_SITE) == "no-link"
    assert skipped["target_url"] == ""
    # An earlier test can leave CHANNEL_ID on another page, whose pack carries
    # a sales URL. The row names its channel so _pin_link stays on that page.
    skipped["channel_id"] = "ancient_knowledge"
    monkeypatch.setenv("PINTEREST_TARGET_URL", "https://sales.example/buy")
    assert _pin_link(skipped) == ""


def test_catalog_paginates_and_caches_on_the_outputs_volume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[int] = []

    def fake_get(url, params=None, timeout=None, verify=None):
        del url, timeout, verify
        page = int((params or {}).get("page") or 1)
        assert (params or {}).get("per_page") == 100
        calls.append(page)
        if page == 1:
            posts = [
                {
                    "slug": "oak-island",
                    "link": f"{_SITE}/oak-island",
                    "title": {"rendered": "Oak Island"},
                    "excerpt": {"rendered": "The pit."},
                    "content": {"rendered": ""},
                }
            ]
            return _Response(200, posts, headers={"X-WP-TotalPages": "2"})
        posts = [
            {
                "slug": "giants",
                "link": f"{_SITE}/giants",
                "title": {"rendered": "Giants"},
                "excerpt": {"rendered": "Bones."},
                "content": {"rendered": ""},
            }
        ]
        return _Response(200, posts, headers={"X-WP-TotalPages": "2"})

    monkeypatch.setattr(requests, "get", fake_get)
    first = fetch_wordpress_catalog(
        _SITE,
        verify=False,
        account_id="ancient_knowledge",
        outputs_dir=tmp_path,
        ttl_seconds=3600,
    )
    second = fetch_wordpress_catalog(
        _SITE,
        verify=False,
        account_id="ancient_knowledge",
        outputs_dir=tmp_path,
        ttl_seconds=3600,
    )
    assert [article.slug for article in first] == ["oak-island", "giants"]
    assert [article.slug for article in second] == ["oak-island", "giants"]
    assert calls == [1, 2]
    cache = tmp_path / "article_cache" / "ancient_knowledge.json"
    assert cache.is_file()

    def boom(*_args, **_kwargs):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(requests, "get", boom)
    stale = fetch_wordpress_catalog(
        _SITE,
        verify=False,
        account_id="ancient_knowledge",
        outputs_dir=tmp_path,
        ttl_seconds=0,
    )
    assert [article.slug for article in stale] == ["oak-island", "giants"]


def test_claude_reads_text_blocks_and_opens_the_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    from agents.posting.pinterest_engine import config as cfg

    cfg.AI_SYSTEM_PROMPT = "Write a short pin."
    cfg.TARGET_URL = ""
    reset_ai_circuit()
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)

    class _Block:
        def __init__(self, kind: str, text: str = "") -> None:
            self.type = kind
            if kind == "text":
                self.text = text

    class _Resp:
        def __init__(self, blocks: list) -> None:
            self.content = blocks

    class _Client:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                payload = json.dumps(
                    {
                        "pinterest_title": "Oak Island Money Pit",
                        "pinterest_caption": "The pit is still open.",
                        "visual_hook": "Who dug the Oak Island pit",
                    }
                )
                return _Resp([_Block("thinking"), _Block("text", payload)])
            return _Resp([_Block("thinking")])

    client = _Client()
    client.messages = client  # type: ignore[attr-defined]
    good = _generate_ai("Oak Island", "The pit.", "", client, "claude-sonnet-5-5", 0)
    assert good["pinterest_title"] == "Oak Island Money Pit"
    assert good["visual_hook"] == "Who dug the Oak Island pit"
    assert "thinking" not in client.calls[0]
    assert client.calls[0]["max_tokens"] >= 2048

    for _ in range(3):
        assert _generate_ai("Giants", "Bones.", "", client, "claude-sonnet-5-5", 0) == {}
    before = len(client.calls)
    assert _generate_ai("Tartaria", "Mud.", "", client, "claude-sonnet-5-5", 0) == {}
    assert len(client.calls) == before
    reset_ai_circuit()


def test_claude_model_is_opt_in_and_never_retired(monkeypatch: pytest.MonkeyPatch) -> None:
    from agents.posting.pinterest_engine.config import get_best_claude_model

    class _Model:
        def __init__(self, mid: str) -> None:
            self.id = mid

    class _Listing:
        data = [
            _Model("claude-3-5-sonnet-latest"),
            _Model("claude-3-opus-20240229"),
            _Model("claude-sonnet-5-5"),
        ]

    listed = {"n": 0}

    class _Models:
        def list(self):
            listed["n"] += 1
            return _Listing()

    class _Client:
        models = _Models()

    for name in ("CLAUDE_MODEL", "USE_CLAUDE", "CLAUDE_POLISH_MODEL", "LOFI_SCRIPT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert get_best_claude_model(_Client()) == ""
    assert get_best_claude_model(None) == ""
    assert listed["n"] == 0

    monkeypatch.setenv("USE_CLAUDE", "1")
    assert get_best_claude_model(_Client()) == "claude-sonnet-4-5"
    assert listed["n"] == 0

    monkeypatch.setenv("CLAUDE_MODEL", "claude-3-5-sonnet-latest")
    chosen = get_best_claude_model(_Client())
    assert chosen == "claude-sonnet-4-5"
    assert "latest" not in chosen
    assert "claude-3" not in chosen

    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-4-5")
    assert get_best_claude_model(_Client()) == "claude-sonnet-4-5"

    monkeypatch.setenv("USE_CLAUDE", "0")
    assert get_best_claude_model(_Client()) == ""
    assert listed["n"] == 0


def test_empty_inventory_builds_only_the_next_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_channel(monkeypatch, tmp_path)
    library = tmp_path / "ancient_knowledge" / "library"
    images = library / "images"
    images.mkdir(parents=True)
    photo = images / "shared.jpg"
    photo.write_bytes(_photo())
    topics = [
        ("oak-island", "Oak Island"),
        ("giants", "Giants"),
        ("tartaria", "Tartaria"),
        ("underwater-cities", "Ancient Underwater Cities"),
        ("easter-island-moai", "Easter Island moai"),
        ("extra-one", "Extra One"),
        ("extra-two", "Extra Two"),
        ("extra-three", "Extra Three"),
    ]
    objects: dict[str, bytes] = {}
    for slug, topic in topics:
        key = f"ancient_knowledge/library/post_{slug}.json"
        objects[key] = _library_record(slug, topic, "images/shared.jpg")
        (library / f"post_{slug}.json").write_bytes(objects[key])
    (library / "images").mkdir(exist_ok=True)
    client = FakeS3({})
    catalog = [
        Article(url=f"{_SITE}/{slug}", topic=topic, body=topic, slug=slug)
        for slug, topic in topics
    ]
    stats = publish_account(_ancient(), pins=1, dry_run=True, client=client, catalog=catalog)
    # pins requested plus two spare, not the whole library.
    assert stats["inventory_built"] == 3
    assert stats["published"] == 1
    assert stats["pins"][0]["target_url"].endswith("/")
    assert not (tmp_path / "ancient_knowledge" / "master_inventory.json").exists()
    assert not (tmp_path / "ancient_knowledge" / "postplanner").exists()
    assert not any(key.startswith("pinterest/ledger/") for key in client.objects)
    assert client.deletes == []


def test_run_picks_linkable_posts_ahead_of_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """pins=5 must publish five articles, not the first five library files."""
    _prepare_channel(monkeypatch, tmp_path)
    library = tmp_path / "ancient_knowledge" / "library"
    images = library / "images"
    images.mkdir(parents=True)
    (images / "shared.jpg").write_bytes(_photo())
    linked = [
        ("oak-island", "Oak Island and the 200-year mystery that still has no answer"),
        ("giants", "The giants who left bones that do not fit the graves"),
        ("tartaria", "Tartaria and the mudflood that buried an older map"),
        ("underwater-cities", "Ancient underwater cities and the streets still down there"),
        ("easter-island-moai", "Easter Island moai and the quarry that never closed"),
    ]
    unlinked = [
        ("aaa-one", "Garden roses"),
        ("aaa-two", "Kitchen herbs"),
        ("aaa-three", "Window ferns"),
        ("aaa-four", "Clay pots"),
        ("aaa-five", "Linen cloth"),
        ("aaa-six", "River stones"),
    ]
    for slug, topic in unlinked + linked:
        (library / f"post_{slug}.json").write_bytes(
            _library_record(slug, topic, "images/shared.jpg")
        )
    catalog = [
        Article(url=f"{_SITE}/{slug}", topic=slug, body=slug, slug=slug) for slug, _topic in linked
    ]
    stats = publish_account(_ancient(), pins=5, dry_run=True, client=FakeS3({}), catalog=catalog)
    assert stats["published"] == 5
    assert stats["inventory_built"] == 7
    urls = [row["target_url"] for row in stats["pins"]]
    slugs = {url.rstrip("/").rsplit("/", 1)[-1] for url in urls}
    assert slugs == {slug for slug, _topic in linked}
    assert all(url.endswith("/") for url in urls)


def test_pin_without_an_article_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare_channel(monkeypatch, tmp_path)
    monkeypatch.setenv("PINTEREST_TARGET_URL", "https://sales.example/buy")
    library = tmp_path / "ancient_knowledge" / "library"
    library.mkdir(parents=True)
    (library / "post_roses.json").write_bytes(
        _library_record("garden-roses", "Garden roses", "")
    )
    photo = library / "roses.jpg"
    photo.write_bytes(_photo())
    record = json.loads((library / "post_roses.json").read_text(encoding="utf-8"))
    record["local_image_path"] = str(photo)
    (library / "post_roses.json").write_text(json.dumps(record), encoding="utf-8")
    stats = publish_account(_ancient(), pins=1, dry_run=True, client=FakeS3({}), catalog=[])
    assert stats["published"] == 0
    assert stats["skipped"] >= 1
    assert not list((tmp_path / "pinterest" / "dryrun").glob("*.jpg"))


def test_hook_stays_short_and_off_the_upper_focal_band(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw

    from agents.posting.pinterest_engine import config as cfg

    cfg.configure(channel_id="ancient_knowledge", inventory_dir=tmp_path)
    long_hook = "What the records leave out about Easter Island moai"
    assert len(_hook_lines(long_hook)) <= 3
    assert len(" ".join(_hook_lines(long_hook)).split()) <= 8

    source = tmp_path / "focal.jpg"
    image = Image.new("RGB", (900, 1400), (12, 12, 12))
    pixels = image.load()
    for y in range(500):
        for x in range(900):
            pixels[x, y] = ((x * 17 + y * 9) % 255, (x * 5) % 200, (y * 11) % 180)
    image.save(source, format="JPEG", quality=90)
    drawn: list[int] = []
    original = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        label = str(text).upper()
        if "ISLAND" in label or "MOAI" in label or "EASTER" in label:
            drawn.append(int(xy[1]))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    try:
        raw = PinTransformer(outputs_dir=tmp_path / "pins", method="cover").get_pin_bytes(
            {
                "local_image_path": str(source),
                "topic": "Easter Island moai",
                "visual_hook": "Easter Island moai still face inland",
                "subject_slug": "easter-island-moai",
                "variant_index": 0,
            }
        )
    finally:
        monkeypatch.undo()
    assert raw
    assert drawn
    assert min(drawn) > 900


def test_worker_dry_run_one_account_does_not_start_the_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omni.worker import main

    seen: dict = {}

    def drip(account, *, pins=None, dry_run=False):
        seen["id"] = account.account_id
        seen["pins"] = pins
        seen["dry_run"] = dry_run
        return {"published": 0, "skipped": 0, "errors": 0}

    def boom(*_args, **_kwargs):
        raise AssertionError("scheduler must not start")

    monkeypatch.setattr("omni.worker.run_pinterest_drip", drip)
    monkeypatch.setattr("omni.worker.run_scheduler", boom)
    monkeypatch.setattr("omni.worker.run_engine", boom)
    main(["--role", "engine", "--dry-run", "--account", "ancient_knowledge", "--pins", "5"])
    assert seen == {"id": "ancient_knowledge", "pins": 5, "dry_run": True}


def test_compose_sets_docker_output_path() -> None:
    text = Path(__file__).resolve().parents[1].joinpath("docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert text.count("OUTPUT_PATH: /opt/omni-engine/outputs") >= 2
