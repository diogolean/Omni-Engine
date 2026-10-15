# -*- coding: utf-8 -*-
"""Command line for avatar generation and model download."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from models_avatar.avatar_config import list_avatar_ids, load_avatar
from models_avatar.backends import get_backend
from models_avatar.batch import run_batch
from models_avatar.civitai.client import CivitaiClient
from models_avatar.service import render


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="models_avatar",
        description="Generate images for an AI-model avatar persona.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list", help="List avatar folders.")
    list_cmd.set_defaults(func=_cmd_list)

    gen = sub.add_parser("generate", help="Generate one image.")
    _add_avatar(gen)
    gen.add_argument("--preset", default=None, help="Preset name. Default: the avatar default (fast).")
    gen.add_argument("--backend", default=None, help="runpod_pod (default), runpod_serverless, ...")
    gen.add_argument("--prompt", default=None, help="Scene text. The trigger is prefixed for you.")
    gen.add_argument("--set", dest="set_name", default=None, help="Template set name.")
    gen.add_argument("--template-id", default=None, help="Template id inside the set.")
    gen.add_argument("--seed", type=int, default=None)
    gen.set_defaults(func=_cmd_generate)

    batch = sub.add_parser("batch", help="Generate a resumable unattended batch.")
    _add_avatar(batch)
    batch.add_argument("--set", dest="set_name", required=True, help="Template set, or 'all'.")
    batch.add_argument("--count", type=int, required=True)
    batch.add_argument("--preset", default=None)
    batch.add_argument("--backend", default=None)
    batch.add_argument("--batch-id", default="batch")
    batch.add_argument("--seed-base", type=int, default=None)
    batch.add_argument("--day", default=None, help="YYYY-MM-DD folder. Default: today.")
    batch.add_argument("--max-attempts", type=int, default=3)
    batch.add_argument("--interval", type=float, default=None, help="Seconds between jobs.")
    batch.add_argument("--templates", type=Path, default=None)
    batch.add_argument("--no-resume", action="store_true")
    batch.set_defaults(func=_cmd_batch)

    download = sub.add_parser("download-models", help="Download Civitai versions listed on the avatar.")
    _add_avatar(download)
    download.set_defaults(func=_cmd_download)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging()
    return int(args.func(args))


def _add_avatar(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--avatar", required=True, help="Avatar folder id, for example sophie_bennet.")


def _cmd_list(_args: argparse.Namespace) -> int:
    for avatar_id in list_avatar_ids():
        avatar = load_avatar(avatar_id)
        presets = ", ".join(sorted(avatar.presets))
        print(f"{avatar.id}\t{avatar.identity.trigger}\t{presets}")
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    result = render(
        args.avatar,
        preset=args.preset,
        prompt=args.prompt,
        set_name=args.set_name,
        template_id=args.template_id,
        seed=args.seed,
        backend_name=args.backend,
    )
    print(result.path)
    return 0


def _cmd_batch(args: argparse.Namespace) -> int:
    report = run_batch(
        args.avatar,
        set_name=args.set_name,
        count=args.count,
        preset=args.preset,
        batch_id=args.batch_id,
        backend=get_backend(args.backend),
        seed_base=args.seed_base,
        day=args.day,
        resume=not args.no_resume,
        max_attempts=args.max_attempts,
        interval_s=args.interval,
        templates_path=args.templates,
    )
    print(
        f"planned={report.planned} completed={report.completed} "
        f"skipped={report.skipped} failed={report.failed}"
    )
    print(report.batch_dir)
    return 0 if report.failed == 0 else 1


def _cmd_download(args: argparse.Namespace) -> int:
    avatar = load_avatar(args.avatar)
    saved = CivitaiClient().download_avatar_assets(avatar)
    for path in saved:
        print(path)
    if not saved:
        print("No numeric Civitai version ids to download.")
    return 0


def _configure_logging() -> None:
    from utils.pipeline_paths import pipeline_logs_dir

    logger = logging.getLogger("models_avatar")
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    try:
        log_path = pipeline_logs_dir() / "models_avatar.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
    except OSError:
        logger.propagate = False
        return
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.propagate = False
