# -*- coding: utf-8 -*-
"""Archive of the Supermemory volume, uploaded to Backblaze B2.

Not scheduled. No cron on the VM calls this backup. Until scheduling is
decided, run ``/opt/omni-engine/scripts/backup_supermemory.sh`` by hand.
Deploy copies that script from ``deploy/vm/backup_supermemory.sh`` and
does not run it. The shell script calls this module.

One object per UTC day: ``backups/supermemory/YYYY-MM-DD.tgz``.
An object that is already in B2 is left untouched. This script never
deletes anything in B2. Seven local archives are kept; older local files
are removed. A daily B2 budget error skips the upload and keeps the
local archive.

The container is stopped only when it is already running, then started
again. A stopped service stays stopped.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_LOG = logging.getLogger("supermemory.backup")
B2_PREFIX = "backups/supermemory/"
LOCAL_KEEP = 7
DEFAULT_VOLUME = "omni-engine_supermemory-data"
DEFAULT_IMAGE = "ghcr.io/diogolean/supermemory-server:0.0.9"


def archive_name(day: str) -> str:
    return f"{day}.tgz"


def b2_key(day: str) -> str:
    return f"{B2_PREFIX}{archive_name(day)}"


def default_backup_dir() -> Path:
    override = (os.getenv("SUPERMEMORY_BACKUP_DIR") or "").strip()
    if override:
        return Path(override)
    from utils.pipeline_paths import outputs_root

    return outputs_root() / "backups" / "supermemory"


def prune_local(directory: Path, *, keep: int = LOCAL_KEEP) -> list[Path]:
    """Delete local archives beyond *keep*. Does not touch B2."""
    files = sorted(path for path in directory.glob("*.tgz") if path.is_file())
    if keep < 0:
        keep = 0
    stale = files[:-keep] if keep else list(files)
    removed: list[Path] = []
    for path in stale:
        path.unlink()
        removed.append(path)
    return removed


def upload_archive(
    path: Path,
    *,
    day: str,
    existing_keys: list[str] | None = None,
    upload=None,
) -> str:
    """Upload one archive. Skip when that day's object already exists."""
    key = b2_key(day)
    if existing_keys is None:
        from agents.media.b2_client import list_object_keys

        existing_keys = list_object_keys(B2_PREFIX)
    if key in set(existing_keys):
        _LOG.info("B2 already has %s; leaving it in place", key)
        return "skipped"
    if upload is None:
        from agents.media.b2_client import upload_object

        def _put(object_key: str, source: Path) -> None:
            upload_object(object_key, source, content_type="application/gzip")

        upload = _put

    upload(key, path)
    _LOG.info("uploaded %s", key)
    return "uploaded"


def _run(argv: list[str]) -> str:
    completed = subprocess.run(argv, check=True, capture_output=True, text=True)
    return completed.stdout or ""


def service_is_running(runner, service: str = "supermemory") -> bool:
    output = runner(
        [
            "docker",
            "compose",
            "--profile",
            "memory",
            "ps",
            "--status",
            "running",
            "-q",
            service,
        ]
    )
    return bool(str(output or "").strip())


def create_archive(
    dest: Path,
    *,
    runner,
    volume: str,
    image: str,
    service_was_running: bool,
) -> None:
    """Tar the volume. Restart the service only if this call stopped it."""
    stopped = False
    try:
        if service_was_running:
            runner(["docker", "compose", "--profile", "memory", "stop", "supermemory"])
            stopped = True
        dest.parent.mkdir(parents=True, exist_ok=True)
        runner(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "tar",
                "-v",
                f"{volume}:/data:ro",
                "-v",
                f"{dest.parent.resolve()}:/backup",
                image,
                "-C",
                "/data",
                "-czf",
                f"/backup/{dest.name}",
                ".",
            ]
        )
    finally:
        if stopped:
            runner(["docker", "compose", "--profile", "memory", "up", "-d", "supermemory"])


def backup_once(
    *,
    day: str,
    backup_dir: Path,
    runner,
    volume: str = DEFAULT_VOLUME,
    image: str = DEFAULT_IMAGE,
    existing_keys: list[str] | None = None,
    upload=None,
    keep: int = LOCAL_KEEP,
) -> str:
    running = service_is_running(runner)
    dest = backup_dir / archive_name(day)
    create_archive(
        dest,
        runner=runner,
        volume=volume,
        image=image,
        service_was_running=running,
    )
    prune_local(backup_dir, keep=keep)
    return upload_archive(dest, day=day, existing_keys=existing_keys, upload=upload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Back up the Supermemory volume to B2.")
    parser.add_argument("--day", default="", help="UTC day YYYY-MM-DD. Default is today.")
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--volume", default=os.getenv("SUPERMEMORY_VOLUME") or DEFAULT_VOLUME)
    parser.add_argument("--image", default=os.getenv("SUPERMEMORY_IMAGE") or DEFAULT_IMAGE)
    parser.add_argument(
        "--upload-only",
        action="store_true",
        help="Upload an archive that already exists. Does not call Docker.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    day = (args.day or datetime.now(timezone.utc).strftime("%Y-%m-%d")).strip()
    backup_dir = args.backup_dir or default_backup_dir()
    if args.upload_only:
        try:
            prune_local(backup_dir)
            status = upload_archive(backup_dir / archive_name(day), day=day)
        except Exception as exc:
            from agents.media.b2_budget import B2DailyCapError

            if isinstance(exc, B2DailyCapError):
                _LOG.warning("B2 daily budget reached; local archive kept, nothing removed from B2")
                return 2
            _LOG.warning("supermemory upload failed (%s)", type(exc).__name__)
            return 1
        _LOG.info("supermemory backup %s for %s", status, day)
        return 0
    try:
        status = backup_once(
            day=day,
            backup_dir=backup_dir,
            runner=_run,
            volume=args.volume,
            image=args.image,
        )
    except Exception as exc:
        from agents.media.b2_budget import B2DailyCapError

        if isinstance(exc, B2DailyCapError):
            _LOG.warning("B2 daily budget reached; local archive kept, nothing removed from B2")
            return 2
        _LOG.warning("supermemory backup failed (%s)", type(exc).__name__)
        return 1
    _LOG.info("supermemory backup %s for %s", status, day)
    return 0


if __name__ == "__main__":
    sys.exit(main())
