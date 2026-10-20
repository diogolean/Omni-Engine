# -*- coding: utf-8 -*-
"""Upgrade ``content_library.json`` files to ContentItem v1.

    python tools/migrate_content_items.py
    python tools/migrate_content_items.py --channel ancient_knowledge
    python tools/migrate_content_items.py --path path/to/content_library.json

The first change to a file copies it to ``<name>.bak`` beside the original.
A file that is already v1 is left as it is. Canonical files live at
``channels_config/<channel>/store/content_library.json``.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_FACTORY = Path(__file__).resolve().parents[1]
if str(_FACTORY) not in sys.path:
    sys.path.insert(0, str(_FACTORY))

from shared.contracts import content_library_path, upgrade_content_library  # noqa: E402
from shared.paths import discover_channel_ids  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Upgrade content libraries to ContentItem v1.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--channel", help="One channel slug under channels_config/")
    group.add_argument("--path", type=Path, help="One library JSON file")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def _targets(channel: str | None, path: Path | None) -> list[tuple[str, Path]]:
    if path is not None:
        return [("", path)]
    if channel:
        return [(channel, content_library_path(channel))]
    found: list[tuple[str, Path]] = []
    for slug in discover_channel_ids():
        library = content_library_path(slug)
        if library.is_file():
            found.append((slug, library))
    return found


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s | %(name)s | %(message)s",
    )
    targets = _targets(args.channel, args.path)
    if not targets:
        logging.info("no content_library.json files to upgrade")
        return 0
    changed = 0
    for channel, library in targets:
        count = upgrade_content_library(library, channel_id=channel)
        if count:
            changed += 1
            logging.info("upgraded %s (%d row(s))", library, count)
        else:
            logging.info("already v1 or missing: %s", library)
    logging.info("files upgraded: %d", changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
