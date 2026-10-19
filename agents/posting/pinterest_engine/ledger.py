# -*- coding: utf-8 -*-
"""Shared pin ledger in B2, with a local cache when B2 cannot be read.

One JSONL object per account: ``pinterest/ledger/<account>.jsonl``.
A row is a duplicate when the normalized title, the source-image hash,
or the library post id was already published or seeded from the board.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_TITLE_NOISE = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    return _TITLE_NOISE.sub(" ", (title or "").lower()).strip()


def source_image_hash(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def ledger_object_key(account_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", account_id.strip()) or "account"
    return f"pinterest/ledger/{safe}.jsonl"


class PinLedger:
    def __init__(
        self,
        account_id: str,
        *,
        client: Any | None = None,
        bucket: str | None = None,
        cache_path: Path | None = None,
    ) -> None:
        self.account_id = account_id
        self.client = client
        self.bucket = bucket
        self.key = ledger_object_key(account_id)
        if cache_path is None:
            from utils.pipeline_paths import outputs_root  # noqa: PLC0415

            cache_path = outputs_root() / "pinterest" / "ledger" / f"{self.key.split('/')[-1]}"
        self.cache_path = cache_path
        self.rows: list[dict] = []

    def load(self) -> list[dict]:
        text = self._read_remote()
        if text is None:
            text = self._read_cache()
        self.rows = _parse_jsonl(text or "")
        return self.rows

    def seen(self, *, post_id: str = "", title: str = "", source_hash: str = "") -> bool:
        wanted_title = normalize_title(title)
        wanted_id = (post_id or "").strip()
        wanted_hash = (source_hash or "").strip()
        for row in self.rows:
            if row.get("pin_recorded") is False:
                continue
            if wanted_id and str(row.get("post_id") or "") == wanted_id:
                return True
            if wanted_hash and str(row.get("source_hash") or "") == wanted_hash:
                return True
            row_title = normalize_title(str(row.get("title") or ""))
            if wanted_title and row_title and row_title == wanted_title:
                return True
        return False

    def facebook_exported(self, post_id: str) -> bool:
        """True when this library post already has a Facebook Post Planner row."""
        wanted = (post_id or "").strip()
        if not wanted:
            return False
        for row in self.rows:
            if str(row.get("post_id") or "") != wanted:
                continue
            if row.get("facebook_exported"):
                return True
        return False

    def append(self, row: dict) -> None:
        stored = {
            "account": self.account_id,
            "post_id": str(row.get("post_id") or ""),
            "title": str(row.get("title") or ""),
            "pin_id": str(row.get("pin_id") or ""),
            "source_hash": str(row.get("source_hash") or ""),
            "article": str(row.get("article") or ""),
            "seeded": bool(row.get("seeded")),
            "facebook_exported": bool(row.get("facebook_exported")),
            "published_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.rows.append(stored)
        self._write()

    def note_facebook_export(self, *, post_id: str, article: str = "") -> None:
        """Remember a Facebook row without treating the pin as already published.

        A dry run queues the sheet before the live pin exists. That note must
        not make ``seen()`` skip the later publish.
        """
        wanted = (post_id or "").strip()
        if not wanted:
            return
        touched = False
        for row in self.rows:
            if str(row.get("post_id") or "") == wanted:
                row["facebook_exported"] = True
                touched = True
        if touched:
            self._write()
            return
        self.rows.append(
            {
                "account": self.account_id,
                "post_id": wanted,
                "title": "",
                "pin_id": "",
                "source_hash": "",
                "article": str(article or ""),
                "seeded": False,
                "facebook_exported": True,
                "pin_recorded": False,
                "published_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        self._write()

    def seed_board_pins(self, pins: list[dict], *, persist: bool = True) -> int:
        """Add board pins that are not already in the ledger. Returns how many were added.

        ``persist=False`` keeps the rows in memory for this run and does not
        upload the ledger. Dry runs use that so B2 stays unchanged.
        """
        added = 0
        for pin in pins:
            title = str(pin.get("title") or "").strip()
            pin_id = str(pin.get("id") or pin.get("pin_id") or "").strip()
            source_hash = str(pin.get("source_hash") or "").strip()
            if not title and not pin_id and not source_hash:
                continue
            if self.seen(title=title, source_hash=source_hash, post_id=""):
                continue
            self.rows.append(
                {
                    "account": self.account_id,
                    "post_id": "",
                    "title": title,
                    "pin_id": pin_id,
                    "source_hash": source_hash,
                    "article": "",
                    "seeded": True,
                    "published_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )
            added += 1
        if added and persist:
            self._write()
            log.info("Seeded %d existing board pin(s) into %s", added, self.key)
        elif added:
            log.info(
                "Noted %d existing board pin(s) in memory only (no ledger upload).",
                added,
            )
        return added

    def _write(self) -> None:
        payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.rows)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.cache_path)
        try:
            from agents.media.b2_client import upload_object  # noqa: PLC0415

            upload_object(
                self.key,
                payload.encode("utf-8"),
                client=self.client,
                bucket=self.bucket,
                content_type="application/x-ndjson",
            )
        except Exception as exc:  # noqa: BLE001
            from agents.media.b2_budget import B2DailyCapError  # noqa: PLC0415

            if isinstance(exc, B2DailyCapError):
                raise
            log.info("B2 ledger write failed (%s); local cache kept.", type(exc).__name__)

    def _read_remote(self) -> str | None:
        try:
            from agents.media.b2_client import read_object_text  # noqa: PLC0415

            return read_object_text(self.key, client=self.client, bucket=self.bucket)
        except Exception as exc:  # noqa: BLE001
            from agents.media.b2_budget import B2DailyCapError  # noqa: PLC0415

            if isinstance(exc, B2DailyCapError):
                raise
            log.info("B2 ledger read failed (%s); using the local cache.", type(exc).__name__)
            return None

    def _read_cache(self) -> str:
        if not self.cache_path.is_file():
            return ""
        try:
            return self.cache_path.read_text(encoding="utf-8")
        except OSError:
            return ""


def _parse_jsonl(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def fetch_board_pins(board_id: str, token: str, *, limit: int = 50) -> list[dict]:
    """Latest pins on the board. Titles are used to stop a repeat post."""
    if not board_id or not token:
        return []
    import requests  # noqa: PLC0415

    url = f"https://api.pinterest.com/v5/boards/{board_id}/pins"
    headers = {"Authorization": f"Bearer {token}"}
    params: dict[str, str | int] = {"page_size": min(limit, 25)}
    pins: list[dict] = []
    bookmark = ""
    while len(pins) < limit:
        if bookmark:
            params["bookmark"] = bookmark
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=20)
        except requests.RequestException as exc:
            log.info("Board pin list failed: %s", type(exc).__name__)
            break
        if resp.status_code != 200:
            log.info("Board pin list HTTP %s", resp.status_code)
            break
        try:
            payload = resp.json()
        except ValueError:
            break
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            break
        for item in items:
            if isinstance(item, dict):
                pins.append(item)
        bookmark = str(payload.get("bookmark") or "") if isinstance(payload, dict) else ""
        if not bookmark:
            break
    return pins[:limit]
