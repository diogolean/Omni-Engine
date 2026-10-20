# -*- coding: utf-8 -*-
"""
pinterest_engine.publisher
---------------------------
Pinterest API v5 pin creation -- Standard (Production) access.

Reads from .env:
    PINTEREST_ACCESS_TOKEN   -- Bearer token (required)
    PINTEREST_BOARD_ID       -- Target board numeric ID (optional if BOARD_NAME set)
    PINTEREST_BOARD_NAME     -- Create/reuse this board when BOARD_ID is empty
    PINTEREST_REFRESH_TOKEN  -- Used to auto-refresh on 401 (optional but recommended)
    PINTEREST_APP_ID         -- App ID for token refresh (optional)
    PINTEREST_APP_SECRET     -- App Secret for token refresh (optional)

Token refresh:
    On HTTP 401, the publisher automatically attempts to exchange
    PINTEREST_REFRESH_TOKEN for a fresh access token and writes both
    access_token and refresh_token back to the active channel .env
    before retrying the original request once.

Board handling:
    If PINTEREST_BOARD_ID is missing, GET /v5/boards is scanned for
    PINTEREST_BOARD_NAME (or the channel pack board_name). A missing
    board is created via POST /v5/boards and the new id is persisted.

Error handling:
    401  -> auto-refresh attempted; if refresh fails, raises PinterestTokenExpiredError
    429  -> exponential backoff, up to _MAX_RETRIES attempts
    other -> logged, retried up to _MAX_RETRIES times, then returns None
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_API_BASE      = "https://api.pinterest.com/v5"
_PINS_ENDPOINT = f"{_API_BASE}/pins"
_BOARDS_ENDPOINT = f"{_API_BASE}/boards"
_MEDIA_ENDPOINT = f"{_API_BASE}/media"
_TOKEN_URL     = "https://api.pinterest.com/v5/oauth/token"
_MAX_RETRIES       = 3
_BACKOFF_BASE_SEC  = 10
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}


def _pin_link(record: dict) -> str:
    """Article link. Ancient Knowledge never substitutes the sales URL."""
    from agents.posting.pinterest_engine.articles import resolve_pin_link  # noqa: PLC0415

    return resolve_pin_link(record)


def _hashtags() -> str:
    from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415
    return cfg.HASHTAGS

def _env_path() -> Path:
    """Resolve the active .env path from agents.posting.pinterest_engine.config (supports --env)."""
    from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415
    return cfg.DOTENV_PATH


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PinterestTokenExpiredError(Exception):
    """Raised when the Pinterest API returns 401 and auto-refresh also fails."""


class PinterestPublishError(Exception):
    """Raised for unrecoverable publish failures."""


# ---------------------------------------------------------------------------
# .env writer (used by token-refresh)
# ---------------------------------------------------------------------------

def _write_env_key(key: str, value: str) -> None:
    """Update or append key=value in the active .env without touching other lines."""
    env_path = _env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    pattern = rf"^{re.escape(key)}\s*=.*$"
    new_line = f"{key}={value}"
    if re.search(pattern, text, re.MULTILINE):
        text = re.sub(pattern, new_line, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{new_line}\n"
    env_path.write_text(text, encoding="utf-8")
    os.environ[key] = value


# ---------------------------------------------------------------------------
# PinterestPublisher
# ---------------------------------------------------------------------------

class PinterestPublisher:
    """
    Publishes a single pin to Pinterest via the v5 API (Standard access).

    Auto-refreshes the access token on 401 using the stored refresh_token,
    then retries the request once before raising PinterestTokenExpiredError.
    """

    def __init__(
        self,
        access_token: str | None = None,
        board_id: str | None = None,
        require_board: bool = True,
    ) -> None:
        # Ensure workspace-local .env is loaded (idempotent if already configured).
        from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415
        if not cfg.DOTENV_LOADED_FROM_FILE:
            cfg.configure()

        self.token: str = access_token or os.getenv("PINTEREST_ACCESS_TOKEN", "")
        self.board_id: str = (
            board_id
            or os.getenv("PINTEREST_BOARD_ID", "")
            or ""
        ).strip()
        self.board_name: str = (
            os.getenv("PINTEREST_BOARD_NAME")
            or getattr(cfg, "BOARD_NAME", "")
            or cfg.DISPLAY_NAME
            or "Ancient Knowledge"
        ).strip()
        self._refreshed_this_session = False

        if not self.token:
            raise ValueError(
                "PINTEREST_ACCESS_TOKEN is not set. Add it to .env."
            )
        if require_board:
            self.ensure_board()

    # ------------------------------------------------------------------
    # Public

    def publish(self, record: dict, pin_image_bytes: bytes) -> dict | None:
        """
        Create one new pin. Guards against double-posting and unsafe captions.

        Returns the Pinterest API response dict on success, None on soft failure.
        Raises PinterestTokenExpiredError if token is dead and refresh fails.

        Existing board pins are never edited. A new article link is only
        sent on this create call. There is no relink or update path.
        """
        # Guard: never double-post
        if record.get("publication_status", {}).get("posted_on_pinterest"):
            log.warning(
                "Skipping already-posted entry: %s (pin_id=%s)",
                record.get("post_id", "?"),
                record.get("publication_status", {}).get("pinterest_pin_id"),
            )
            return None

        # Guard: caption safety
        caption = record.get("pinterest_caption", "")
        from distribution.scheduling.inventory import (  # noqa: PLC0415
            build_caption_regex,
            validate_caption_safe,
        )
        valid, reason = validate_caption_safe(caption)
        if not valid:
            log.warning("Caption safety fix (%s) for %s.", reason, record.get("post_id", "?"))
            caption = build_caption_regex(
                record.get("original_caption", ""), record.get("variant_index", 0)
            )
            record["pinterest_caption"] = caption

        from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415

        title = (
            record.get("pinterest_title")
            or record.get("topic")
            or cfg.DEFAULT_TOPIC
        )[:100]
        video_path = (record.get("local_video_path") or "").strip()
        if not pin_image_bytes:
            if video_path and Path(video_path).suffix.lower() in _VIDEO_EXTS:
                log.info(
                    "Skipping video pin (image cards only): %s",
                    Path(video_path).name,
                )
            else:
                log.warning("No pin image for %s — skipping.", record.get("post_id", "?"))
            return None

        from agents.posting.pinterest_engine.pin_copy import pin_alt_text  # noqa: PLC0415

        payload = {
            "board_id": self.board_id,
            "title": title,
            "description": self._build_description(record),
            "alt_text": pin_alt_text(record),
            "link": _pin_link(record) or None,
            "media_source": {
                "source_type": "image_base64",
                "content_type": "image/jpeg",
                "data": base64.b64encode(pin_image_bytes).decode("ascii"),
            },
        }
        if not payload["alt_text"]:
            payload.pop("alt_text")
        if not payload["link"]:
            payload.pop("link")

        log.info(
            "Publishing: '%s' (board=%s, image=%d KB)",
            title, self.board_id, len(pin_image_bytes) // 1024,
        )
        return self._post_with_retry(payload)

    def validate_token(self) -> bool:
        """
        Check token validity via GET /v5/boards (boards:read scope only).
        Returns True if valid, False otherwise. Does not raise.
        """
        try:
            resp = requests.get(
                f"{_API_BASE}/boards",
                headers=self._headers(),
                params={"page_size": 1},
                timeout=15,
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                name = items[0].get("name", "?") if items else "(no boards)"
                log.info("Token valid. First board: '%s'", name)
                return True
            if resp.status_code == 401:
                log.warning("Token 401 on validation. Attempting auto-refresh...")
                if self._try_refresh():
                    return self.validate_token()
                log.error("Token invalid and refresh failed.")
                return False
            log.warning("Validation HTTP %d: %s", resp.status_code, resp.text[:150])
            return False
        except requests.RequestException as exc:
            log.error("Token validation error: %s", exc)
            return False

    def list_boards(self) -> list[dict]:
        """Return every board visible to this token (paginated)."""
        boards: list[dict] = []
        params: dict[str, str | int] = {"page_size": 100}
        url: str | None = _BOARDS_ENDPOINT
        while url:
            try:
                resp = self._request("GET", url, params=params, timeout=20)
            except PinterestTokenExpiredError:
                raise
            except requests.RequestException as exc:
                log.error("Board list failed: %s", exc)
                return boards
            if resp.status_code != 200:
                log.warning("Board list HTTP %d: %s", resp.status_code, resp.text[:200])
                return boards
            data = resp.json()
            boards.extend(data.get("items") or [])
            cursor = data.get("bookmark")
            if cursor:
                params = {"bookmark": cursor, "page_size": 100}
            else:
                url = None
        return boards

    def create_board(self, name: str, description: str = "") -> dict | None:
        """Create a public board via POST /v5/boards."""
        payload = {
            "name": name[:180],
            "privacy": "PUBLIC",
        }
        if description:
            payload["description"] = description[:500]
        try:
            resp = self._request("POST", _BOARDS_ENDPOINT, json=payload, timeout=20)
        except PinterestTokenExpiredError:
            raise
        except requests.RequestException as exc:
            log.error("Board create failed: %s", exc)
            return None
        if resp.status_code in (200, 201):
            data = resp.json()
            log.info("Created Pinterest board '%s' id=%s", data.get("name"), data.get("id"))
            return data
        log.error("Board create HTTP %d: %s", resp.status_code, resp.text[:300])
        return None

    def ensure_board(self) -> str:
        """
        Resolve the target board id, creating the named board when missing.

        Persists PINTEREST_BOARD_ID to the active channel .env.
        """
        if self.board_id:
            log.info("Using existing PINTEREST_BOARD_ID=%s", self.board_id)
            return self.board_id

        if not self.board_name:
            raise ValueError(
                "Neither PINTEREST_BOARD_ID nor PINTEREST_BOARD_NAME is set."
            )

        boards = self.list_boards()
        target = self.board_name.strip().lower()
        for board in boards:
            name = str(board.get("name") or "").strip()
            if name.lower() == target:
                self.board_id = str(board.get("id") or "").strip()
                if self.board_id:
                    _write_env_key("PINTEREST_BOARD_ID", self.board_id)
                    log.info("Reusing existing board '%s' id=%s", name, self.board_id)
                    return self.board_id

        from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415
        description = (
            f"{cfg.DISPLAY_NAME or self.board_name} — recycled investigations, "
            "megaliths, and hidden history."
        )
        created = self.create_board(self.board_name, description)
        if not created or not created.get("id"):
            raise ValueError(
                f"Could not find or create Pinterest board '{self.board_name}'. "
                "Check boards:read / boards:write scopes on the access token."
            )
        self.board_id = str(created["id"]).strip()
        _write_env_key("PINTEREST_BOARD_ID", self.board_id)
        return self.board_id

    # ------------------------------------------------------------------
    # Private

    def _headers(self, content_type: str | None = "application/json") -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        allow_refresh: bool = True,
        **kwargs,
    ) -> requests.Response:
        """HTTP helper that retries once after a successful token refresh."""
        kwargs.setdefault("timeout", 30)
        headers = kwargs.pop("headers", None) or self._headers()
        resp = requests.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401 and allow_refresh and self._try_refresh():
            headers = self._headers()
            resp = requests.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            raise PinterestTokenExpiredError(
                "Pinterest access token expired and refresh failed. "
                "Run: python pinterest_oauth.py --channel <id> to re-authenticate."
            )
        return resp

    def _build_description(self, record: dict) -> str:
        """Keyword sentence plus the article line from ``pin_theme``. Max 500 chars."""
        from agents.posting.pinterest_engine.pin_copy import build_pin_description  # noqa: PLC0415

        return build_pin_description(record)

    def _try_refresh(self) -> bool:
        """
        Exchange PINTEREST_REFRESH_TOKEN for a new access token.
        On success: updates self.token and writes new token to .env.
        Returns True on success, False on failure.
        """
        if self._refreshed_this_session:
            log.warning("Already refreshed once this session; not retrying.")
            return False

        refresh_token = os.getenv("PINTEREST_REFRESH_TOKEN", "")
        app_id        = os.getenv("PINTEREST_APP_ID", "")
        app_secret    = os.getenv("PINTEREST_APP_SECRET", "")

        if not all([refresh_token, app_id, app_secret]):
            log.warning(
                "Auto-refresh skipped: PINTEREST_REFRESH_TOKEN, "
                "PINTEREST_APP_ID, or PINTEREST_APP_SECRET missing in .env."
            )
            return False

        creds = base64.b64encode(f"{app_id}:{app_secret}".encode()).decode()
        body = urllib.parse.urlencode({
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
        }).encode()
        req = urllib.request.Request(
            _TOKEN_URL,
            data=body,
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            log.error("Token refresh request failed: %s", exc)
            return False

        new_token = result.get("access_token", "")
        if not new_token:
            keys = sorted(result.keys()) if isinstance(result, dict) else []
            log.error("Refresh response missing access_token (fields=%s)", ",".join(keys))
            return False

        self.token = new_token
        self._refreshed_this_session = True
        _write_env_key("PINTEREST_ACCESS_TOKEN", new_token)
        if result.get("refresh_token"):
            _write_env_key("PINTEREST_REFRESH_TOKEN", result["refresh_token"])

        log.info("Access token refreshed and saved to %s.", _env_path().name)
        return True

    def _post_with_retry(self, payload: dict) -> dict | None:
        """POST with exponential backoff on 429; auto-refresh on 401."""
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    _PINS_ENDPOINT,
                    headers=self._headers(),
                    data=json.dumps(payload),
                    timeout=60,
                )
            except requests.RequestException as exc:
                log.error("Network error attempt %d/%d: %s", attempt, _MAX_RETRIES, exc)
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE_SEC * attempt)
                    continue
                return None

            if resp.status_code == 201:
                data = resp.json()
                log.info("Pin created! ID=%s", data.get("id", "?"))
                return data

            if resp.status_code == 401:
                log.warning("401 Unauthorized. Attempting token refresh...")
                if self._try_refresh():
                    log.info("Refresh succeeded. Retrying publish...")
                    continue   # retry with new token
                raise PinterestTokenExpiredError(
                    "Pinterest access token expired and refresh failed. "
                    "Run: python agents/posting/pinterest_oauth.py to re-authenticate."
                )

            if resp.status_code == 429:
                wait = _BACKOFF_BASE_SEC * (2 ** attempt)
                log.warning("Rate limited (429). Waiting %ds...", wait)
                time.sleep(wait)
                continue

            log.error(
                "Pinterest API HTTP %d attempt %d/%d: %s",
                resp.status_code, attempt, _MAX_RETRIES,
                resp.text[:400],
            )
            if attempt < _MAX_RETRIES:
                time.sleep(_BACKOFF_BASE_SEC)
                continue
            return None

        return None

    def _publish_video(
        self,
        record: dict,
        video_path: Path,
        cover_jpeg: bytes | None,
    ) -> dict | None:
        """
        Upload a local video (POST /v5/media + register) then create a video pin.
        Returns the pin payload on success, None on soft failure.
        """
        try:
            register = self._request("POST", _MEDIA_ENDPOINT, json={"media_type": "video"})
        except PinterestTokenExpiredError:
            raise
        except requests.RequestException as exc:
            log.error("Video media register failed: %s", exc)
            return None
        if register.status_code not in (200, 201):
            log.error("Video media register HTTP %d: %s", register.status_code, register.text[:300])
            return None
        media = register.json()
        media_id = str(media.get("media_id") or media.get("id") or "")
        upload_url = media.get("upload_url") or ""
        upload_params = media.get("upload_parameters") or {}
        if not media_id or not upload_url:
            log.error("Video register missing media_id/upload_url: %s", media)
            return None

        try:
            with open(video_path, "rb") as fh:
                files = {"file": (video_path.name, fh, "video/mp4")}
                up = requests.post(
                    upload_url,
                    data=upload_params,
                    files=files,
                    timeout=300,
                )
        except OSError as exc:
            log.error("Cannot read video %s: %s", video_path, exc)
            return None
        except requests.RequestException as exc:
            log.error("Video upload failed: %s", exc)
            return None
        if up.status_code not in (200, 201, 204):
            log.error("Video upload HTTP %d: %s", up.status_code, up.text[:300])
            return None

        status_url = f"{_MEDIA_ENDPOINT}/{media_id}"
        ready = False
        for attempt in range(12):
            time.sleep(2 if attempt else 1)
            try:
                st = self._request("GET", status_url, timeout=20)
            except (PinterestTokenExpiredError, requests.RequestException) as exc:
                log.warning("Video status poll failed: %s", exc)
                break
            if st.status_code != 200:
                continue
            state = str(st.json().get("status") or "").lower()
            if state in {"succeeded", "success", "ready", "registered"}:
                ready = True
                break
            if state in {"failed", "error"}:
                log.error("Video processing failed: %s", st.text[:200])
                return None
        if not ready:
            log.warning("Video %s not ready after polling — aborting video pin.", video_path.name)
            return None

        from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415
        title = (
            record.get("pinterest_title")
            or record.get("topic")
            or cfg.DEFAULT_TOPIC
        )[:100]
        media_source: dict = {
            "source_type": "video_id",
            "media_id": media_id,
        }
        if cover_jpeg:
            media_source["cover_image_content_type"] = "image/jpeg"
            media_source["cover_image_data"] = base64.b64encode(cover_jpeg).decode("ascii")
        payload = {
            "board_id": self.board_id,
            "title": title,
            "description": self._build_description(record),
            "link": _pin_link(record) or None,
            "media_source": media_source,
        }
        if not payload["link"]:
            payload.pop("link")
        return self._post_with_retry(payload)


# ---------------------------------------------------------------------------
# Protocol adapter
# ---------------------------------------------------------------------------
# The CLI still constructs PinterestPublisher itself. PinterestAccountPublisher
# is the shared publish(content, account) entry: active-account gate, the
# _omni idempotency ledger, and the per-account token overlay. Video pins
# are refused here and also inside PinterestPublisher.publish.

from typing import Any  # noqa: E402

from accounts.tokens import pinterest_account_env, remember_rotated_token  # noqa: E402
from distribution.publishers.base import (  # noqa: E402
    Account,
    Content,
    PublishResult,
    Publisher,
)


class PinterestAccountPublisher(Publisher):
    platform = "pinterest"

    def publish_once(self, content: Content, account: Account) -> PublishResult:
        from agents.posting.pinterest_engine import config as cfg
        from distribution.scheduling.inventory import is_video_filename

        if _is_video_pin(content, is_video_filename):
            return PublishResult(
                ok=False,
                platform=self.platform,
                idempotency_key=content.idempotency_key,
                error="video pins are disabled",
            )

        cfg.configure(channel_id=account.branding)
        # When no dotenv file was loaded, PinterestPublisher.__init__ calls
        # configure() with no channel and would drop the binding above.
        if not cfg.DOTENV_LOADED_FROM_FILE:
            cfg.DOTENV_LOADED_FROM_FILE = True

        record = _record_from_content(content)
        image = content.media_bytes or b""
        env_path = cfg.DOTENV_PATH
        try:
            with pinterest_account_env(account):
                publisher = PinterestPublisher(
                    access_token=None,
                    board_id=None,
                    require_board=True,
                )
                try:
                    response = publisher.publish(record, image)
                finally:
                    remember_rotated_token(account, env_path)
        except PinterestTokenExpiredError as exc:
            return PublishResult(
                ok=False,
                platform=self.platform,
                idempotency_key=content.idempotency_key,
                error=str(exc),
            )

        if not response:
            return PublishResult(
                ok=False,
                platform=self.platform,
                idempotency_key=content.idempotency_key,
                error="pinterest publish returned no pin id",
            )
        external_id = str(response.get("id") or "")
        return PublishResult(
            ok=bool(external_id),
            platform=self.platform,
            idempotency_key=content.idempotency_key,
            external_id=external_id or None,
            error=None if external_id else "pinterest response missing id",
        )


def _is_video_pin(content: Content, is_video_filename) -> bool:
    if content.media_path and is_video_filename(content.media_path):
        return True
    if str(content.payload.get("media_kind") or "").strip().lower() == "video":
        return True
    video = str(content.payload.get("local_video_path") or "")
    return bool(video and is_video_filename(video) and not content.media_bytes)


def _record_from_content(content: Content) -> dict[str, Any]:
    record = dict(content.payload)
    record.setdefault("post_id", content.idempotency_key)
    record.setdefault("pinterest_title", content.title)
    record.setdefault("pinterest_caption", content.body)
    record.setdefault("topic", content.title)
    if content.link and not record.get("target_url"):
        record["target_url"] = content.link
    record.setdefault("publication_status", {})
    return record
