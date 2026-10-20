"""Scheduler service that replaces the manual Pinterest drip loop.

One wake publishes ``PINTEREST_PINS_PER_RUN`` image pins (default 1) for
every active row in ``config/pinterest_accounts.yaml``. Media comes from
B2 one object at a time. Video files are skipped. Before the first publish
the process sleeps until the newest PinLedger ``published_utc`` plus one
cadence draw (``MIN_INTERVAL_HOURS`` to ``MAX_INTERVAL_HOURS``, default
3–6). If that moment is already past, it publishes immediately. An empty
or unreachable ledger keeps the previous random 3–6h startup delay.
Between wakes it still sleeps a fresh random gap in that range.
``--once`` and ``--dry-run`` skip the initial delay. ``--dry-run`` builds
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
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from omni.models import Account  # noqa: E402
from omni.pinterest_accounts import active_pinterest_accounts  # noqa: E402

log = logging.getLogger("omni.worker")

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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_published_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_account_ledger(account: Account) -> tuple[list[dict], bool]:
    from agents.posting.pinterest_engine.ledger import PinLedger  # noqa: PLC0415

    return PinLedger(account.account_id).read_rows()


def newest_pin_published(
    accounts: Sequence[Account] | None = None,
    *,
    read_rows: Callable[[Account], tuple[list[dict], bool]] | None = None,
) -> datetime | None:
    """Newest ``published_utc`` across active accounts.

    ``None`` when every ledger is empty, or when any ledger is unreachable.
    A partial read is not a safe resume point: the missing account may hold
    the newest pin.
    """
    rows_of = read_rows or _read_account_ledger
    chosen = list(accounts) if accounts is not None else active_pinterest_accounts()
    if not chosen:
        return None
    newest: datetime | None = None
    for account in chosen:
        try:
            rows, reachable = rows_of(account)
        except Exception:  # noqa: BLE001 — one bad ledger must not publish early
            log.info("Pin ledger unreachable for %s", account.account_id)
            return None
        if not reachable:
            log.info("Pin ledger unreachable for %s", account.account_id)
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            stamp = _parse_published_utc(row.get("published_utc"))
            if stamp is not None and (newest is None or stamp > newest):
                newest = stamp
    return newest


def startup_delay_seconds(
    published_at: datetime | None,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> float:
    """Seconds to wait before the first publish.

    ``published_at`` is the newest pin time, or ``None`` when the ledger is
    empty or unreachable. ``None`` keeps the random 3–6h delay. Otherwise
    the wait is whatever remains of one cadence draw after that pin.
    """
    cadence = interval_seconds(rng)
    if published_at is None:
        return cadence
    moment = now or _utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    stamp = published_at
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    remaining = (stamp + timedelta(seconds=cadence) - moment).total_seconds()
    return max(0.0, remaining)


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
    from omni.pinterest_drip import publish_account

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
    # Resume from the last pin. An empty or unreachable ledger keeps the
    # random startup delay so a restart cannot publish early by accident.
    if not once and not dry_run:
        published_at = newest_pin_published()
        pause = startup_delay_seconds(published_at)
        if published_at is None:
            log.info(
                "Pin ledger empty or unreachable; initial delay %.0f minutes",
                pause / 60.0,
            )
        elif pause <= 0:
            log.info(
                "Last pin at %s is already past the cadence; publishing without the startup delay",
                published_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        else:
            log.info(
                "Resuming from last pin at %s; initial delay %.0f minutes",
                published_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                pause / 60.0,
            )
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
