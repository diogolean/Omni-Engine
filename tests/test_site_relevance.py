# -*- coding: utf-8 -*-
"""s23b B: a site photograph only when its line names the site."""
from __future__ import annotations

from production.formats.economic_reel.artefact.asset_pool import PoolAsset, _unused_asset, assign_shots, reset_claims


def _pool() -> list[PoolAsset]:
    """Shaped like the s23b Baghdad Battery pool."""
    return [
        PoolAsset("01.jpg", "01 parthian jar copper iron. File:Ironie pile Bagdad.jpg",
                  "object", "cache", "jar01", photo=True),
        PoolAsset("02.jpg", "02 parthian jar. File:PartherJar InnerSystem.jpg",
                  "object", "cache", "jar02", photo=True),
        PoolAsset("12.jpg", "Parthian clay jar, 1st-2nd century",
                  "object", "cache", "jar12", photo=True),
        PoolAsset("13.jpg", "Parthian clay jar, Cleveland Museum of Art",
                  "object", "cache", "jar13", photo=True),
        PoolAsset("15.jpg", "Parthian jar, probably Iran",
                  "object", "cache", "jar15", photo=True),
        PoolAsset("04.jpg", "04 ctesiphon 1932. File:Ctesiphon, Iraq, 1932.jpg Ctesiphon arch",
                  "site", "cache", "ctes04", photo=True),
        PoolAsset("06.jpg", "06 taq kasra. File:Taq Kasra.jpg Taq Kasra, Ctesiphon",
                  "site", "cache", "taq06", photo=True),
        PoolAsset("09.jpg", "Ctesiphon wall decoration, Mesopotamia",
                  "site", "cache", "wall09", photo=True),
        # Search hits that only share a generic word, or name the site in
        # passing while showing something else.
        PoolAsset("real_06.jpg", "Minaret with brick wall decorations, Iraq",
                  "site", "commons", "minaret", photo=True),
        PoolAsset("real_07.jpg", "Conical shrine on the road to Taq Kasra arch",
                  "site", "commons", "shrine", photo=True),
    ]


def test_the_taq_kasra_line_takes_the_named_cache_photo() -> None:
    shots = assign_shots(
        [
            "The Baghdad Battery is a clay jar with a copper cylinder.",
            "The Taq Kasra arch stands as a testament to their skill.",
        ],
        _pool(),
    )
    assert shots[1].asset_key == "taq06"


def test_wall_decorations_never_take_the_minaret() -> None:
    shots = assign_shots(
        [
            "The Baghdad Battery is a clay jar with a copper cylinder.",
            "Wall decorations from that era show intricate, repeating patterns.",
        ],
        _pool(),
    )
    assert shots[1].asset_key != "minaret"
    assert shots[1].asset_key != "shrine"


def test_a_line_that_only_says_arch_names_no_site() -> None:
    shots = assign_shots(
        [
            "The Baghdad Battery is a clay jar with a copper cylinder.",
            "An arch rises over the plain.",
        ],
        _pool(),
    )
    keys = {shot.asset_key for shot in shots}
    assert not keys & {"minaret", "shrine", "taq06", "ctes04", "wall09"}


def test_a_generic_fallback_line_takes_a_jar_not_ctesiphon() -> None:
    reset_claims()
    chosen = _unused_asset(
        "Historians debate whether such precision existed in the first century.",
        _pool(),
    )
    assert chosen is not None and chosen.kind == "object"
    reset_claims()


def test_a_named_site_line_still_gets_its_site() -> None:
    shots = assign_shots(
        [
            "The Baghdad Battery is a clay jar with a copper cylinder.",
            "The ruins of Ctesiphon lie a few miles away.",
        ],
        _pool(),
    )
    assert shots[1].asset_key in {"ctes04", "taq06", "wall09"}
