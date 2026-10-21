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

import json
import logging
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.posting.pinterest_engine import config as cfg  # noqa: E402

log = logging.getLogger(__name__)


def _fetch_stop_reason(exc: BaseException) -> str:
    """Stop the run on a B2 daily cap or a Drive-only quota failure."""
    from agents.media.b2_budget import B2DailyCapError  # noqa: PLC0415
    from agents.media.drive_source import DriveQuotaError  # noqa: PLC0415

    if isinstance(exc, DriveQuotaError):
        return "drive_quota"
    if isinstance(exc, B2DailyCapError):
        return "b2_daily_cap"
    return ""

_DEFAULT_MIN_HOURS = 3.0
_DEFAULT_MAX_HOURS = 6.0
_DEFAULT_PINS_MIN = 3
_DEFAULT_PINS_MAX = 5
_INTER_PIN_COURTESY_SEC = 3   # brief pause before starting sleep countdown
_LAZY_INVENTORY_SPARE = 2


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
            from shared.store import hydrate_state_file

            self.path = hydrate_state_file(self.path)
        except Exception:
            pass
        self._seen, self._article_rows = self._load()

    # ------------------------------------------------------------------
    # Public

    def __contains__(self, filename: str) -> bool:
        """True if *filename* has already been scheduled."""
        return filename in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    def article_rows(self) -> list[dict]:
        """Published rows with timestamp, topic, and article URL when recorded."""
        return list(self._article_rows)

    def record(
        self,
        filename: str,
        post_id: str,
        pin_id: str,
        topic: str,
        timestamp: str | None = None,
        article: str = "",
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
        safe_topic = topic.replace("\n", " ").replace(_HISTORY_SEP, " ")[:60]
        safe_url = (article or "").replace("\n", " ").replace(_HISTORY_SEP, " ").strip()
        fields = [ts, filename, post_id, pin_id, safe_topic]
        if safe_url:
            fields.append(safe_url)
        line = _HISTORY_SEP.join(fields)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self._seen.add(filename)
        self._article_rows.append(
            {"published_utc": ts, "topic": safe_topic, "url": safe_url, "filename": filename}
        )
        try:
            from shared.store import sync_state_file

            sync_state_file(self.path)
        except Exception:
            pass
        log.debug("History: recorded '%s'", filename)

    # ------------------------------------------------------------------
    # Private

    def _load(self) -> tuple[set[str], list[dict]]:
        if not self.path.is_file():
            return set(), []
        seen: set[str] = set()
        rows: list[dict] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(_HISTORY_SEP)
            # Column 1 (0-indexed) is the filename
            if len(parts) < 2:
                continue
            filename = parts[1].strip()
            seen.add(filename)
            rows.append(
                {
                    "published_utc": parts[0].strip(),
                    "filename": filename,
                    "topic": parts[4].strip() if len(parts) > 4 else "",
                    "url": parts[5].strip() if len(parts) > 5 else "",
                }
            )
        log.debug("History loaded: %d recorded filenames.", len(seen))
        return seen, rows


def _entry_is_video_only(entry: dict) -> bool:
    """True when the entry has no image card and would have been a video pin."""
    from distribution.scheduling.inventory import is_video_filename  # noqa: PLC0415

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
    from distribution.scheduling.inventory import _title_from_clip_name  # noqa: PLC0415

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
        from distribution.scheduling.inventory import MasterInventory, validate_caption_safe  # noqa: PLC0415
        from distribution.scheduling.library_source import ensure_local_library  # noqa: PLC0415
        from distribution.publishers.api.pinterest import (  # noqa: PLC0415
            PinterestPublisher,
            PinterestTokenExpiredError,
        )

        prefix = str(self.context.get("b2_prefix") or "")
        if not prefix and cfg.CHANNEL_ID:
            from distribution.scheduling.library_source import library_prefix  # noqa: PLC0415

            prefix = library_prefix(cfg.CHANNEL_ID)
        if prefix:
            try:
                ensure_local_library(
                    self.outputs_dir,
                    prefix=prefix,
                    client=self.context.get("b2_client"),
                    bucket=self.context.get("b2_bucket"),
                    folder_id=str(self.context.get("drive_folder_id") or ""),
                )
            except Exception as exc:  # noqa: BLE001
                reason = _fetch_stop_reason(exc)
                if not reason:
                    raise
                log.warning("Library fetch stopped (%s).", reason)
                return {
                    "published": 0,
                    "skipped": 0,
                    "skipped_by_history": 0,
                    "errors": 0,
                    "remaining": 0,
                    "stopped": reason,
                }

        inv = MasterInventory(outputs_dir=self.outputs_dir)
        data = inv.load()
        library_ids = {
            path.stem for path in (self.outputs_dir / "library").glob("post_*.json")
        }
        ready = inv.get_unposted(data)
        from agents.posting.pinterest_engine.articles import article_link_required  # noqa: PLC0415

        account = str(self.context.get("account_id") or cfg.CHANNEL_ID or "")
        wants_articles = article_link_required(account)
        prefer_ids = self._linkable_library_ids() if wants_articles else None
        ready_ids = {entry.get("post_id") for entry in ready}
        if wants_articles:
            ready_linkable = sum(1 for post_id in (prefer_ids or []) if post_id in ready_ids)
            need_build = ready_linkable < self.pins_per_run
        else:
            need_build = len(ready) < self.pins_per_run
        built_entries: int | None = None
        if need_build:
            limit = max(self.pins_per_run, 1) + _LAZY_INVENTORY_SPARE
            log.info(
                "Building master inventory for the next pins only "
                "(limit=%d, library=%d, dry_run=%s).",
                limit,
                len(library_ids),
                self.dry_run,
            )
            data = inv.build(
                use_ai=True,
                limit=limit,
                dry_run=self.dry_run,
                prefer_ids=prefer_ids,
            )
            built_entries = len(data.get("entries") or [])
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

        self._attach_article_links(unposted)
        if wants_articles:
            candidates = [
                entry for entry in unposted
                if entry.get("_skip_article") != "skip"
                and str(entry.get("target_url") or "").strip()
            ]
        else:
            candidates = list(unposted)
        total_eligible = len(candidates)

        if not candidates:
            skipped_unlinked = 0
            if wants_articles and unposted and not skipped_by_history:
                skipped_unlinked = sum(
                    1
                    for entry in unposted
                    if not str(entry.get("target_url") or "").strip()
                )
                log.warning(
                    "No pins with an article link. Skipped %d unlinked post(s).",
                    skipped_unlinked,
                )
            else:
                log.warning(
                    "No eligible pins remaining after history filter. "
                    "All unposted entries may already be in scheduled_history.txt."
                )
            return {
                "published": 0,
                "skipped": skipped_unlinked,
                "skipped_by_history": skipped_by_history,
                "errors": 0,
                "remaining": 0,
            }
        try:
            ledger = self._load_ledger()
        except Exception as exc:  # noqa: BLE001
            from agents.media.b2_budget import B2DailyCapError  # noqa: PLC0415

            if not isinstance(exc, B2DailyCapError):
                raise
            log.warning("B2 daily cap reached — stopping this run.")
            return {
                "published": 0,
                "skipped": 0,
                "skipped_by_history": skipped_by_history,
                "errors": 0,
                "remaining": total_eligible,
                "stopped": "b2_daily_cap",
            }

        from distribution.scheduling.inventory import select_pin_batch  # noqa: PLC0415

        batch, article_skipped = select_pin_batch(
            candidates,
            history.article_rows(),
            list(ledger.rows),
            self.pins_per_run,
        )
        skipped_by_history += article_skipped
        if article_skipped:
            log.info(
                "Article history: skipped %d recent duplicate entr%s.",
                article_skipped,
                "y" if article_skipped == 1 else "ies",
            )

        unposted_before_history = len(unposted) + skipped_by_history
        log.info(
            "Inventory: %d total | %d unposted-by-flag | %d blocked-by-history | %d selected",
            len(data["entries"]), unposted_before_history,
            skipped_by_history, len(batch),
        )
        if not batch:
            log.warning(
                "No eligible pins remaining after article history filter."
            )
            return {
                "published": 0,
                "skipped": article_skipped,
                "skipped_by_history": skipped_by_history,
                "errors": 0,
                "remaining": 0,
            }
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
                    from distribution.publishers.api.pinterest import PinterestTokenExpiredError  # noqa: PLC0415
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
        if built_entries is not None:
            stats["inventory_built"] = built_entries

        for i, entry in enumerate(batch):
            from distribution.scheduling.inventory import ensure_writer_pin_copy  # noqa: PLC0415

            if ensure_writer_pin_copy(entry, use_ai=True) and not self.dry_run:
                inv.save(data)
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
            missing_link = entry.get("_skip_article") == "no-link"
            missing_link = missing_link or not str(entry.get("target_url") or "").strip()
            if missing_link:
                from agents.posting.pinterest_engine.articles import article_link_required  # noqa: PLC0415

                account = str(self.context.get("account_id") or cfg.CHANNEL_ID or "")
                if article_link_required(account):
                    log.info(
                        "  Skipping %s — no article link; pin will not be published.",
                        post_id,
                    )
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
                from distribution.scheduling.inventory import (  # noqa: PLC0415
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
            try:
                image_path, delete_after = self._ensure_photo(entry, prefix)
            except Exception as exc:  # noqa: BLE001
                reason = _fetch_stop_reason(exc)
                if not reason:
                    raise
                log.warning("Library fetch stopped (%s).", reason)
                stats["stopped"] = reason
                break
            source_hash = ""
            if image_path is not None and image_path.is_file():
                from distribution.scheduling.ledger import source_image_hash  # noqa: PLC0415

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
                    "  [DRY RUN] Would publish '%s' (%d bytes pin image) card=%s link=%s",
                    title, len(pin_bytes), card_path, entry.get("target_url") or "",
                )
                log.info(
                    "  [DRY RUN] Not recording '%s' (no inventory save, no ledger, no Facebook).",
                    src_filename,
                )
                stats.setdefault("pins", []).append(
                    {
                        "post_id": post_id,
                        "title": title,
                        "target_url": str(entry.get("target_url") or ""),
                    }
                )
                stats["published"] += 1

            else:
                # --- Live publish ---
                try:
                    result = publisher.publish(entry, pin_bytes)
                except Exception as exc:  # noqa: BLE001
                    from distribution.publishers.api.pinterest import PinterestTokenExpiredError  # noqa: PLC0415
                    if isinstance(exc, PinterestTokenExpiredError):
                        inv.save(data)
                        raise
                    log.error("  Publish exception for %s: %s", post_id, exc)
                    stats["errors"] += 1
                    continue

                if result:
                    pin_id = result.get("id", "unknown")
                    try:
                        exported = self._queue_facebook(
                            entry, pin_bytes, ledger, record_note=False
                        )
                    except Exception as exc:  # noqa: BLE001
                        from agents.media.b2_budget import B2DailyCapError  # noqa: PLC0415

                        if not isinstance(exc, B2DailyCapError):
                            raise
                        exported = False
                        stats["stopped"] = "b2_daily_cap"
                        log.warning("B2 daily cap reached — stopping this run.")
                    inv.mark_posted(data, post_id, pin_id)
                    inv.save(data)              # crash-safe: save after EVERY success
                    history.record(
                        src_filename,
                        post_id,
                        pin_id,
                        topic,
                        article=str(entry.get("target_url") or ""),
                    )
                    try:
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
                    except Exception as exc:  # noqa: BLE001
                        from agents.media.b2_budget import B2DailyCapError  # noqa: PLC0415

                        if not isinstance(exc, B2DailyCapError):
                            raise
                        stats["published"] += 1
                        log.warning("B2 daily cap reached — stopping this run.")
                        stats["stopped"] = "b2_daily_cap"
                        break
                    stats["published"] += 1
                    log.info(
                        "  Published! Pin ID=%s | history logged: %s",
                        pin_id, src_filename,
                    )
                else:
                    log.warning("  Soft failure for %s -- NOT recorded in history.", post_id)
                    stats["errors"] += 1

            if stats.get("stopped") == "b2_daily_cap":
                break

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
            from distribution.scheduling.queue import (  # noqa: PLC0415
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
            from agents.media.b2_budget import B2DailyCapError  # noqa: PLC0415

            if isinstance(exc, B2DailyCapError):
                raise
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

    def _article_catalog(self) -> list:
        """WordPress posts for this run. A caller-supplied catalog is not fetched again."""
        site = str(self.context.get("site_url") or cfg.TARGET_URL or "").strip()
        if not site or site.upper() == "TODO":
            return []
        catalog = self.context.get("catalog")
        if catalog is not None:
            return catalog
        verify = self.context.get("article_tls_verify", True)
        if isinstance(verify, str):
            verify = verify.strip().lower() not in {"0", "false", "no", "off"}
        from agents.posting.pinterest_engine.articles import fetch_wordpress_catalog  # noqa: PLC0415

        account = str(self.context.get("account_id") or cfg.CHANNEL_ID or "")
        catalog = fetch_wordpress_catalog(
            site,
            verify=bool(verify),
            account_id=account,
            outputs_dir=self.outputs_dir,
        )
        self.context["catalog"] = catalog
        return catalog

    def _linkable_library_ids(self) -> list[str]:
        """Library post ids that resolve to an article, longest/catalog order aside.

        Scanning the JSON is cheap. Pin copy is generated only for these ids.
        """
        from agents.posting.pinterest_engine.articles import attach_article_url  # noqa: PLC0415

        catalog = self._article_catalog()
        site = str(self.context.get("site_url") or cfg.TARGET_URL or "").strip()
        account = str(self.context.get("account_id") or cfg.CHANNEL_ID or "")
        ranked: list[str] = []
        library = self.outputs_dir / "library"
        for path in sorted(library.glob("post_*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            probe = {
                "subject_slug": raw.get("subject_slug") or "",
                "topic": raw.get("topic") or "",
                "pinterest_title": raw.get("pinterest_title") or "",
                "post_id": path.stem,
                "image_relative": raw.get("image_relative") or "",
                "image_path": raw.get("image_path") or "",
                "local_image_path": raw.get("local_image_path") or "",
            }
            status = attach_article_url(probe, catalog, account, site_url=site)
            if status is None and str(probe.get("target_url") or "").strip():
                ranked.append(path.stem)
        return ranked

    def _attach_article_links(self, entries: list[dict]) -> None:
        site = str(self.context.get("site_url") or cfg.TARGET_URL or "").strip()
        if not site or site.upper() == "TODO":
            return
        account = str(self.context.get("account_id") or cfg.CHANNEL_ID or "")
        catalog = self._article_catalog()
        from agents.posting.pinterest_engine.articles import attach_article_url  # noqa: PLC0415

        for entry in entries:
            linked = attach_article_url(entry, catalog, account, site_url=site)
            if linked in {"skip", "no-link"}:
                entry["_skip_article"] = linked

    def _load_ledger(self):
        import os  # noqa: PLC0415

        from distribution.scheduling.ledger import (  # noqa: PLC0415
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
            ledger.seed_board_pins(pins, persist=not self.dry_run)
        return ledger

    def _ensure_photo(self, entry: dict, prefix: str) -> tuple[Path | None, bool]:
        from agents.posting.pinterest_engine.flux_photo import ensure_flux_photo  # noqa: PLC0415
        from distribution.scheduling.library_source import materialize_image  # noqa: PLC0415
        from agents.posting.pinterest_engine.photos import is_text_graphic  # noqa: PLC0415

        channel = str(cfg.CHANNEL_ID or self.context.get("account_id") or "pinterest")
        path, delete_after = materialize_image(
            entry,
            prefix=prefix,
            channel=channel,
            client=self.context.get("b2_client"),
            bucket=self.context.get("b2_bucket"),
            folder_id=str(self.context.get("drive_folder_id") or ""),
        )
        if path is not None and path.is_file() and not is_text_graphic(path):
            return path, delete_after
        if delete_after and path is not None:
            path.unlink(missing_ok=True)
        free_path = self._free_license_photo(entry, channel)
        if free_path is not None:
            return free_path, True
        generated = ensure_flux_photo(
            entry,
            outputs_dir=self.outputs_dir,
            channel_id=channel,
            prefix=prefix or f"{channel}/library/",
            client=self.context.get("b2_client"),
            bucket=self.context.get("b2_bucket"),
        )
        return generated, False

    def _free_license_photo(self, entry: dict, channel: str) -> Path | None:
        """One vetted free-license file, before FLUX. None when the search misses."""
        from agents.media.b2_budget import B2DailyCapError  # noqa: PLC0415
        from agents.media.free_images.policy import resolve_policy  # noqa: PLC0415
        from agents.media.free_images.sourcer import resolve_image_source  # noqa: PLC0415
        from utils.pipeline_paths import temp_path  # noqa: PLC0415

        policy = resolve_policy(channel)
        if policy == "ai_only":
            return None
        stem = str(entry.get("subject_slug") or entry.get("post_id") or "pin").strip() or "pin"
        dest = temp_path(channel or "pinterest", "free_images", "pins", f"{stem}.jpg")
        try:
            decision = resolve_image_source(
                channel=channel,
                dest=dest,
                topic=str(entry.get("topic") or entry.get("pinterest_title") or ""),
                entity=str(entry.get("entity") or entry.get("named_entity") or ""),
                sentence=str(
                    entry.get("original_caption") or entry.get("humanized_caption") or ""
                ),
                policy=policy,
                producer="pinterest_card",
            )
        except B2DailyCapError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.info("Free-license photo skipped (%s).", type(exc).__name__)
            return None
        hit = decision.hit
        if hit is None or not dest.is_file():
            return None
        entry["image_credit"] = hit.credit_line
        entry["image_source"] = "free_license"
        entry["image_attribution"] = hit.attribution.to_json()
        return dest

    # ------------------------------------------------------------------

    def show_status(self) -> None:
        """Print a rich queue/history summary."""
        from distribution.scheduling.inventory import MasterInventory  # noqa: PLC0415

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
