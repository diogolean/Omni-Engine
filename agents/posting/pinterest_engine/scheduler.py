# -*- coding: utf-8 -*-
"""
pinterest_engine.scheduler
---------------------------
Human-Mimic Safe-Drip Scheduler.

Publishes 3-5 pins per session with randomised intervals between each pin
to mimic authentic human posting behaviour (avoids algorithmic spam flags).

Key behaviours:
  - Reads exclusively from master_inventory.json (never posts twice)
  - Checks outputs/scheduled_history.txt BEFORE building each batch so that
    even if master_inventory is rebuilt, no filename is ever re-posted
  - Updates posted_on_pinterest + pinterest_pin_id IMMEDIATELY after success
  - Saves master_inventory.json after EVERY pin (crash-safe)
  - Appends to scheduled_history.txt after every confirmed live publish
  - Sleeps get_random_interval() minutes between pins (real sleep)
  - On 401 token expiry: logs, saves ledger, raises PinterestTokenExpiredError
  - --no-wait flag skips sleep (for testing / dry runs)
  - --dry-run logs without writing to history (history stays clean for tests)

Interval config (in .env):
    MIN_INTERVAL_HOURS  default 3
    MAX_INTERVAL_HOURS  default 6
"""
from __future__ import annotations

import logging
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.posting.pinterest_engine import config as cfg  # noqa: E402

log = logging.getLogger(__name__)

_DEFAULT_MIN_HOURS = 3.0
_DEFAULT_MAX_HOURS = 6.0
_DEFAULT_PINS_MIN = 3
_DEFAULT_PINS_MAX = 5
_INTER_PIN_COURTESY_SEC = 3   # brief pause before starting sleep countdown


def _write_dry_run_card(account_id: str, post_id: str, pin_bytes: bytes) -> Path:
    """Save a dry-run card under outputs/pinterest/dryrun/ for inspection."""
    from utils.pipeline_paths import outputs_root  # noqa: PLC0415

    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{account_id}_{post_id}")
    if not safe.lower().endswith((".jpg", ".jpeg")):
        safe += ".jpg"
    dest = outputs_root() / "pinterest" / "dryrun" / safe
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pin_bytes)
    return dest

# ---------------------------------------------------------------------------
# FilenameHistory  --  the filename-level duplicate guard
# ---------------------------------------------------------------------------
# Stored at outputs/scheduled_history.txt.
# Format (one line per published pin):
#   2026-07-03T15:04:00Z | source_filename.jpg | post_id | pin_id | topic
# The *filename* column is what is checked; all other columns are for auditing.

_HISTORY_FILENAME = "scheduled_history.txt"
_HISTORY_SEP = " | "


class FilenameHistory:
    """
    Persistent filename-based guard against accidental re-scheduling.

    Two independent protection layers exist in the engine:
      1. master_inventory.json  ::  posted_on_pinterest flag (per post_id)
      2. scheduled_history.txt  ::  filename set          (per source file)

    This class owns layer 2.  It is intentionally file-based so it
    survives inventory rebuilds, syncs, or any other operation that
    touches master_inventory.json.

    Dry-run sessions do NOT write to this file so test runs never
    consume real history slots.
    """

    def __init__(self, outputs_dir: Path) -> None:
        self.path = outputs_dir / _HISTORY_FILENAME
        try:
            from modules.durable_store import hydrate_state_file

            self.path = hydrate_state_file(self.path)
        except Exception:
            pass
        self._seen: set[str] = self._load()

    # ------------------------------------------------------------------
    # Public

    def __contains__(self, filename: str) -> bool:
        """True if *filename* has already been scheduled."""
        return filename in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    def record(
        self,
        filename: str,
        post_id: str,
        pin_id: str,
        topic: str,
        timestamp: str | None = None,
    ) -> None:
        """
        Append *filename* to the history file and add it to the in-memory set.

        Parameters
        ----------
        filename  : Source image filename (the deduplication key).
        post_id   : Inventory post_id (or 'MANUAL_IMPORT' for backfills).
        pin_id    : Pinterest pin ID (or 'MANUAL_IMPORT' for backfills).
        topic     : Human-readable topic label for the audit trail.
        timestamp : ISO-8601 UTC string, e.g. '2026-05-04T00:00:00Z'.
                    Defaults to the current UTC time when omitted.
        """
        if filename in self._seen:
            return  # idempotent — safe to call multiple times
        ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = _HISTORY_SEP.join([ts, filename, post_id, pin_id, topic[:60]])
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self._seen.add(filename)
        try:
            from modules.durable_store import sync_state_file

            sync_state_file(self.path)
        except Exception:
            pass
        log.debug("History: recorded '%s'", filename)

    # ------------------------------------------------------------------
    # Private

    def _load(self) -> set[str]:
        if not self.path.is_file():
            return set()
        seen: set[str] = set()
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(_HISTORY_SEP)
            # Column 1 (0-indexed) is the filename
            if len(parts) >= 2:
                seen.add(parts[1].strip())
        log.debug("History loaded: %d recorded filenames.", len(seen))
        return seen


def _entry_is_video_only(entry: dict) -> bool:
    """True when the entry has no image card and would have been a video pin."""
    from agents.posting.pinterest_engine.inventory import is_video_filename  # noqa: PLC0415

    if entry.get("local_image_path") or entry.get("imgbb_url"):
        return False
    if str(entry.get("media_kind") or "").lower() == "video":
        return True
    video = entry.get("local_video_path") or ""
    if video and is_video_filename(video):
        return True
    return is_video_filename(get_entry_filename(entry))


def _title_is_filename(title: str, filename: str) -> bool:
    """True when *title* is just the clip filename spelled out."""
    from agents.posting.pinterest_engine.inventory import _title_from_clip_name  # noqa: PLC0415

    text = (title or "").strip()
    if not text or not filename:
        return False
    stem = Path(filename).stem
    banned = {
        stem.lower(),
        Path(filename).name.lower(),
        stem.replace("_", " ").replace("-", " ").lower(),
        _title_from_clip_name(filename).lower(),
    }
    return text.lower() in banned


def get_entry_filename(entry: dict) -> str:
    """
    Return the unique trackable filename for a master_inventory entry.

    Priority:
      1. Basename of local_image_path  (most unique — tied to the file on disk)
      2. Basename of imgbb_url          (public cloud copy)
      3. post_id                        (guaranteed unique fallback)
    """
    local = entry.get("local_image_path", "")
    if local:
        return Path(local).name

    video = entry.get("local_video_path", "")
    if video:
        return Path(video).name

    imgbb = entry.get("imgbb_url", "")
    if imgbb:
        # Strip query strings and hash fragments before taking the basename
        return Path(imgbb.split("?")[0].split("#")[0]).name

    return entry.get("post_id", "UNKNOWN")


# ---------------------------------------------------------------------------
# Public scheduling helpers
# ---------------------------------------------------------------------------

def get_random_interval(
    min_hours: float | None = None,
    max_hours: float | None = None,
) -> int:
    """
    Return a random interval in MINUTES between min_hours and max_hours.
    Reads MIN_INTERVAL_HOURS / MAX_INTERVAL_HOURS from env if not passed.
    """
    if min_hours is None:
        min_hours = cfg.MIN_INTERVAL_HOURS
    if max_hours is None:
        max_hours = cfg.MAX_INTERVAL_HOURS

    min_minutes = int(min_hours * 60)
    max_minutes = int(max_hours * 60)
    return random.randint(min_minutes, max_minutes)


def _resolve_pins_per_run() -> int:
    pins = cfg.PINTEREST_PINS_PER_DAY
    if isinstance(pins, int) and pins > 0:
        return pins
    return random.randint(_DEFAULT_PINS_MIN, _DEFAULT_PINS_MAX)


def _sleep_with_log(minutes: int, label: str = "") -> None:
    """
    Sleep for `minutes` minutes, logging progress every 10 minutes.
    Can be interrupted cleanly with Ctrl-C.
    """
    total_sec = minutes * 60
    elapsed = 0
    chunk = min(600, total_sec)   # log every 10 min or less

    eta = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info(
        "Waiting %d min (~%.1f h) before next pin. ETA: %s  %s",
        minutes, minutes / 60, eta, f"[{label}]" if label else "",
    )
    try:
        while elapsed < total_sec:
            time.sleep(chunk)
            elapsed += chunk
            remaining = total_sec - elapsed
            if remaining > 0:
                log.info(
                    "  ... %d min elapsed, %d min remaining.",
                    elapsed // 60, remaining // 60,
                )
    except KeyboardInterrupt:
        log.warning("Sleep interrupted by user (Ctrl-C). Continuing with next pin...")


# ---------------------------------------------------------------------------
# PinterestScheduler
# ---------------------------------------------------------------------------

class PinterestScheduler:
    """
    Orchestrates safe-drip publishing against master_inventory.json.

    Parameters
    ----------
    outputs_dir : Path, optional
        Root outputs/ directory.
    pins_per_run : int, optional
        Override for the number of pins to publish this session.
    dry_run : bool
        Full pipeline except the actual API call.
    no_wait : bool
        Skip the sleep interval (useful for testing).
    min_interval_hours : float, optional
        Override for MIN_INTERVAL_HOURS.
    max_interval_hours : float, optional
        Override for MAX_INTERVAL_HOURS.
    """

    def __init__(
        self,
        outputs_dir: Path | None = None,
        pins_per_run: int | None = None,
        dry_run: bool = False,
        no_wait: bool = False,
        min_interval_hours: float | None = None,
        max_interval_hours: float | None = None,
        context: dict | None = None,
    ) -> None:
        self.outputs_dir = outputs_dir or cfg.OUTPUTS_DIR
        self.dry_run = dry_run
        self.no_wait = no_wait
        self.min_interval_hours = min_interval_hours
        self.max_interval_hours = max_interval_hours
        self.pins_per_run = pins_per_run or _resolve_pins_per_run()
        self.context = context or {}

        log.info(
            "Scheduler ready: pins_per_run=%d  dry_run=%s  no_wait=%s  "
            "interval=%.1f-%.1fh",
            self.pins_per_run, dry_run, no_wait,
            min_interval_hours or cfg.MIN_INTERVAL_HOURS,
            max_interval_hours or cfg.MAX_INTERVAL_HOURS,
        )

    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        Execute one publishing session.

        Returns stats dict: {published, skipped, skipped_by_history, errors, remaining}.
        """
        from agents.posting.pinterest_engine.image_transformer import (  # noqa: PLC0415
            PinTransformer,
            configured_pin_method,
        )
        from agents.posting.pinterest_engine.inventory import MasterInventory, validate_caption_safe  # noqa: PLC0415
        from agents.posting.pinterest_engine.library_source import ensure_local_library  # noqa: PLC0415
        from agents.posting.pinterest_engine.publisher import (  # noqa: PLC0415
            PinterestPublisher,
            PinterestTokenExpiredError,
        )

        prefix = str(self.context.get("b2_prefix") or "")
        if not prefix and cfg.CHANNEL_ID:
            from agents.posting.pinterest_engine.library_source import library_prefix  # noqa: PLC0415

            prefix = library_prefix(cfg.CHANNEL_ID)
        if prefix:
            ensure_local_library(
                self.outputs_dir,
                prefix=prefix,
                client=self.context.get("b2_client"),
                bucket=self.context.get("b2_bucket"),
            )

        inv = MasterInventory(outputs_dir=self.outputs_dir)
        data = inv.load()
        library_ids = {
            path.stem for path in (self.outputs_dir / "library").glob("post_*.json")
        }
        known_ids = {entry.get("post_id") for entry in data.get("entries") or []}
        if not known_ids or not library_ids <= known_ids:
            log.info(
                "Building master inventory from the library with the Claude pin-copy path."
            )
            data = inv.build(use_ai=True)
            if not data.get("entries"):
                log.warning(
                    "No recyclable assets found under %s. "
                    "Add library/post_*.json with images first.",
                    self.outputs_dir,
                )
                return {"published": 0, "skipped": 0, "skipped_by_history": 0,
                        "errors": 0, "remaining": 0}

        # --- Layer 1 guard: inventory posted_on_pinterest flag ---
        unposted = inv.get_unposted(data)

        # --- Layer 2 guard: filename history file ---
        history = FilenameHistory(self.outputs_dir)
        if len(history):
            before = len(unposted)
            unposted = [
                e for e in unposted
                if get_entry_filename(e) not in history
            ]
            skipped_by_history = before - len(unposted)
            if skipped_by_history:
                log.info(
                    "Filename history: skipped %d entr%s already in scheduled_history.txt.",
                    skipped_by_history,
                    "y" if skipped_by_history == 1 else "ies",
                )
        else:
            skipped_by_history = 0

        total_eligible = len(unposted)
        batch = unposted[: self.pins_per_run]

        unposted_before_history = total_eligible + skipped_by_history
        log.info(
            "Inventory: %d total | %d unposted-by-flag | %d blocked-by-history | %d selected",
            len(data["entries"]), unposted_before_history,
            skipped_by_history, len(batch),
        )

        if not batch:
            log.warning(
                "No eligible pins remaining after history filter. "
                "All unposted entries may already be in scheduled_history.txt."
            )
            return {"published": 0, "skipped": 0,
                    "skipped_by_history": skipped_by_history,
                    "errors": 0, "remaining": 0}

        self._attach_article_links(data.get("entries") or [])
        ledger = self._load_ledger()
        self._postplanner_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        # Initialise tools. Method comes from the channel pack; default is the
        # original blurred-padding card (hook panel, gold divider, CTA bar).
        transformer = PinTransformer(
            outputs_dir=self.outputs_dir,
            method=str(self.context.get("pin_method") or configured_pin_method()),
        )

        publisher = None
        if not self.dry_run:
            try:
                publisher = PinterestPublisher()
                if not publisher.validate_token():
                    from agents.posting.pinterest_engine.publisher import PinterestTokenExpiredError  # noqa: PLC0415
                    raise PinterestTokenExpiredError("Token validation failed before batch.")
            except PinterestTokenExpiredError:
                inv.save(data)
                raise

        stats = {
            "published": 0,
            "skipped": 0,
            "skipped_by_history": skipped_by_history,
            "errors": 0,
            "remaining": total_eligible,
        }

        for i, entry in enumerate(batch):
            post_id = entry["post_id"]
            topic = entry.get("topic", "?")
            title = entry.get("pinterest_title", topic)
            src_filename = get_entry_filename(entry)

            if _entry_is_video_only(entry):
                log.info(
                    "  Skipping video %s — image cards only (file=%s).",
                    post_id, src_filename,
                )
                stats["skipped"] += 1
                continue
            if _title_is_filename(entry.get("pinterest_title", ""), src_filename):
                log.info(
                    "  Skipping %s — title was derived from the filename.",
                    post_id,
                )
                stats["skipped"] += 1
                continue

            if entry.get("_skip_article") == "skip":
                log.info("  Skipping %s — image map says skip.", post_id)
                stats["skipped"] += 1
                continue

            log.info(
                "[%d/%d] Processing: '%s'  post_id=%s  file=%s",
                i + 1, len(batch), topic, post_id, src_filename,
            )

            if ledger.seen(post_id=post_id, title=title):
                log.info("  Skipping %s — already in the shared ledger or on the board.", post_id)
                stats["skipped"] += 1
                continue

            # --- Safety: validate caption ---
            caption = entry.get("pinterest_caption", "")
            valid, reason = validate_caption_safe(caption)
            if not valid:
                log.warning("  Caption safety check failed (%s) -- auto-fixing.", reason)
                from agents.posting.pinterest_engine.inventory import (  # noqa: PLC0415
                    build_caption_regex,
                    is_raw_draft,
                    strip_draft_headers,
                )
                original = entry.get("original_caption", "")
                fact_sheet = entry.get("raw_fact_sheet", "")

                if is_raw_draft(original):
                    log.warning(
                        "  original_caption is also a raw draft for %s "
                        "-- using raw_fact_sheet as source.", post_id,
                    )
                    source = strip_draft_headers(fact_sheet) if fact_sheet else ""
                else:
                    source = original

                entry["pinterest_caption"] = build_caption_regex(
                    source, entry.get("variant_index", 0)
                )
                log.info(
                    "  Caption rebuilt from %s source.",
                    "fact_sheet" if (is_raw_draft(original) and fact_sheet) else "original_caption",
                )

            # --- Source photo (library file, one B2 download, or FLUX) ---
            image_path, delete_after = self._ensure_photo(entry, prefix)
            source_hash = ""
            if image_path is not None and image_path.is_file():
                from agents.posting.pinterest_engine.ledger import source_image_hash  # noqa: PLC0415

                source_hash = source_image_hash(image_path)
                entry["local_image_path"] = str(image_path)
            if source_hash and ledger.seen(post_id=post_id, title=title, source_hash=source_hash):
                log.info("  Skipping %s — source image already pinned.", post_id)
                if delete_after and image_path is not None:
                    image_path.unlink(missing_ok=True)
                stats["skipped"] += 1
                continue

            # --- Transform image ---
            pin_bytes = None
            try:
                pin_bytes = transformer.get_pin_bytes(entry)
                if pin_bytes:
                    transformer.transform(entry)   # also save pin to disk for audit
            finally:
                if delete_after and image_path is not None:
                    image_path.unlink(missing_ok=True)
            if pin_bytes is None:
                log.warning("  No image for %s -- skipping.", post_id)
                stats["skipped"] += 1
                continue

            if self.dry_run:
                card_path = _write_dry_run_card(
                    str(self.context.get("account_id") or cfg.CHANNEL_ID or "pin"),
                    post_id,
                    pin_bytes,
                )
                log.info(
                    "  [DRY RUN] Would publish '%s' (%d bytes pin image) card=%s",
                    title, len(pin_bytes), card_path,
                )
                log.info(
                    "  [DRY RUN] Would record '%s' in scheduled_history.txt.",
                    src_filename,
                )
                self._queue_facebook(entry, pin_bytes, ledger, record_note=True)
                inv.mark_posted(data, post_id, "DRY_RUN_PIN_ID")
                inv.save(data)
                stats["published"] += 1

            else:
                # --- Live publish ---
                try:
                    result = publisher.publish(entry, pin_bytes)
                except Exception as exc:  # noqa: BLE001
                    from agents.posting.pinterest_engine.publisher import PinterestTokenExpiredError  # noqa: PLC0415
                    if isinstance(exc, PinterestTokenExpiredError):
                        inv.save(data)
                        raise
                    log.error("  Publish exception for %s: %s", post_id, exc)
                    stats["errors"] += 1
                    continue

                if result:
                    pin_id = result.get("id", "unknown")
                    exported = self._queue_facebook(
                        entry, pin_bytes, ledger, record_note=False
                    )
                    inv.mark_posted(data, post_id, pin_id)
                    inv.save(data)              # crash-safe: save after EVERY success
                    history.record(src_filename, post_id, pin_id, topic)
                    ledger.append(
                        {
                            "post_id": post_id,
                            "title": title,
                            "pin_id": pin_id,
                            "source_hash": source_hash,
                            "article": entry.get("target_url") or "",
                            "facebook_exported": exported,
                        }
                    )
                    stats["published"] += 1
                    log.info(
                        "  Published! Pin ID=%s | history logged: %s",
                        pin_id, src_filename,
                    )
                else:
                    log.warning("  Soft failure for %s -- NOT recorded in history.", post_id)
                    stats["errors"] += 1

            # --- Human-mimic interval (skip after last pin) ---
            is_last = (i == len(batch) - 1)
            if not is_last and not self.no_wait:
                interval = get_random_interval(
                    self.min_interval_hours, self.max_interval_hours
                )
                time.sleep(_INTER_PIN_COURTESY_SEC)
                _sleep_with_log(interval, label=f"next: {batch[i+1].get('topic', '?')[:40]}")

        stats["remaining"] = max(0, total_eligible - stats["published"])
        log.info(
            "Session done -- published=%d  skipped=%d  history-blocked=%d  "
            "errors=%d  remaining=%d",
            stats["published"], stats["skipped"],
            stats["skipped_by_history"], stats["errors"], stats["remaining"],
        )
        return stats

    def _queue_facebook(self, entry: dict, pin_bytes: bytes, ledger, *, record_note: bool) -> bool:
        """Append a Post Planner row for a card that was just committed.

        A Facebook failure is logged and does not fail the pin. ``record_note``
        is for dry runs, which do not append a published-pin ledger row.
        """
        post_id = str(entry.get("post_id") or "")
        try:
            from agents.posting.pinterest_engine.facebook_queue import (  # noqa: PLC0415
                enqueue_facebook_card,
                facebook_settings,
            )

            settings = facebook_settings(self.context)
            if not settings["enabled"]:
                return False
            if ledger.facebook_exported(post_id):
                log.info("  Facebook row already queued for %s.", post_id)
                return True
            queued = enqueue_facebook_card(
                entry,
                pin_bytes,
                channel_id=str(settings["channel_id"] or cfg.CHANNEL_ID or ""),
                page_name=str(settings["page_name"] or ""),
                caption_template=str(settings["template"] or ""),
                planner_dir=Path(self.outputs_dir) / "postplanner",
                run_stamp=getattr(self, "_postplanner_stamp", "")
                or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                client=self.context.get("b2_client"),
                bucket=self.context.get("b2_bucket"),
            )
        except Exception as exc:  # noqa: BLE001
            log.info(
                "  Facebook Post Planner row failed for %s (%s).",
                post_id,
                type(exc).__name__,
            )
            return False
        if queued and record_note:
            try:
                ledger.note_facebook_export(
                    post_id=post_id,
                    article=str(entry.get("target_url") or ""),
                )
            except Exception as exc:  # noqa: BLE001
                log.info("  Facebook ledger note failed for %s (%s).", post_id, type(exc).__name__)
        return bool(queued)

    def _attach_article_links(self, entries: list[dict]) -> None:
        site = str(self.context.get("site_url") or cfg.TARGET_URL or "").strip()
        if not site or site.upper() == "TODO":
            return
        verify = self.context.get("article_tls_verify", True)
        if isinstance(verify, str):
            verify = verify.strip().lower() not in {"0", "false", "no", "off"}
        account = str(self.context.get("account_id") or cfg.CHANNEL_ID or "")
        catalog = self.context.get("catalog")
        if catalog is None:
            from agents.posting.pinterest_engine.articles import fetch_wordpress_catalog  # noqa: PLC0415

            catalog = fetch_wordpress_catalog(site, verify=bool(verify), account_id=account)
        from agents.posting.pinterest_engine.articles import attach_article_url  # noqa: PLC0415

        for entry in entries:
            if attach_article_url(entry, catalog, account) == "skip":
                entry["_skip_article"] = "skip"

    def _load_ledger(self):
        import os  # noqa: PLC0415

        from agents.posting.pinterest_engine.ledger import (  # noqa: PLC0415
            PinLedger,
            fetch_board_pins,
        )

        account = str(self.context.get("account_id") or cfg.CHANNEL_ID or "default")
        cache = self.context.get("ledger_cache")
        ledger = PinLedger(
            account,
            client=self.context.get("b2_client"),
            bucket=self.context.get("b2_bucket"),
            cache_path=cache,
        )
        ledger.load()
        pins = list(self.context.get("board_pins") or [])
        if not pins and not self.context.get("skip_board_fetch"):
            token = (os.getenv("PINTEREST_ACCESS_TOKEN") or "").strip()
            board = (os.getenv("PINTEREST_BOARD_ID") or "").strip()
            if token and board:
                pins = fetch_board_pins(board, token)
        if pins:
            ledger.seed_board_pins(pins)
        return ledger

    def _ensure_photo(self, entry: dict, prefix: str) -> tuple[Path | None, bool]:
        from agents.posting.pinterest_engine.flux_photo import ensure_flux_photo  # noqa: PLC0415
        from agents.posting.pinterest_engine.library_source import materialize_image  # noqa: PLC0415
        from agents.posting.pinterest_engine.photos import is_text_graphic  # noqa: PLC0415

        channel = str(cfg.CHANNEL_ID or self.context.get("account_id") or "pinterest")
        path, delete_after = materialize_image(
            entry,
            prefix=prefix,
            channel=channel,
            client=self.context.get("b2_client"),
            bucket=self.context.get("b2_bucket"),
        )
        if path is not None and path.is_file() and not is_text_graphic(path):
            return path, delete_after
        if delete_after and path is not None:
            path.unlink(missing_ok=True)
        generated = ensure_flux_photo(
            entry,
            outputs_dir=self.outputs_dir,
            channel_id=channel,
            prefix=prefix or f"{channel}/library/",
            client=self.context.get("b2_client"),
            bucket=self.context.get("b2_bucket"),
        )
        return generated, False

    # ------------------------------------------------------------------

    def show_status(self) -> None:
        """Print a rich queue/history summary."""
        from agents.posting.pinterest_engine.inventory import MasterInventory  # noqa: PLC0415

        inv = MasterInventory(outputs_dir=self.outputs_dir)
        data = inv.load()
        history = FilenameHistory(self.outputs_dir)

        all_entries = data.get("entries", [])
        total = len(all_entries)

        # --- Compute all counters independently so they never mask each other ---

        # How many entries have posted_on_pinterest=True in the inventory
        posted_flag_count = sum(
            1 for e in all_entries
            if e.get("publication_status", {}).get("posted_on_pinterest")
        )

        # How many inventory entries (across ALL 222) have a filename in the
        # history file — regardless of their posted_on_pinterest status.
        # This is the canonical "Blocked by history" number.
        blocked_by_history = sum(
            1 for e in all_entries
            if get_entry_filename(e) in history
        )

        # True queue = entries that pass BOTH guards:
        #   1. posted_on_pinterest is False  (inventory guard)
        #   2. filename is NOT in history    (filename guard)
        # We use inv.get_unposted() for guard 1 (it also requires image + metadata).
        unposted_inv = inv.get_unposted(data)
        eligible = [
            e for e in unposted_inv
            if get_entry_filename(e) not in history
        ]

        no_img = sum(
            1 for e in all_entries
            if not e.get("local_image_path") and not e.get("imgbb_url")
        )
        no_meta = sum(
            1 for e in all_entries
            if not e.get("pinterest_title")
        )

        print("\nPinterest Engine Status")
        if cfg.CHANNEL_ID:
            print(f"  Channel                : {cfg.CHANNEL_ID}")
        print(f"  Outputs dir            : {self.outputs_dir}")
        print(f"  Inventory entries      : {total}")
        print(f"  Published (inventory)  : {posted_flag_count}")
        print(f"  History file entries   : {len(history)}")
        print(f"  Blocked by history     : {blocked_by_history}  "
              f"(filenames in {_HISTORY_FILENAME} matched against inventory)")
        print(f"  True queue (eligible)  : {len(eligible)}  "
              f"(unposted + not in history)")
        print(f"  No image data          : {no_img}")
        print(f"  No Pinterest meta      : {no_meta}  (run sync to generate)")

        if eligible:
            print(f"\n  Next {min(5, len(eligible))} in queue:")
            for e in eligible[:5]:
                fn = get_entry_filename(e)
                print(
                    f"    [{e['post_id']}] {e.get('topic', '?')[:40]}"
                    f" | file: {fn}"
                )
        print()
