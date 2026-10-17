# -*- coding: utf-8 -*-
"""Generate one test clip.

    python -m modules.video_providers.cli \\
        --provider elevenlabs \\
        --model veo-3.1-fast-generate-001 \\
        --duration 4 --aspect 9:16 --resolution 720p \\
        --prompt "..."

The file lands under ``OUTPUT_PATH/<channel>/clips`` (default channel
``endless_summer_paradise``). The command is an explicit request, so it runs
even when the channel flag is off. It still refuses Seedance unless that
model is allowed in config.
"""
from __future__ import annotations

import argparse
import sys

from modules.video_providers.config import load_channel_settings
from modules.video_providers.errors import VideoError, VideoRequestError
from modules.video_providers.registry import get_provider
from modules.video_providers.service import generate_clip, resolve_clip_path
from modules.video_providers.types import ReferenceImage, VideoRequest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m modules.video_providers.cli",
        description="Generate one AI video clip and write a cost sidecar next to it.",
    )
    parser.add_argument("--provider", default=None, help="elevenlabs, gemini, or kling.")
    parser.add_argument("--model", default=None, help="Model id. Default is the channel model.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--negative-prompt", default=None)
    parser.add_argument("--duration", type=float, default=None, help="Seconds. Veo: 4, 6, or 8.")
    parser.add_argument("--aspect", default=None, help="Veo: 16:9 or 9:16.")
    parser.add_argument("--resolution", default=None, help="Veo: 720p, 1080p, or 4K.")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--start-frame",
        default=None,
        help="Image path, asset:<id>, or generation:<id>.",
    )
    parser.add_argument("--end-frame", default=None)
    parser.add_argument(
        "--reference-image",
        action="append",
        default=[],
        help="Subject reference. Repeat up to 3 times for Veo. Requires duration 8.",
    )
    parser.add_argument(
        "--style-image",
        action="append",
        default=[],
        help="Style reference (Veo). Counts toward the 3-image limit.",
    )
    parser.add_argument("--audio", dest="audio", action="store_true")
    parser.add_argument("--no-audio", dest="audio", action="store_false")
    parser.set_defaults(audio=None)
    parser.add_argument(
        "--channel",
        default="endless_summer_paradise",
        help="Output channel folder under OUTPUT_PATH. Default: endless_summer_paradise.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="mp4 path under OUTPUT_PATH. Default: <channel>/clips/<timestamp>-<slug>.mp4.",
    )
    parser.add_argument("--timeout", type=float, default=None, help="Poll timeout in seconds.")
    return parser


def request_from_args(args: argparse.Namespace) -> VideoRequest:
    images: list[ReferenceImage] = [
        ReferenceImage(source=path, role="subject") for path in args.reference_image
    ]
    images.extend(ReferenceImage(source=path, role="style") for path in args.style_image)
    return VideoRequest(
        prompt=args.prompt,
        negative_prompt=args.negative_prompt,
        duration=args.duration,
        aspect=args.aspect,
        resolution=args.resolution,
        audio=args.audio,
        start_frame=args.start_frame,
        end_frame=args.end_frame,
        reference_images=tuple(images),
        seed=args.seed,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_channel_settings(args.channel)
    provider_name = args.provider or settings.provider
    model = args.model or settings.model
    try:
        provider = get_provider(
            provider_name,
            model=model,
            allow_seedance=settings.allow_seedance,
        )
        dest = resolve_clip_path(args.channel, args.prompt, args.output)
        result = generate_clip(
            request_from_args(args),
            dest=dest,
            channel=args.channel,
            provider=provider,
            settings=settings,
            force=True,
            timeout_s=args.timeout,
        )
    except VideoRequestError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except VideoError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(result.path)
    print(result.sidecar_path)
    if result.credits_delta is not None:
        print(f"credits_delta={result.credits_delta}")
    print(f"elapsed_s={result.elapsed_s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
