"""Scheduler service that replaces the manual Pinterest drip loop.

One wake publishes ``PINTEREST_PINS_PER_RUN`` image pins (default 1) for
every active row in ``config/pinterest_accounts.yaml``. Media comes from
B2 one object at a time. Video files are skipped. Before the first publish,
and between wakes, the process sleeps a random gap between
``MIN_INTERVAL_HOURS`` and ``MAX_INTERVAL_HOURS`` (default 3–6).
``--once`` and ``--dry-run`` skip that initial delay. ``--dry-run`` builds
the cards and does not call Pinterest.

YouTube scheduling stays on ``main.py --publish-youtube`` (``publishAt``).
This worker does not upload videos on a timer.

The ``engine`` role stays up so ``docker compose exec engine python main.py ...``
has a process to attach to. It does not generate content by itself.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import signal
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.publishing.models import Account  # noqa: E402
from modules.accounts.pinterest_accounts import active_pinterest_accounts  # noqa: E402

log = logging.getLogger("core.worker")

_DEFAULT_MIN_HOURS = 3.0
_DEFAULT_MAX_HOURS = 6.0


def _hours(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("Ignoring non-numeric %s=%r", name, raw)
        return default


def interval_seconds(rng: random.Random | None = None) -> float:
    """Random seconds in ``[MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS]``."""
    min_h = _hours("MIN_INTERVAL_HOURS", _DEFAULT_MIN_HOURS)
    max_h = _hours("MAX_INTERVAL_HOURS", _DEFAULT_MAX_HOURS)
    if max_h < min_h:
        min_h, max_h = max_h, min_h
    if max_h == min_h:
        return min_h * 3600.0
    roll = (rng or random).random()
    return (min_h + (max_h - min_h) * roll) * 3600.0


def pins_per_wake() -> int:
    raw = (os.getenv("PINTEREST_PINS_PER_RUN") or "1").strip()
    try:
        value = int(raw)
    except ValueError:
        return 1
    return value if value > 0 else 1


def run_pinterest_drip(
    account: Account,
    *,
    pins: int | None = None,
    dry_run: bool = False,
) -> dict:
    """One image-card wake for ``account``. Videos and filename titles are skipped."""
    from core.publishing.pinterest_drip import publish_account

    quantity = pins if pins is not None else pins_per_wake()
    return publish_account(account, pins=quantity, dry_run=dry_run)


def run_cycle(*, dry_run: bool = False) -> list[dict]:
    """Publish once for each active Pinterest account. Inactive rows are skipped."""
    accounts = active_pinterest_accounts()
    if not accounts:
        log.warning("No active Pinterest accounts in config/pinterest_accounts.yaml")
        return []
    results: list[dict] = []
    for account in accounts:
        log.info(
            "Pinterest drip | branding=%s account=%s dry_run=%s",
            account.branding,
            account.account_id,
            dry_run,
        )
        try:
            stats = run_pinterest_drip(account, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 — one account must not kill the loop
            log.exception("Pinterest drip failed for %s", account.branding)
            stats = {"published": 0, "errors": 1, "error": str(exc)}
        results.append(
            {
                "branding": account.branding,
                "account_id": account.account_id,
                "stats": stats,
            }
        )
    return results


def _sleep_until(seconds: float, stop: dict) -> None:
    deadline = time.monotonic() + seconds
    while not stop["flag"] and time.monotonic() < deadline:
        time.sleep(min(5.0, deadline - time.monotonic()))


def run_one_account(account_id: str, *, pins: int, dry_run: bool) -> dict:
    """Publish or dry-run a single account and return. Does not start the scheduler."""
    wanted = account_id.strip()
    account = next(
        (
            row
            for row in active_pinterest_accounts()
            if row.account_id == wanted or row.branding == wanted
        ),
        None,
    )
    if account is None:
        log.error("No active Pinterest account %s", wanted)
        raise SystemExit(2)
    log.info(
        "Single account run | account=%s pins=%d dry_run=%s",
        account.account_id,
        pins,
        dry_run,
    )
    stats = run_pinterest_drip(account, pins=pins, dry_run=dry_run)
    log.info(
        "Single account done | account=%s published=%s skipped=%s errors=%s",
        account.account_id,
        stats.get("published"),
        stats.get("skipped"),
        stats.get("errors"),
    )
    return stats


def run_scheduler(*, once: bool = False, dry_run: bool = False) -> None:
    stop = {"flag": False}

    def _stop(signum, _frame) -> None:
        log.info("Signal %s — finishing the current pin, then exiting", signum)
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log.info(
        "Scheduler up | pins_per_wake=%s interval=%s-%sh dry_run=%s",
        pins_per_wake(),
        os.getenv("MIN_INTERVAL_HOURS", "3"),
        os.getenv("MAX_INTERVAL_HOURS", "6"),
        dry_run,
    )
    # A fresh container must not publish in the same minute it starts.
    if not once and not dry_run:
        pause = interval_seconds()
        log.info("Initial delay before the first publish: %.0f minutes", pause / 60.0)
        _sleep_until(pause, stop)
        if stop["flag"]:
            return
    while not stop["flag"]:
        run_cycle(dry_run=dry_run)
        if once or stop["flag"]:
            return
        pause = interval_seconds()
        log.info("Next drip in %.0f minutes", pause / 60.0)
        _sleep_until(pause, stop)


def run_engine() -> None:
    """Idle parent process for ad-hoc ``docker compose exec`` factory runs."""
    stop = {"flag": False}

    def _stop(signum, _frame) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info(
        "Engine idle. Generate with: "
        "docker compose exec engine python main.py --channel <id> --post-type ..."
    )
    while not stop["flag"]:
        time.sleep(60)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Omni-Engine worker")
    parser.add_argument(
        "--role",
        choices=("scheduler", "engine"),
        default=os.getenv("OMNI_ROLE", "scheduler"),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scheduler wake and exit (no sleep loop).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build image cards and log the pins. Do not call Pinterest or record them.",
    )
    parser.add_argument(
        "--account",
        default=None,
        help="Run one Pinterest account (for example ancient_knowledge) and exit.",
    )
    parser.add_argument(
        "--pins",
        type=int,
        default=None,
        help="How many pins --account renders. Dry-run defaults to 5.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
    )
    if args.account:
        pins = args.pins if args.pins is not None else (5 if args.dry_run else pins_per_wake())
        run_one_account(args.account, pins=pins, dry_run=args.dry_run)
        return
    if args.role == "engine":
        run_engine()
        return
    run_scheduler(once=args.once, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
