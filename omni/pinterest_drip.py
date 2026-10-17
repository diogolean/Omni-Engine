# -*- coding: utf-8 -*-
"""Per-account wrapper around the original PC pin pipeline.

The card itself is built by MasterInventory, PinTransformer, and
PinterestPublisher. This module only decides which account this process
is allowed to run, then calls that same scheduler.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from agents.posting.pinterest_engine.articles import (
    fetch_wordpress_catalog,
    match_article_slug,
)
from agents.posting.pinterest_engine.photos import is_text_graphic
from omni.accounts import pinterest_account_env
from omni.models import Account
from omni.pinterest_accounts import account_blockers

log = logging.getLogger("omni.pinterest_drip")

__all__ = [
    "account_runs_here",
    "fetch_wordpress_catalog",
    "is_text_graphic",
    "match_article_slug",
    "publish_account",
    "publisher_mode",
]


def publisher_mode(default: str = "docker") -> str:
    return (os.getenv("PUBLISHER_MODE") or default).strip().lower() or default


def account_runs_here(account: Account, mode: str | None = None) -> bool:
    """True when this process mode is allowed to publish ``account``.

    ``runner`` is ``local``, ``docker``, ``both``, or ``off``.
    ``both`` runs in either process. ``off`` never runs.
    """
    runner = str(account.extra.get("runner") or "both").strip().lower()
    current = (mode if mode is not None else publisher_mode()).strip().lower()
    if runner in {"", "both"}:
        return True
    if runner == "off":
        return False
    return runner == current


def publish_account(
    account: Account,
    *,
    pins: int = 1,
    dry_run: bool = False,
    client: Any | None = None,
    bucket: str | None = None,
    board_pins: list[dict] | None = None,
    catalog: list | None = None,
) -> dict:
    """Publish up to *pins* cards for one account through the PC pipeline."""
    stats = {
        "published": 0,
        "skipped": 0,
        "errors": 0,
        "remaining": 0,
        "dry_run": dry_run,
    }
    if not account_runs_here(account):
        log.info(
            "Pinterest account %s skipped: runner=%s mode=%s",
            account.account_id,
            account.extra.get("runner") or "both",
            publisher_mode(),
        )
        stats["skipped_reason"] = "runner"
        return stats

    blockers = account_blockers(account)
    if blockers:
        log.info(
            "Pinterest account %s skipped until config is set: %s",
            account.account_id,
            ", ".join(blockers),
        )
        stats["skipped_reason"] = ",".join(blockers)
        return stats

    from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415
    from agents.posting.pinterest_engine.scheduler import PinterestScheduler  # noqa: PLC0415

    cfg.configure(channel_id=account.branding)
    if not cfg.DOTENV_LOADED_FROM_FILE:
        cfg.DOTENV_LOADED_FROM_FILE = True

    scheduler = PinterestScheduler(
        outputs_dir=cfg.OUTPUTS_DIR,
        pins_per_run=pins,
        dry_run=dry_run,
        no_wait=True,
        context={
            "account_id": account.account_id,
            "site_url": account.extra.get("site_url", ""),
            "article_tls_verify": account.extra.get("article_tls_verify", "true"),
            "b2_prefix": account.extra.get("b2_prefix", ""),
            "b2_client": client,
            "b2_bucket": bucket,
            "board_pins": board_pins or [],
            "catalog": catalog,
            "skip_board_fetch": client is not None or board_pins is not None,
        },
    )
    with pinterest_account_env(account):
        result = scheduler.run()
    result["dry_run"] = dry_run
    return result
