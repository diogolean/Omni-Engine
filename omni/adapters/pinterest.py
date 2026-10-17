"""Pinterest adapter over ``agents.posting.pinterest_engine.publisher``.

The CLI (``agents/posting/pinterest_main.py --channel ...``) still constructs
``PinterestPublisher`` itself. This adapter is the shared ``publish(content, account)``
entry for new callers and for the multi-account token overlay.
"""

from __future__ import annotations

from typing import Any

from omni.accounts import pinterest_account_env, remember_rotated_token
from omni.models import Account, Content, PublishResult
from omni.publisher import Publisher


class PinterestAccountPublisher(Publisher):
    platform = "pinterest"

    def publish_once(self, content: Content, account: Account) -> PublishResult:
        from agents.posting.pinterest_engine import config as cfg
        from agents.posting.pinterest_engine.inventory import is_video_filename
        from agents.posting.pinterest_engine.publisher import (
            PinterestPublisher,
            PinterestTokenExpiredError,
        )

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
