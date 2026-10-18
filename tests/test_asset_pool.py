# -*- coding: utf-8 -*-
"""Asset-first stills: a judged pool, then shots assigned from that pool."""
from __future__ import annotations

from pathlib import Path

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
        "The clay jar is lifted from the soil.",
        "The pillars stand in the enclosure.",
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
