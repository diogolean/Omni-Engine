# -*- coding: utf-8 -*-
"""Drive-backed stills are staged locally, and a flat card is not encoded."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from production.formats.economic_reel.grounding.still_cache import write_spoken_sidecar
from production.media.reel_ffmpeg_render import (
    beat_timeout_s,
    refuse_flat_still,
    stage_still,
)
from production.pipeline.local_review import (
    DOGON_STEM,
    begin,
    end,
    review_cache_dirs,
)
from production.pipeline.stills import _apply_still_cache_to_jobs


def _pattern(path: Path) -> None:
    image = Image.new("RGB", (96, 160), (16, 24, 32))
    pixels = image.load()
    for y in range(160):
        for x in range(96):
            if (x + y) % 9 < 4:
                pixels[x, y] = (180, 90, 40)
    image.save(path, format="JPEG", quality=90)


def test_the_reported_187_frame_beat_is_not_given_600_seconds() -> None:
    assert beat_timeout_s(187) <= 120
    assert beat_timeout_s(187) < 600


def test_env_timeout_is_a_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REEL_FFMPEG_TIMEOUT_S", "45")
    assert beat_timeout_s(5000) <= 45


def test_a_flat_card_is_refused_before_ffmpeg(tmp_path: Path) -> None:
    source = tmp_path / "scene_02.jpg"
    Image.new("RGB", (96, 160), (86, 108, 72)).save(source, format="JPEG")
    staged = stage_still(source, tmp_path / "local.jpg")
    with pytest.raises(RuntimeError, match="refusing to encode a flat still: scene_02.jpg"):
        refuse_flat_still(staged, source.name)


def test_a_textured_still_stages_and_is_allowed(tmp_path: Path) -> None:
    source = tmp_path / "scene_02.jpg"
    _pattern(source)
    staged = stage_still(source, tmp_path / "local.jpg")
    refuse_flat_still(staged, source.name)
    assert staged.is_file()
    assert staged.stat().st_size > 32


def test_an_unreadable_still_fails_fast(tmp_path: Path) -> None:
    source = tmp_path / "scene_02.jpg"
    source.write_text("not an image", encoding="utf-8")
    with pytest.raises(RuntimeError, match="still unreadable"):
        stage_still(source, tmp_path / "local.jpg")


def test_prior_review_reuses_only_a_passed_line(tmp_path: Path) -> None:
    page = tmp_path / "ancient_knowledge"
    work = (
        page / "clips" / "local_review" / "20261107T225611Z"
        / "assets" / "ep_20261107_1956" / "work"
    )
    work.mkdir(parents=True)
    stem = f"{DOGON_STEM}_v01"
    spoken = "The Dogon kept a record of the star."
    good = work / f"{stem}_act02.jpg"
    _pattern(good)
    write_spoken_sidecar(good, spoken, passed=True, score=0.91)
    rejected = work / f"{stem}_act03.jpg"
    _pattern(rejected)
    write_spoken_sidecar(rejected, "another line", passed=False, score=0.1)
    begin(page, expect_stem=DOGON_STEM, protected=[])
    try:
        dirs = review_cache_dirs(None, page)
        assert work in dirs
        new_work = page / "clips" / "local_review"
        # current stamp is excluded; the old work dir remains
        jobs = {1: "generate", 2: "generate"}
        meta: dict = {}
        reused = _apply_still_cache_to_jobs(
            jobs=jobs,
            meta=meta,
            n_acts=3,
            start=0,
            work_dir=new_work / "fresh",
            stem=stem,
            extra_dirs=dirs,
            spoken_by_act={1: spoken, 2: "another line"},
        )
        assert reused == 1
        assert 1 not in jobs
        assert 2 in jobs
        assert meta[1]["cached_path"].is_file()
    finally:
        end()
