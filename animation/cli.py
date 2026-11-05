"""Command line for the parametric engine.

``python -m animation render scene.yaml --audio take.wav --output out.mp4``
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from animation.api import render_scene


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="animation",
        description="Render a parametric scene (Rigly) to an MP4.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="Render a scene spec and an audio file")
    render.add_argument("spec", type=Path, help="Scene JSON or YAML")
    render.add_argument("--audio", required=True, type=Path, help="WAV, MP3, or another ffmpeg input")
    render.add_argument("--output", "-o", required=True, type=Path, help="MP4 path")
    render.add_argument(
        "--preview",
        action="store_true",
        help="Half resolution, 12 fps cap, fast encode",
    )
    render.add_argument("--seed", type=int, default=None, help="Override the spec seed")
    render.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Seconds allowed for the ffmpeg calls in this render",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return int(code)
    if args.command != "render":
        parser.error(f"unknown command {args.command}")
    try:
        stats = render_scene(
            args.spec,
            args.audio,
            args.output,
            preview=bool(args.preview),
            seed=args.seed,
            timeout_s=args.timeout,
        )
    except (OSError, ValueError, TimeoutError, RuntimeError) as exc:
        print(f"animation: {exc}", file=sys.stderr)
        return 1
    print(
        f"{stats.output_path} {stats.width}x{stats.height} "
        f"{stats.frames_written} frames in {stats.render_seconds:.2f}s "
        f"({stats.encoder})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
