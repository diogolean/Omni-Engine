# -*- coding: utf-8 -*-
"""Asset-first stills: a judged pool, then shots assigned from that pool."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image


class _Item:
    def __init__(self, key: str, caption: str, kind: str = "site") -> None:
        self.key = key
        self.title = caption
        self.caption = caption
        self.kind = kind
        self.download_url = key
        self.source = "commons"


def _jpeg(path: Path, color: tuple[int, int, int]) -> Path:
    Image.new("RGB", (160, 90), color).save(path, quality=85)
    return path


def _judge_ok(path: Path, caption: str, kind: str) -> dict:
    if "panel" in caption or "label" in caption:
        return {"passed": False, "contains_text": True, "relevance_score": 0.95, "kind": kind}
    return {"passed": True, "contains_text": False, "relevance_score": 0.91, "kind": kind}


def _download(item: _Item, folder: Path) -> Path:
    return _jpeg(folder / f"{item.key}.jpg", (30, 90, 40))


def test_the_pool_keeps_real_photos_and_skips_openverse(tmp_path: Path) -> None:
    from modules.reel_artefact.asset_pool import build_asset_pool

    items = [_Item(f"site{i}", f"limestone enclosure site {i}") for i in range(6)]
    items += [_Item(f"jar{i}", f"clay jar from the museum {i}", "object") for i in range(3)]
    items.append(_Item("panel", "museum label panel", "site"))
    opened = {"n": 0}
    subs = {"n": 0}

    def search(_query: str) -> list[_Item]:
        return items

    def category(name: str) -> list[_Item]:
        if name == "Category:Child":
            return [_Item("child", "walkway roof over the enclosure", "site")]
        return []

    def subcategories(_name: str) -> list[str]:
        subs["n"] += 1
        return ["Category:Child"]

    def openverse(_query: str) -> list[_Item]:
        opened["n"] += 1
        return []

    pool = build_asset_pool(
        queries=["Göbekli"],
        categories=["Category:Göbekli Tepe"],
        dest=tmp_path,
        search=search,
        category=category,
        subcategories=subcategories,
        openverse=openverse,
        download=_download,
        judge=_judge_ok,
        nano=lambda _prompt, _dest: (_ for _ in ()).throw(AssertionError("nano")),
        object_prompts=["clay jar"],
    )
    captions = " ".join(item.caption for item in pool)
    assert subs["n"] == 1
    assert opened["n"] == 0
    assert "museum label" not in captions
    assert "walkway roof" in captions
    assert any(item.kind == "object" for item in pool)
    assert 8 <= len(pool) <= 15


def test_openverse_and_nano_fill_a_short_pool(tmp_path: Path) -> None:
    from modules.reel_artefact.asset_pool import build_asset_pool

    opened = {"n": 0}
    nanos = {"n": 0}

    def search(_query: str) -> list[_Item]:
        return [
            _Item("site", "limestone enclosure", "site"),
            _Item("jar", "clay jar", "object"),
        ]

    def openverse(_query: str) -> list[_Item]:
        opened["n"] += 1
        return [_Item("verse", "museum photograph of the site", "site")]

    def nano(prompt: str, dest: Path) -> Path:
        nanos["n"] += 1
        return _jpeg(dest, (80, 40, 20))

    pool = build_asset_pool(
        queries=["jar"],
        categories=["Category:Jar"],
        dest=tmp_path,
        search=search,
        category=lambda _name: [],
        subcategories=lambda _name: [],
        openverse=openverse,
        download=_download,
        judge=_judge_ok,
        nano=nano,
        object_prompts=["the clay jar"],
        object_floor=3,
        nano_max=4,
    )
    assert opened["n"] == 1
    assert nanos["n"] == 2
    assert sum(1 for item in pool if item.kind == "object" and item.source == "nano") == 2
    assert any(item.source == "openverse" for item in pool)


def test_nano_is_not_called_when_object_photos_exist(tmp_path: Path) -> None:
    from modules.reel_artefact.asset_pool import build_asset_pool

    items = [_Item(f"jar{i}", f"clay jar {i}", "object") for i in range(3)]
    items += [_Item(f"site{i}", f"enclosure {i}", "site") for i in range(6)]

    def nano(_prompt: str, _dest: Path) -> Path:
        raise AssertionError("nano")

    pool = build_asset_pool(
        queries=["jar"],
        categories=[],
        dest=tmp_path,
        search=lambda _query: items,
        category=lambda _name: [],
        subcategories=lambda _name: [],
        openverse=lambda _query: [],
        download=_download,
        judge=_judge_ok,
        nano=nano,
    )
    assert len(pool) >= 8
    assert sum(1 for item in pool if item.source == "nano") == 0


def test_flux_is_only_the_sky_and_crops_do_not_repeat(tmp_path: Path) -> None:
    from modules.reel_artefact.asset_pool import PoolAsset, assign_shots

    assets = [
        PoolAsset(str(tmp_path / "jar.jpg"), "clay jar", "object", "commons", "jar"),
        PoolAsset(str(tmp_path / "site.jpg"), "limestone enclosure", "site", "commons", "site"),
    ]
    sentences = [
        "Clouds gather on the horizon.",
        "The clay jar sat in the pit.",
        "The pillars stand in the enclosure.",
        "The clay jar is lifted from the soil.",
        "A storm crosses the landscape.",
        "The sky clears at dusk.",
        "Dawn light fills the atmosphere.",
        "Weather moves over the horizon.",
        "Another storm builds in the sky.",
    ]
    shots = assign_shots(sentences, assets, geography="limestone hills near Şanlıurfa", flux_max=6)
    flux = [item for item in shots if item.mode == "flux"]
    assert len(flux) == 6
    assert all("Şanlıurfa" in item.prompt or "limestone" in item.prompt for item in flux)
    jar = [item for item in shots if "jar" in item.sentence]
    assert jar
    assert all(item.mode != "flux" for item in jar)
    site = next(item for item in shots if "pillars" in item.sentence)
    assert site.mode != "flux"
    assert site.asset_key == "site"
    reused = [item for item in shots if item.asset_key == "jar"]
    assert len(reused) == 2
    assert reused[0].anchor != reused[1].anchor


def test_a_failed_sky_falls_back_to_a_relevant_crop(tmp_path: Path) -> None:
    from modules.reel_artefact.asset_pool import PoolAsset, ShotAssignment, place_assignment

    source = _jpeg(tmp_path / "jar.jpg", (180, 40, 30))
    wide = Image.new("RGB", (400, 120), (20, 20, 20))
    pixels = wide.load()
    for x in range(80):
        for y in range(120):
            pixels[x, y] = (220, 30, 30)
    wide.save(source, quality=90)
    pool = [PoolAsset(str(source), "clay jar in the pit", "object", "commons", "jar")]

    def flux(prompt: str, output_stem: str, output_directory: Path) -> Path:
        path = Path(output_directory) / f"{output_stem}.png"
        Image.new("RGB", (32, 32), (1, 1, 1)).save(path)
        return path

    def judge(_path: Path, _sentence: str) -> dict:
        return {"passed": False, "relevance_score": 0.1}

    result = place_assignment(
        ShotAssignment(sentence="The clay jar sat in the pit.", mode="flux", prompt="sky"),
        tmp_path / "beat.jpg",
        pool,
        flux_generate=flux,
        judge_fn=judge,
    )
    assert result.publishable
    assert result.path is not None and Path(result.path).is_file()
    assert result.attempts[-1]["provider"] == "fallback"
    assert Path(result.path).stat().st_size > 100


def test_the_script_prompt_lists_the_photographs() -> None:
    from core.reel_sequence_engine import build_sequence_script_prompt

    prompt = build_sequence_script_prompt(
        "Göbekli Tepe",
        "history",
        asset_captions=["limestone pillars", "a clay jar"],
    )
    assert "limestone pillars" in prompt
    assert "a clay jar" in prompt
    assert "PHOTOGRAPHS AVAILABLE" in prompt


def test_topic_search_walks_subcategories(monkeypatch) -> None:
    from agents.media.free_images.models import Candidate
    from modules.reel_artefact import fetch

    def cand(title: str) -> Candidate:
        return Candidate(
            provider="commons",
            title=title,
            license_raw="CC0",
            download_url=f"https://example.test/{title}.jpg",
        )

    calls = {"sub": 0, "open": 0, "cat": []}

    def _search(_query: str, _client: object) -> list:
        return [cand(f"s{i}") for i in range(8)]

    def _category(name: str, _client: object) -> list:
        calls["cat"].append(name)
        return [cand(name)]

    def _subs(_name: str, _client: object) -> list[str]:
        calls["sub"] = 1
        return ["Category:Child"]

    def _open(*_args: object, **_kwargs: object) -> list:
        calls["open"] += 1
        return []

    monkeypatch.setattr(fetch, "_commons_search", _search)
    monkeypatch.setattr(fetch, "_commons_category", _category)
    monkeypatch.setattr(fetch, "commons_subcategories", _subs)
    monkeypatch.setattr(fetch, "_openverse_search", _open)
    found = fetch.search_topic_images(["jar"], ["Category:Baghdad Battery"], client=object())
    assert calls["sub"] == 1
    assert "Category:Child" in calls["cat"]
    assert calls["open"] == 0
    assert len(found) >= 8


def test_openverse_runs_when_commons_is_short(monkeypatch) -> None:
    from agents.media.free_images.models import Candidate
    from modules.reel_artefact import fetch

    hit = Candidate(
        provider="openverse",
        title="Jar",
        license_raw="CC0",
        download_url="https://example.test/jar.jpg",
    )
    monkeypatch.setattr(fetch, "_commons_search", lambda *_a, **_k: [])
    monkeypatch.setattr(fetch, "_commons_category", lambda *_a, **_k: [])
    monkeypatch.setattr(fetch, "commons_subcategories", lambda *_a, **_k: [])
    monkeypatch.setattr(fetch, "_openverse_search", lambda *_a, **_k: [hit])
    found = fetch.search_topic_images(["jar"], ["Category:Jar"], client=object())
    assert [item.title for item in found] == ["Jar"]


def test_subcategory_titles_come_from_the_category(monkeypatch) -> None:
    from modules.reel_artefact.fetch import commons_subcategories

    class _Resp:
        ok = True
        status = 200

        def json(self) -> dict:
            return {"query": {"categorymembers": [{"title": "Category:Jars"}]}}

    class _Client:
        def get(self, _url: str, params: dict | None = None, headers: dict | None = None) -> _Resp:
            assert params is not None
            assert params["cmtype"] == "subcat"
            return _Resp()

    assert commons_subcategories("Baghdad Battery", _Client()) == ["Category:Jars"]


def test_military_titles_never_enter_the_pool(tmp_path: Path) -> None:
    from modules.reel_artefact.asset_pool import build_asset_pool
    from modules.reel_artefact.fetch import ancient_title_reject

    assert ancient_title_reject("Rocket Attack in Babil, Baghdad, Iraq") == "rocket attack"
    assert ancient_title_reject("Abraham Lincoln", "Category:Portrait photographs")
    assert ancient_title_reject("PartherJar InnerSystem") == ""
    items = [
        _Item("war", "Rocket Attack in Babil, Baghdad, Iraq"),
        _Item("lincoln", "Abraham Lincoln"),
        _Item("jar", "PartherJar InnerSystem", "object"),
    ]
    pool = build_asset_pool(
        queries=["battery"],
        categories=[],
        dest=tmp_path,
        search=lambda _query: items,
        category=lambda _name: [],
        subcategories=lambda _name: [],
        openverse=lambda _query: [],
        download=_download,
        judge=_judge_ok,
        nano=None,
        target_min=1,
        object_floor=0,
    )
    captions = " ".join(item.caption for item in pool)
    assert "Rocket Attack" not in captions
    assert "Lincoln" not in captions
    assert "PartherJar" in captions


def test_a_labeled_museum_jar_stays_in_the_pool(tmp_path: Path) -> None:
    from modules.reel_artefact.asset_pool import build_asset_pool

    item = _Item("jar", "PartherJar InnerSystem", "object")

    def judge(_path: Path, caption: str, kind: str) -> dict:
        return {
            "passed": False,
            "contains_text": True,
            "non_photoreal": True,
            "rejection_reason": "contains_text,non_photoreal",
            "relevance_score": 0.91,
            "kind": kind,
        }

    pool = build_asset_pool(
        queries=["battery"],
        categories=[],
        dest=tmp_path,
        search=lambda _query: [item],
        category=lambda _name: [],
        subcategories=lambda _name: [],
        openverse=lambda _query: [],
        download=_download,
        judge=judge,
        nano=None,
        target_min=1,
        object_floor=0,
    )
    assert [asset.caption for asset in pool] == ["PartherJar InnerSystem"]
    assert pool[0].kind == "object"


def test_cache_seed_counts_and_a_short_pool_aborts(tmp_path: Path) -> None:
    from modules.reel_artefact.asset_pool import PoolTooSmall, build_asset_pool

    cache = tmp_path / "cache"
    cache.mkdir()
    _jpeg(cache / "variation_ref.jpg", (90, 70, 40))
    calls = {"n": 0}

    def nano(_prompt: str, dest: Path) -> Path:
        calls["n"] += 1
        raise RuntimeError("nanobanana_failed:gemini-3.1-flash-lite-image:402 RESOURCE_EXHAUSTED")

    seeded = build_asset_pool(
        queries=["battery"],
        categories=[],
        dest=tmp_path / "pool",
        search=lambda _query: [],
        category=lambda _name: [],
        subcategories=lambda _name: [],
        openverse=lambda _query: [],
        download=_download,
        judge=_judge_ok,
        nano=nano,
        cache_dir=cache,
        object_floor=1,
        target_min=1,
        strict=True,
    )
    assert calls["n"] == 0
    assert seeded[0].source == "cache"
    assert seeded[0].kind == "object"
    with pytest.raises(PoolTooSmall, match="Nano Banana 402 billing") as caught:
        build_asset_pool(
            queries=["battery"],
            categories=[],
            dest=tmp_path / "short",
            search=lambda _query: [_Item("one", "one site photo", "site")],
            category=lambda _name: [],
            subcategories=lambda _name: [],
            openverse=lambda _query: [],
            download=_download,
            judge=_judge_ok,
            nano=nano,
            object_floor=3,
            target_min=8,
            strict=True,
        )
    assert calls["n"] == 1
    assert "pool 1 < 8" in str(caught.value)


def test_a_source_is_not_used_twice_in_a_row_or_more_than_twice() -> None:
    from modules.reel_artefact.asset_pool import PoolAsset, fit_pool_beats

    assets = [
        PoolAsset("a.jpg", "limestone enclosure", "site", "commons", "a"),
        PoolAsset("b.jpg", "carved pillar", "site", "commons", "b"),
        PoolAsset("c.jpg", "stone circle", "site", "commons", "c"),
    ]
    sentences = [f"The enclosure stands in photograph {i}." for i in range(10)]
    durations = [1.0] * 10
    snippets, durs, shots = fit_pool_beats(sentences, durations, assets)
    keys = [shot.asset_key for shot in shots if shot.asset_key]
    assert keys
    assert all(keys[i] != keys[i + 1] for i in range(len(keys) - 1))
    assert all(keys.count(key) <= 2 for key in set(keys))
    anchors: dict[str, set[str]] = {}
    for shot in shots:
        if not shot.asset_key:
            continue
        anchors.setdefault(shot.asset_key, set()).add(shot.anchor)
    for key, used in anchors.items():
        assert len(used) == keys.count(key)
    assert abs(sum(durs) - 10.0) < 0.01
    assert len(snippets) == len(shots) < 10


def test_pool_photos_are_billed_as_their_source(tmp_path: Path) -> None:
    from modules.reel_artefact.asset_pool import PoolAsset, ShotAssignment, place_assignment
    from modules.reel_artefact.pricing import generation_bill

    source = _jpeg(tmp_path / "jar.jpg", (40, 80, 30))
    pool = [PoolAsset(str(source), "clay jar", "object", "commons", "jar")]
    result = place_assignment(
        ShotAssignment(sentence="The clay jar.", mode="asset", asset_key="jar", anchor="left"),
        tmp_path / "beat.jpg",
        pool,
    )
    key, count = generation_bill(result.path, 0, attempts=1)
    assert key == "image_commons"
    assert count == 1


def test_object_beats_do_not_show_a_landscape() -> None:
    from modules.reel_artefact.asset_pool import PoolAsset, assign_shots

    assets = [
        PoolAsset("land.jpg", "open farmland", "site", "commons", "land"),
        PoolAsset("jar.jpg", "clay jar", "object", "commons", "jar"),
    ]
    shots = assign_shots(
        ["The clay jar held the metal rod.", "Farmland surrounds the mound."],
        assets,
    )
    jar = shots[0]
    assert jar.asset_key == "jar"
    assert jar.mode == "asset"


def _pool_from(tmp_path: Path, item: _Item, judge) -> list:
    from modules.reel_artefact.asset_pool import build_asset_pool

    return build_asset_pool(
        queries=["battery"],
        categories=[],
        dest=tmp_path,
        search=lambda _query: [item],
        category=lambda _name: [],
        subcategories=lambda _name: [],
        openverse=lambda _query: [],
        download=_download,
        judge=judge,
        nano=None,
        target_min=1,
        object_floor=0,
    )


def test_a_low_score_museum_jar_stays_and_a_diagram_does_not(tmp_path: Path) -> None:
    item = _Item("jar", "PartherJar InnerSystem", "object")

    def museum(_path: Path, _caption: str, kind: str) -> dict:
        return {
            "passed": False,
            "contains_text": True,
            "non_photoreal": True,
            "rejection_reason": "documentary_fail:non_photoreal,contains_text",
            "relevance_score": 0.35,
            "kind": kind,
            "image_description": "clay jar in a museum case with a small label and a scale bar",
        }

    kept = _pool_from(tmp_path / "jar", item, museum)
    assert [asset.caption for asset in kept] == ["PartherJar InnerSystem"]

    def diagram(_path: Path, _caption: str, kind: str) -> dict:
        return {
            "passed": False,
            "contains_text": False,
            "non_photoreal": True,
            "rejection_reason": "documentary_fail:non_photoreal",
            "image_description": "ink diagram of the jar",
            "relevance_score": 0.95,
            "kind": kind,
        }

    assert _pool_from(tmp_path / "diagram", item, diagram) == []

    panel = _Item("panel", "museum text panel", "site")

    def text_panel(_path: Path, _caption: str, kind: str) -> dict:
        return {
            "passed": False,
            "contains_text": True,
            "rejection_reason": "documentary_fail:contains_text",
            "image_description": "a text panel on the wall",
            "relevance_score": 0.2,
            "kind": kind,
        }

    assert _pool_from(tmp_path / "panel", panel, text_panel) == []


def test_label_bands_are_cropped_off_an_object_photo(tmp_path: Path) -> None:
    from modules.reel_artefact.asset_pool import build_asset_pool
    from modules.reel_artefact.crop import crop_label_bands

    image = Image.new("RGB", (200, 400), (180, 40, 40))
    image.paste(Image.new("RGB", (200, 56), (255, 255, 255)), (0, 344))
    cropped = crop_label_bands(image)
    assert cropped.getpixel((10, cropped.size[1] - 1))[0] > 100
    assert cropped.getpixel((10, cropped.size[1] - 1))[1] < 80

    def download(_item: _Item, folder: Path) -> Path:
        path = folder / "jar.jpg"
        image.save(path, quality=95)
        return path

    def judge(_path: Path, _caption: str, kind: str) -> dict:
        return {
            "passed": False,
            "contains_text": True,
            "non_photoreal": True,
            "rejection_reason": "documentary_fail:contains_text",
            "relevance_score": 0.0,
            "kind": kind,
            "image_description": "museum photograph of a clay jar with a caption strip",
        }

    pool = build_asset_pool(
        queries=["battery"],
        categories=[],
        dest=tmp_path,
        search=lambda _query: [_Item("jar", "Parthian jar", "object")],
        category=lambda _name: [],
        subcategories=lambda _name: [],
        openverse=lambda _query: [],
        download=download,
        judge=judge,
        nano=None,
        target_min=1,
        object_floor=0,
    )
    assert pool
    with Image.open(pool[0].path) as saved:
        pixel = saved.getpixel((10, saved.size[1] - 1))
    assert pixel[0] > 100
    assert pixel[1] < 120


def test_committed_baghdad_cache_names_the_licence(tmp_path: Path) -> None:
    import json

    from modules.reel_artefact.asset_pool import build_asset_pool
    from modules.reel_artefact.fetch import committed_cache_dir

    folder = committed_cache_dir("ancient_knowledge", "baghdad_battery")
    credits = json.loads((folder / "credits.json").read_text(encoding="utf-8"))
    photos = credits["photos"]
    assert len(photos) >= 8
    blob = " ".join(
        f"{row.get('file', '')} {row.get('title', '')} {row.get('subject', '')}" for row in photos
    ).lower()
    assert "jar" in blob or "battery" in blob or "pile" in blob
    assert "ctesiphon" in blob or "mesopotamia" in blob or "taq" in blob
    for row in photos:
        assert row.get("license")
        assert row.get("author")
        assert row.get("source_url")
        assert (folder / row["file"]).is_file()
    pool = build_asset_pool(
        queries=["battery"],
        categories=[],
        dest=tmp_path,
        search=lambda _query: [],
        category=lambda _name: [],
        subcategories=lambda _name: [],
        openverse=lambda _query: [],
        download=_download,
        judge=_judge_ok,
        nano=None,
        cache_dir=folder,
        target_min=1,
        object_floor=0,
    )
    assert any(asset.source == "cache" and asset.kind == "object" for asset in pool)
