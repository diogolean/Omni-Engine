"""B relevance: generic lines hold the previous photo, never an unnamed site."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from modules.reel_artefact import asset_pool
from modules.reel_artefact.asset_pool import PoolAsset, assign_shots, screen_pool, view_key


def _jar(i: int) -> PoolAsset:
    return PoolAsset(path=f"/x/jar{i}.jpg", caption=f"Parthian clay jar {i}", kind="object",
                     source="cache", key=f"jar{i}", photo=True)


def _site(key: str, caption: str) -> PoolAsset:
    return PoolAsset(path=f"/x/{key}.jpg", caption=caption, kind="site",
                     source="cache", key=key, photo=True)


POOL = [_jar(i) for i in range(3)] + [
    _site("ctes", "Ctesiphon arch, Mesopotamia"),
    _site("minaret", "Khan al-Kumruk minaret"),
]


def test_generic_lines_past_the_span_limit_hold_instead_of_flux() -> None:
    lines = ["This clay jar was found in 1936."] + [
        "Historians still debate what it was for."] * 5
    shots = assign_shots(lines, POOL[:1], geography="Mesopotamia", flux_max=6)
    assert [s.mode for s in shots] == ["asset"]
    assert shots[0].span == 6


def test_an_atmosphere_line_past_the_limit_gets_flux() -> None:
    lines = ["This clay jar was found in 1936."] + ["People argued about it."] * 2 + [
        "The desert sky darkened over the horizon."]
    shots = assign_shots(lines, POOL[:1], geography="Mesopotamia", flux_max=6)
    assert [s.mode for s in shots] == ["asset", "flux"]


def test_a_line_naming_a_pool_site_is_not_atmosphere() -> None:
    lines = ["This clay jar was found in 1936.",
             "At sunset the Ctesiphon arch glowed over the horizon."]
    shots = assign_shots(lines, POOL, geography="Mesopotamia", flux_max=6)
    assert shots[-1].mode == "asset" and shots[-1].asset_key == "ctes"


def test_unnamed_site_never_fills_a_generic_line() -> None:
    lines = ["This clay jar was found in 1936.", "Similar vessels sit in museums."]
    shots = assign_shots(lines, POOL, geography="Mesopotamia", flux_max=6)
    assert all(s.asset_key not in {"ctes", "minaret"} for s in shots)


def test_fallback_reports_exhausted_instead_of_an_unnamed_site() -> None:
    asset_pool.reset_claims()
    for jar in POOL[:3]:
        asset_pool.claim_key(jar.key)
    assert asset_pool._unused_asset("Similar vessels sit in museums.", POOL) is None
    asset_pool.reset_claims()


def test_two_photos_of_one_facade_share_a_view() -> None:
    a = _site("04", "04 ctesiphon 1932. File:Ctesiphon, Iraq, 1932.jpg Ctesiphon arch, Mesopotamia")
    b = _site("05", "05 ctesiphon arch. File:Ctesiphon.jpg Ctesiphon")
    c = _site("07", "07 arch of ctesiphon. File:Arch of Ctesiphon.jpg Arch of Ctesiphon")
    wall = _site("09", "09 ctesiphon wall. File:Wall decoration from Ctesiphon DP-1091-001.jpg")
    taq = _site("06", "06 taq kasra. File:Taq Kasra.jpg Taq Kasra, Ctesiphon")
    assert view_key(a) == view_key(b) == view_key(c)
    assert len({view_key(a), view_key(wall), view_key(taq)}) == 3
    assert view_key(_jar(1)) == frozenset()


def test_screen_pool_keeps_one_photo_per_view(tmp_path: Path) -> None:
    import random
    assets = []
    for n, cap in enumerate(["Ctesiphon arch", "Arch of Ctesiphon", "Taq Kasra, Ctesiphon"]):
        rng = random.Random(n)
        img = Image.new("RGB", (64, 48))
        img.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(64 * 48)])
        img = img.resize((640, 480), Image.NEAREST)
        path = tmp_path / f"s{n}.jpg"
        img.save(path, quality=92)
        assets.append(PoolAsset(path=str(path), caption=cap, kind="site",
                                source="cache", key=f"s{n}"))
    kept = [a.key for a in screen_pool(assets)]
    assert kept == ["s0", "s2"]
