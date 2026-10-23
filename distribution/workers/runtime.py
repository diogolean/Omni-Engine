"""Scheduler service that replaces the manual Pinterest drip loop.

One wake publishes ``PINTEREST_PINS_PER_RUN`` image pins (default 1) for
every active row in ``config/pinterest_accounts.yaml``, and never more
than ``PINTEREST_PINS_PER_DAY`` (default 4) once pins already recorded
that calendar day are counted. The day is ``PINTEREST_DAY_TZ`` (default
``America/Sao_Paulo``). Rows with ``seeded=True`` are board history and
do not count, and they do not move the resume point. Media comes from B2
one object at a time. Video files are skipped. Before the first publish
the process sleeps until the newest real PinLedger ``published_utc`` plus
one cadence draw (``MIN_INTERVAL_HOURS`` to ``MAX_INTERVAL_HOURS``,
default 3–6). If that moment is already past, it publishes immediately,
still under the daily cap and the shared ledger duplicate check. A
``published_utc`` in the
future cannot stretch that wait past ``MAX_INTERVAL_HOURS``. An empty or
unreachable ledger keeps the previous random 3–6h startup delay.
The chosen wake is written per channel to ``outputs/scheduler/next_wake.json``
on the data volume. A restart reuses that clock when it is still ahead,
publishes once if the wake was missed by at most 30 minutes, and only
then draws a new gap. A ledger pin at or after that wake minus 5 minutes
is the slot already posted, so the process waits out the cadence instead
of posting it again. Between wakes it still sleeps a fresh random gap
in that range. A wake file that cannot be written is logged and ignored;
the process keeps the in-memory delay. The running process keeps the
``MIN_INTERVAL_HOURS`` / ``MAX_INTERVAL_HOURS`` it started with when a
channel ``.env`` reloads.
``--once`` and ``--dry-run`` skip the initial delay. ``--dry-run`` builds
the cards and does not call Pinterest.

YouTube scheduling stays on ``main.py --publish-youtube`` (``publishAt``).
This worker does not upload videos on a timer.

The ``engine`` role stays up so ``docker compose exec engine python main.py ...``
has a process to attach to. It does not generate content by itself.
"""

from __future__ import annotations

import argparse
import inspect
import json
import logging
import os
import random
import signal
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from accounts.pinterest import active_pinterest_accounts  # noqa: E402
from distribution.publishers.base import Account  # noqa: E402
from shared.paths import outputs_root  # noqa: E402

log = logging.getLogger("omni.worker")

_DEFAULT_MIN_HOURS = 3.0
_DEFAULT_MAX_HOURS = 6.0
_DEFAULT_DAY_TZ = "America/Sao_Paulo"
# A deploy that lands during the wake still publishes that wake once.
_WAKE_GRACE = timedelta(minutes=30)
# A pin this close to the saved wake is that slot, already in the ledger.
_CATCHUP_MARGIN = timedelta(minutes=5)


def _hours(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("Ignoring non-numeric %s=%r", name, raw)
        return default


# Set for the life of one ``run_scheduler`` call. A drip reloads ``.env`` with
# override, and that must not move the next wake off the cadence this process
# started with.
_bound_cadence: tuple[float, float] | None = None


def _interval_bounds() -> tuple[float, float]:
    """``(min_hours, max_hours)`` for the next gap.

    Inside ``run_scheduler`` this is the pair captured at startup. Outside it,
    it is the current ``MIN_INTERVAL_HOURS`` / ``MAX_INTERVAL_HOURS``.
    """
    if _bound_cadence is not None:
        return _bound_cadence
    min_h = _hours("MIN_INTERVAL_HOURS", _DEFAULT_MIN_HOURS)
    max_h = _hours("MAX_INTERVAL_HOURS", _DEFAULT_MAX_HOURS)
    if max_h < min_h:
        min_h, max_h = max_h, min_h
    return min_h, max_h


def interval_seconds(rng: random.Random | None = None) -> float:
    """Random seconds in ``[MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS]``."""
    min_h, max_h = _interval_bounds()
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
    from distribution.scheduling.ledger import PinLedger  # noqa: PLC0415

    return PinLedger(account.account_id).read_rows()


def _is_seeded(row: dict) -> bool:
    """Board history copied into the ledger, not a pin this process published."""
    return row.get("seeded") is True


def newest_pin_published(
    accounts: Sequence[Account] | None = None,
    *,
    read_rows: Callable[[Account], tuple[list[dict], bool]] | None = None,
) -> datetime | None:
    """Newest real ``published_utc`` across active accounts.

    Rows with ``seeded=True`` are ignored, including older duplicates of the
    same pin id. ``None`` when every ledger is empty, when the only stamps
    are seeded, or when any ledger is unreachable. A partial read is not a
    safe resume point: the missing account may hold the newest pin.
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
            if not isinstance(row, dict) or _is_seeded(row):
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
    A stamp in the future (clock skew or a bad row) cannot push the wait
    past ``MAX_INTERVAL_HOURS``.
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
    ceiling = max(0.0, _interval_bounds()[1]) * 3600.0
    return min(max(0.0, remaining), ceiling)


def scheduler_state_path() -> Path:
    """Per-channel wake clock on the outputs volume. This file is not in git."""
    return outputs_root() / "scheduler" / "next_wake.json"


def _format_utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scheduler_channel_ids() -> list[str]:
    """Active Pinterest channel ids. Empty when the account file cannot be read."""
    try:
        accounts = active_pinterest_accounts()
    except Exception:  # noqa: BLE001 — a bad yaml must not erase the wake clock
        log.info("Pinterest accounts unreadable; using the shared scheduler wake")
        return []
    found: list[str] = []
    for account in accounts:
        channel = str(account.account_id or "").strip()
        if channel and channel not in found:
            found.append(channel)
    return found


def _load_wake_state() -> dict[str, str]:
    path = scheduler_state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        log.info("Ignoring unreadable scheduler wake file %s", path)
        return {}
    channels = raw.get("channels") if isinstance(raw, dict) else None
    if not isinstance(channels, dict):
        return {}
    loaded: dict[str, str] = {}
    for key, value in channels.items():
        if isinstance(key, str) and isinstance(value, str) and key.strip():
            loaded[key] = value
    return loaded


def _write_wake_state(channels: dict[str, str]) -> None:
    """Persist the wake clock. A full or read-only volume must not kill the loop."""
    path = scheduler_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"channels": dict(sorted(channels.items()))}
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        log.warning("Could not save scheduler wake to %s: %s", path, exc)


def load_channel_wake(channel: str) -> datetime | None:
    """Saved ``next_wake_utc`` for ``channel``, or ``None`` when it is absent."""
    return _parse_published_utc(_load_wake_state().get(channel))


def save_channel_wake(channel: str, when: datetime) -> None:
    """Remember ``when`` as this channel's next wake without dropping the others."""
    state = _load_wake_state()
    state[channel] = _format_utc(when)
    _write_wake_state(state)


def _save_wake_updates(state: dict[str, str], updates: dict[str, datetime]) -> None:
    if not updates:
        return
    for channel, when in updates.items():
        state[channel] = _format_utc(when)
    _write_wake_state(state)


def remember_next_wake(when: datetime) -> None:
    """Point every active channel at the same following wake."""
    channels = _scheduler_channel_ids() or ["scheduler"]
    _save_wake_updates(_load_wake_state(), {channel: when for channel in channels})


def _reader_accepts_accounts(reader: Callable) -> bool:
    """False for the zero-argument ledger stubs the scheduler tests install."""
    try:
        parameters = inspect.signature(reader).parameters
    except (TypeError, ValueError):
        return False
    if any(item.kind == inspect.Parameter.VAR_POSITIONAL for item in parameters.values()):
        return True
    positional = [
        item
        for item in parameters.values()
        if item.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    return len(positional) >= 1


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _published_for_channel(channel: str, published_at: datetime | None) -> datetime | None:
    """Newest real pin for one channel.

    ``published_at`` is the stamp startup already read. It stands in when
    this channel has no account row, or when the ledger reader cannot take
    an account list.
    """
    if not _reader_accepts_accounts(newest_pin_published):
        return published_at
    try:
        accounts = [
            account
            for account in active_pinterest_accounts()
            if str(account.account_id or "").strip() == channel
        ]
    except Exception:  # noqa: BLE001 — a bad yaml still has the startup stamp
        log.info("Pinterest accounts unreadable; using the shared ledger stamp")
        return published_at
    if not accounts:
        return published_at
    return newest_pin_published(accounts)


def _pin_covers_saved_wake(
    channel: str,
    saved: datetime,
    published_at: datetime | None,
) -> datetime | None:
    """Ledger stamp when this channel already published the saved slot."""
    stamp = _published_for_channel(channel, published_at)
    if stamp is None:
        return None
    stamp = _as_utc(stamp)
    if stamp < _as_utc(saved) - _CATCHUP_MARGIN:
        return None
    return stamp


def resolve_startup_delay(
    published_at: datetime | None,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> tuple[float, str]:
    """Seconds to sleep before the first publish, and why.

    ``reuse`` keeps a saved wake that is still ahead. ``missed`` means a
    saved wake fell inside the 30-minute grace and the ledger has no pin
    for it, so this process publishes once and the following wake is
    stored first. A pin within 5 minutes before that wake is the slot, and
    the wait is a fresh gap from the pin (``draw``). ``due`` means the
    ledger cadence has already elapsed and there is nothing saved to reuse.
    ``draw`` is a fresh gap, the same rule as a process with no wake file.
    """
    moment = now or _utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    channels = _scheduler_channel_ids() or ["scheduler"]
    state = _load_wake_state()
    grace = _WAKE_GRACE.total_seconds()
    future: list[datetime] = []
    missed: list[str] = []
    fresh: list[str] = []
    for channel in channels:
        saved = _parse_published_utc(state.get(channel))
        if saved is not None and saved > moment:
            future.append(saved)
            continue
        if saved is not None and (moment - saved).total_seconds() <= grace:
            missed.append(channel)
            continue
        fresh.append(channel)

    updates: dict[str, datetime] = {}
    drawn: datetime | None = None
    if fresh and not missed:
        delay = startup_delay_seconds(published_at, now=moment, rng=rng)
        if delay <= 0:
            missed.extend(fresh)
            fresh = []
        else:
            drawn = moment + timedelta(seconds=delay)
            for channel in fresh:
                updates[channel] = drawn
            future.append(drawn)
    if missed:
        covered: list[datetime] = []
        pending: list[str] = []
        for channel in missed:
            saved = _parse_published_utc(state.get(channel))
            stamp = (
                _pin_covers_saved_wake(channel, saved, published_at) if saved is not None else None
            )
            if stamp is None:
                pending.append(channel)
            else:
                covered.append(stamp)
        if not pending:
            # The deploy restarted after the pin landed and before the next
            # wake was saved. Wait out the cadence from that pin.
            anchor = max(covered)
            delay = startup_delay_seconds(anchor, now=moment, rng=rng)
            log.info(
                "Pin at %s is within %.0f minutes of the saved wake; not repeating that slot",
                _format_utc(anchor),
                _CATCHUP_MARGIN.total_seconds() / 60.0,
            )
            if delay <= 0:
                follow = moment + timedelta(seconds=interval_seconds(rng))
                for channel in missed:
                    updates[channel] = follow
                _save_wake_updates(state, updates)
                return 0.0, "due"
            drawn_wake = moment + timedelta(seconds=delay)
            for channel in missed:
                updates[channel] = drawn_wake
            future.append(drawn_wake)
            _save_wake_updates(state, updates)
            soonest = min(future)
            reason = "draw" if soonest == drawn_wake else "reuse"
            return max(0.0, (soonest - moment).total_seconds()), reason
        # Store the next wake before publishing. A second start then waits
        # instead of posting this slot again. Read the saved stamps first:
        # the write below replaces them in ``state``.
        had_saved_wake = any(
            _parse_published_utc(state.get(channel)) is not None for channel in pending
        )
        follow = moment + timedelta(seconds=interval_seconds(rng))
        for channel in missed:
            updates[channel] = follow
        _save_wake_updates(state, updates)
        return 0.0, "missed" if had_saved_wake else "due"
    if updates:
        _save_wake_updates(state, updates)
    if not future:
        delay = startup_delay_seconds(published_at, now=moment, rng=rng)
        wake = moment + timedelta(seconds=max(0.0, delay))
        _save_wake_updates(state, {channel: wake for channel in channels})
        return max(0.0, delay), "draw"
    soonest = min(future)
    reason = "draw" if drawn is not None and soonest == drawn else "reuse"
    return max(0.0, (soonest - moment).total_seconds()), reason


def _pins_per_day() -> int:
    raw = (os.getenv("PINTEREST_PINS_PER_DAY") or "4").strip()
    try:
        value = int(raw)
    except ValueError:
        return 4
    return value if value > 0 else 4


def pins_per_wake() -> int:
    raw = (os.getenv("PINTEREST_PINS_PER_RUN") or "1").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 1
    if value <= 0:
        value = 1
    return min(value, _pins_per_day())


def _day_timezone() -> ZoneInfo:
    """Calendar day for ``PINTEREST_PINS_PER_DAY``. Default America/Sao_Paulo."""
    name = (os.getenv("PINTEREST_DAY_TZ") or "").strip() or _DEFAULT_DAY_TZ
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        log.warning("Ignoring unknown PINTEREST_DAY_TZ=%r; using %s", name, _DEFAULT_DAY_TZ)
        return ZoneInfo(_DEFAULT_DAY_TZ)


def _pins_recorded_on(rows: Sequence[object], day, tz: ZoneInfo) -> int:
    count = 0
    for row in rows:
        if not isinstance(row, dict) or row.get("pin_recorded") is False or _is_seeded(row):
            continue
        stamp = _parse_published_utc(row.get("published_utc"))
        if stamp is not None and stamp.astimezone(tz).date() == day:
            count += 1
    return count


def wake_pin_allowance(
    account: Account,
    *,
    now: datetime | None = None,
    read_rows: Callable[[Account], tuple[list[dict], bool]] | None = None,
) -> int:
    """How many pins this wake may publish for ``account``.

    The request cannot exceed ``PINTEREST_PINS_PER_DAY``. Rows already
    recorded on the current calendar day in ``PINTEREST_DAY_TZ`` (default
    America/Sao_Paulo) consume that budget. Seeded board rows do not. When
    the ledger cannot be read, that count is unknown and only the per-wake
    cap applies.
    """
    requested = pins_per_wake()
    moment = now or _utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    rows_of = read_rows or _read_account_ledger
    try:
        rows, reachable = rows_of(account)
    except Exception:  # noqa: BLE001 — unknown ledger must not add a full batch
        log.info("Pin ledger unreadable for %s; keeping the per-wake cap", account.account_id)
        return requested
    if not reachable:
        log.info("Pin ledger unreadable for %s; keeping the per-wake cap", account.account_id)
        return requested
    tz = _day_timezone()
    used = _pins_recorded_on(rows, moment.astimezone(tz).date(), tz)
    return max(0, min(requested, _pins_per_day() - used))


def run_pinterest_drip(
    account: Account,
    *,
    pins: int | None = None,
    dry_run: bool = False,
) -> dict:
    """One image-card wake for ``account``. Videos and filename titles are skipped."""
    from distribution.scheduling.drip import publish_account

    if pins is None:
        quantity = wake_pin_allowance(account)
        if quantity <= 0:
            log.info(
                "Pinterest daily cap reached for %s; this wake publishes nothing",
                account.account_id,
            )
            return {
                "published": 0,
                "skipped": 0,
                "errors": 0,
                "remaining": 0,
                "skipped_reason": "daily_cap",
            }
    else:
        quantity = pins
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
    """One scheduler process. The cadence is fixed at entry.

    ``publish_account`` reloads the channel ``.env`` with override. That file
    often still has the example ``MIN_INTERVAL_HOURS=3`` / ``MAX_INTERVAL_HOURS=6``.
    Binding the pair here keeps missed wakes, catch-up wakes, and the gap
    after an empty inventory on the hours this process was started with.
    """
    global _bound_cadence
    min_h = _hours("MIN_INTERVAL_HOURS", _DEFAULT_MIN_HOURS)
    max_h = _hours("MAX_INTERVAL_HOURS", _DEFAULT_MAX_HOURS)
    if max_h < min_h:
        min_h, max_h = max_h, min_h
    _bound_cadence = (min_h, max_h)
    try:
        _run_scheduler(once=once, dry_run=dry_run, min_hours=min_h, max_hours=max_h)
    finally:
        _bound_cadence = None


def _run_scheduler(
    *,
    once: bool,
    dry_run: bool,
    min_hours: float,
    max_hours: float,
) -> None:
    stop = {"flag": False}

    def _stop(signum, _frame) -> None:
        log.info("Signal %s — finishing the current pin, then exiting", signum)
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log.info(
        "Scheduler up | pins_per_wake=%s interval=%g-%gh dry_run=%s",
        pins_per_wake(),
        min_hours,
        max_hours,
        dry_run,
    )
    # Resume from the saved wake when it is still ahead. Otherwise the last
    # pin, or a random gap when the ledger is empty or unreachable.
    if not once and not dry_run:
        published_at = newest_pin_published()
        pause, reason = resolve_startup_delay(published_at)
        if reason == "reuse":
            log.info("Reusing saved wake; initial delay %.0f minutes", pause / 60.0)
        elif reason == "missed":
            log.info(
                "Saved wake was missed within %.0f minutes; publishing that wake once",
                _WAKE_GRACE.total_seconds() / 60.0,
            )
        elif published_at is None:
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
        if not dry_run:
            remember_next_wake(_utc_now() + timedelta(seconds=pause))
        log.info(
            "Next drip in %.0f minutes (interval=%g-%gh)",
            pause / 60.0,
            min_hours,
            max_hours,
        )
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
