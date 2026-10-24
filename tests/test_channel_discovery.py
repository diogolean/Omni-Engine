"""A channel is a folder. The CLI list is that walk, not a hand-written tuple."""
from __future__ import annotations

from pathlib import Path

import pytest

from production.pipeline.channel_context import VALID_PAGES, discover_channel_ids
from production.pipeline.cli_parser import parse_cli_args
from production.writers.persona import channel_profile_rows

_REPO = Path(__file__).resolve().parents[1]


def test_a_new_folder_is_a_channel_without_an_engine_edit(tmp_path: Path) -> None:
    folder = tmp_path / "channels_config" / "north_garden"
    folder.mkdir(parents=True)
    (folder / "master_dna.json").write_text("{}", encoding="utf-8")
    skipped = tmp_path / "channels_config" / "aiwake"
    skipped.mkdir()
    (skipped / "master_dna.json").write_text("{}", encoding="utf-8")
    hidden = tmp_path / "channels_config" / ".draft"
    hidden.mkdir()
    (hidden / "master_dna.json").write_text("{}", encoding="utf-8")

    assert discover_channel_ids(tmp_path) == ("north_garden",)
    source = (_REPO / "production" / "pipeline" / "channel_context.py").read_text(encoding="utf-8")
    assert "north_garden" not in source
    assert "VALID_PAGES: tuple[str, ...] = (" not in source


def test_valid_pages_match_the_channel_folders() -> None:
    assert VALID_PAGES == discover_channel_ids()
    assert VALID_PAGES == tuple(row["channel"] for row in channel_profile_rows())
    assert "aiwake" not in VALID_PAGES
    assert "wonder_feed" in VALID_PAGES
    assert list(VALID_PAGES) == sorted(VALID_PAGES)


def test_cli_accepts_a_discovered_channel_and_rejects_an_unknown_one() -> None:
    args = parse_cli_args(["--channel", "wonder_feed"])
    assert args.page == "wonder_feed"
    with pytest.raises(SystemExit):
        parse_cli_args(["--channel", "not_a_real_channel"])
