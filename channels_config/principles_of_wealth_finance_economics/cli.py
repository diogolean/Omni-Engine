# -*- coding: utf-8 -*-
"""CLI for the Principles of Wealth library-ingest + YouTube publish fabric.

Source videos stay on the external production drive (read-only). Processed
files are written to a Processed/ sibling folder. YouTube tokens:
``credentials/tokens/youtube_token_principles_of_wealth_finance_economics.json``.

Typical first run
-----------------
    python tools/channel_cli.py --channel principles_of_wealth_finance_economics scan
    python tools/channel_cli.py --channel principles_of_wealth_finance_economics process --dry-run
    python tools/channel_cli.py --channel principles_of_wealth_finance_economics process
    python tools/channel_cli.py --channel principles_of_wealth_finance_economics publish --mode longs --episodes 1-10 --dry-run
    python tools/channel_cli.py --channel principles_of_wealth_finance_economics publish --mode longs --episodes 1-10
    python tools/channel_cli.py --channel principles_of_wealth_finance_economics publish --mode shorts --dry-run
    python tools/channel_cli.py --channel principles_of_wealth_finance_economics publish --mode shorts --episodes 1
    python tools/channel_cli.py --channel principles_of_wealth_finance_economics metadata --dry-run
    python tools/channel_cli.py --channel principles_of_wealth_finance_economics metadata --apply
    python tools/channel_cli.py --channel principles_of_wealth_finance_economics cleanup --from-episode 2 --longs-only --dry-run
    python tools/channel_cli.py --channel principles_of_wealth_finance_economics cleanup --from-episode 2 --longs-only
"""
from __future__ import annotations
from shared.layer_bridge import Symbol

import argparse
import logging
import sys
from pathlib import Path

_FACTORY_ROOT = Path(__file__).resolve().parents[2]
if str(_FACTORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_FACTORY_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_FACTORY_ROOT / ".env", override=False)
except ImportError:
    pass

from shared.logging import setup_cli_logging  # noqa: E402
run_cleanup = Symbol('production.formats.wealth.pipeline', 'run_cleanup')
run_metadata = Symbol('production.formats.wealth.pipeline', 'run_metadata')
run_playlists = Symbol('production.formats.wealth.pipeline', 'run_playlists')
run_process = Symbol('production.formats.wealth.pipeline', 'run_process')
run_publish = Symbol('production.formats.wealth.pipeline', 'run_publish')
run_scan = Symbol('production.formats.wealth.pipeline', 'run_scan')
run_status = Symbol('production.formats.wealth.pipeline', 'run_status')


def _setup_logging(verbose: bool) -> None:
    setup_cli_logging(verbose)


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--source-dir",
        default=None,
        help="Override the production folder (default: page_config.SOURCE_DIRECTORY).",
    )
    p.add_argument(
        "--episodes",
        default=None,
        help="Episode filter, e.g. 1,2,5-8. Default = every matched episode.",
    )
    p.add_argument("--dry-run", action="store_true", help="Preview without writing or uploading.")
    p.add_argument("-v", "--verbose", action="store_true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/channel_cli.py --channel principles_of_wealth_finance_economics",
        description=(
            "Principles of Wealth — scan the production drive, re-sign assets, "
            "upload longs then Shorts (relatedVideoId), and build ACT playlists."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser(
        "scan",
        help="Match longs (Ray Dalio epN) and up to 10 Shorts per episode (Short1-10 / EpN.1-10).",
    )
    _add_common(p_scan)

    p_proc = sub.add_parser(
        "process",
        help=(
            "Sign every raw long, Short, and thumbnail in SOURCE_DIRECTORY "
            "via core.utils.fingerprint_engine into Processed/."
        ),
    )
    _add_common(p_proc)
    p_proc.add_argument("--processed-dir", default=None, help="Override Processed/ output folder.")
    p_proc.add_argument("--force", action="store_true", help="Re-process even when output already exists.")
    p_proc.add_argument("--no-hwaccel", action="store_true", help="Disable FFmpeg -hwaccel auto.")
    p_proc.add_argument(
        "--hw-encode",
        action="store_true",
        help="Use h264_nvenc instead of libx264 ultrafast (NVIDIA GPU).",
    )

    p_pub = sub.add_parser(
        "publish",
        help=(
            "Independently schedule longs (Tue/Thu, 2 per week) or Shorts "
            "(daily 18:00 America/New_York) via private+publishAt. "
            "Use --dry-run to preview dates."
        ),
    )
    _add_common(p_pub)
    p_pub.add_argument(
        "--mode",
        choices=["longs", "shorts"],
        required=True,
        help="Run longs or Shorts independently (required).",
    )
    p_pub.add_argument(
        "--schedule",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Schedule with private+publishAt (default: True). Use --no-schedule to publish now.",
    )
    p_pub.add_argument(
        "--start-date",
        default=None,
        help=(
            "First slot YYYY-MM-DD. Default: day after the latest YouTube "
            "publishAt (or state last_*_scheduled_at), else tomorrow / next Tue-Thu."
        ),
    )
    p_pub.add_argument(
        "--time-utc",
        default=None,
        help=(
            "HH:MM UTC override. Default clock is 18:00 America/New_York "
            "for both longs (Tue/Thu) and Shorts (daily)."
        ),
    )
    p_pub.add_argument(
        "--privacy-long",
        choices=["private", "unlisted", "public"],
        default="private",
        help="Used only with --no-schedule. Scheduled uploads are always private.",
    )
    p_pub.add_argument(
        "--privacy-short",
        choices=["private", "unlisted", "public"],
        default="private",
        help="Used only with --no-schedule. Scheduled uploads are always private.",
    )
    p_pub.add_argument(
        "--use-originals",
        action="store_true",
        help="Upload source files instead of Processed/ uniqueness outputs.",
    )
    p_pub.add_argument(
        "--process-first",
        action="store_true",
        help="Run the uniqueness pass before uploading.",
    )
    p_pub.add_argument(
        "--reupload",
        action="store_true",
        help="Upload even when wealth_publish_state.json already has a video ID.",
    )
    p_pub.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Override global daily upload safety cap (default MAX_DAILY_UPLOADS=20).",
    )
    p_pub.add_argument("--no-hwaccel", action="store_true")
    p_pub.add_argument("--hw-encode", action="store_true")

    p_pl = sub.add_parser("playlists", help="Create/sync the three ACT playlists in chronological order.")
    _add_common(p_pl)

    p_st = sub.add_parser("status", help="Show source matches vs uploaded IDs.")
    _add_common(p_st)

    p_meta = sub.add_parser(
        "metadata",
        help="Audit/export SEO pack and patch titles, descriptions, tags, playlists on YouTube.",
    )
    _add_common(p_meta)
    p_meta.add_argument(
        "--apply",
        action="store_true",
        help="Write snippets to YouTube. Default is export + dry-run table.",
    )
    p_meta.add_argument(
        "--mode",
        choices=["all", "longs", "shorts", "playlists"],
        default="all",
        help="Patch longs, Shorts, playlists, or all (default).",
    )

    p_clean = sub.add_parser(
        "cleanup",
        help=(
            "Query YouTube uploads/scheduled videos, match Ep 2+ longs by title, "
            "and delete them. Never touches Episode 1 or any Shorts."
        ),
    )
    _add_common(p_clean)
    p_clean.add_argument(
        "--from-episode",
        type=int,
        default=2,
        help="First episode eligible for long deletion (minimum 2; Episode 1 is protected).",
    )
    p_clean.add_argument(
        "--longs-only",
        action="store_true",
        default=True,
        help="Only delete long-form videos (always enforced).",
    )

    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))

    if args.command == "scan":
        run_scan(source_dir=args.source_dir)
        return 0
    if args.command == "process":
        run_process(
            source_dir=args.source_dir,
            processed_dir=getattr(args, "processed_dir", None),
            episodes=args.episodes,
            skip_existing=not args.force,
            hwaccel=not args.no_hwaccel,
            hw_encode=args.hw_encode,
            dry_run=args.dry_run,
        )
        return 0
    if args.command == "cleanup":
        run_cleanup(
            from_episode=getattr(args, "from_episode", 2),
            longs_only=True,
            dry_run=args.dry_run,
            episodes=args.episodes,
            source_dir=args.source_dir,
        )
        return 0
    if args.command == "publish":
        run_publish(
            source_dir=args.source_dir,
            mode=args.mode,
            episodes=args.episodes,
            schedule=args.schedule,
            start_date=args.start_date,
            time_utc=args.time_utc,
            privacy_long=args.privacy_long,
            privacy_short=args.privacy_short,
            use_processed=not args.use_originals,
            process_first=args.process_first,
            skip_existing_uploads=not args.reupload,
            dry_run=args.dry_run,
            hwaccel=not args.no_hwaccel,
            hw_encode=args.hw_encode,
            limit=getattr(args, "limit", None),
        )
        return 0
    if args.command == "playlists":
        run_playlists(
            source_dir=args.source_dir,
            episodes=args.episodes,
            dry_run=args.dry_run,
        )
        return 0
    if args.command == "status":
        run_status(source_dir=args.source_dir)
        return 0
    if args.command == "metadata":
        run_metadata(
            apply=bool(getattr(args, "apply", False)) and not args.dry_run,
            dry_run=args.dry_run or not bool(getattr(args, "apply", False)),
            mode=getattr(args, "mode", "all"),
            episodes=args.episodes,
            source_dir=args.source_dir,
        )
        return 0
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
