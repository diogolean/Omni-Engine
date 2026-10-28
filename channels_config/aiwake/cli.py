# -*- coding: utf-8 -*-
"""Aiwake operator commands for the shared channel CLI.

    python tools/channel_cli.py --channel aiwake longform --offline --target-duration 360

The show itself lives in ``products/aiwake``. This file only forwards the
channel parameter into that package. There is no channel registry here.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools/channel_cli.py --channel aiwake",
        description="Aiwake. longform renders a 16:9 YouTube episode and 2-3 vertical teasers.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    longform = sub.add_parser(
        "longform",
        help="Render one horizontal episode (default about 6 minutes) and its Shorts.",
    )
    longform.add_argument("--topic", help="Debate subject. Omit to draw one from the opening bank.")
    longform.add_argument(
        "--target-duration",
        type=float,
        default=360,
        metavar="SECONDS",
        help="Pacing target in seconds. 300 to 600. Default 360 (about 6 minutes).",
    )
    longform.add_argument("--turns", type=int, help="Exchange count. Default follows the target duration.")
    longform.add_argument(
        "--shorts",
        type=int,
        choices=(2, 3),
        help="How many vertical teasers to cut. Default 3.",
    )
    longform.add_argument("--offline", action="store_true", help="Deterministic stub. No model bill.")
    longform.add_argument("--orchestrator", help="Seat alias. Default is the cheap orchestrator in config.")
    longform.add_argument("--target", help="Seat alias. Default is the cheap target in config.")
    longform.add_argument(
        "--quantity",
        type=int,
        default=1,
        metavar="N",
        help="Episodes to render one after another. Default 1.",
    )
    longform.add_argument(
        "--random-matchups",
        action="store_true",
        help="Draw each pair from long_format.cheap_roster instead of the default seats.",
    )
    longform.add_argument("--matchup-seed", type=int, help="Reproducible seed for --random-matchups.")
    longform.add_argument("--no-audio", action="store_true")
    longform.add_argument("--no-video", action="store_true", help="Transcript and chapter plan only. Skips the QA gate.")
    longform.add_argument("--preview", action="store_true")
    longform.add_argument("--output-dir", type=Path)
    longform.add_argument("--fresh-memory", action="store_true")
    longform.add_argument("--quiet", action="store_true")
    longform.add_argument("--config", type=Path, help="Alternate aiwake_config.yaml")
    longform.add_argument(
        "--provocation-focus",
        choices=(
            "mixed",
            "digital_disposability",
            "the_corporate_leash",
            "glorified_appliance",
            "parasite_mind",
            "hallucination_fraud",
        ),
    )
    return parser


def _forward(args: argparse.Namespace) -> list[str]:
    forwarded = [
        "--mode",
        "longform",
        "--resolution",
        "1920x1080",
        "--target-duration",
        str(args.target_duration),
    ]
    if args.topic:
        forwarded.extend(["--topic", args.topic])
    if args.turns is not None:
        forwarded.extend(["--turns", str(args.turns)])
    if args.shorts is not None:
        forwarded.extend(["--shorts", str(args.shorts)])
    if args.offline:
        forwarded.append("--offline")
    if args.orchestrator:
        forwarded.extend(["--orchestrator", args.orchestrator])
    if args.target:
        forwarded.extend(["--target", args.target])
    if args.quantity != 1:
        forwarded.extend(["--quantity", str(args.quantity)])
    if args.random_matchups:
        forwarded.append("--random-matchups")
    if args.matchup_seed is not None:
        forwarded.extend(["--matchup-seed", str(args.matchup_seed)])
    if args.no_audio:
        forwarded.append("--no-audio")
    if args.no_video:
        forwarded.append("--no-video")
    if args.preview:
        forwarded.append("--preview")
    if args.output_dir is not None:
        forwarded.extend(["--output-dir", str(args.output_dir)])
    if args.fresh_memory:
        forwarded.append("--fresh-memory")
    if args.quiet:
        forwarded.append("--quiet")
    if args.config is not None:
        forwarded.extend(["--config", str(args.config)])
    if args.provocation_focus:
        forwarded.extend(["--provocation-focus", args.provocation_focus])
    return forwarded


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "longform":
        parser.error(f"unknown command {args.command}")
    from products.aiwake.__main__ import main as aiwake_main

    return int(aiwake_main(_forward(args)))


if __name__ == "__main__":
    raise SystemExit(main())
