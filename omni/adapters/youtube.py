"""YouTube adapter over ``agents.posting.youtube_publisher``.

``publish_envelope`` is a straight forwarder used by ``main.py`` so the
existing ``--publish-youtube`` path keeps its arguments and return value.
``YouTubeAccountPublisher.publish`` is the gated interface (active flag,
idempotency key, retries only when the upload raises ``TransientPublishError``).
Chunk-level retries stay inside ``upload_short``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from omni.models import Account, Content, PublishResult
from omni.publisher import TRANSIENT_STATUS, Publisher, TransientPublishError


def publish_envelope(
    row: dict,
    page_name: str,
    privacy_status: str = "private",
    publish_at: Optional[datetime] = None,
    client_secrets_path: Optional[str | Path] = None,
    token_dir: Optional[str | Path] = None,
    youtube=None,
) -> tuple[str, str, Optional[datetime]]:
    """Forward to the existing uploader. Does not consult ``brandings.yaml``."""
    from agents.posting.youtube_publisher import upload_short_from_envelope

    return upload_short_from_envelope(
        row=row,
        page_name=page_name,
        privacy_status=privacy_status,
        publish_at=publish_at,
        client_secrets_path=client_secrets_path,
        token_dir=token_dir,
        youtube=youtube,
    )


class YouTubeAccountPublisher(Publisher):
    platform = "youtube"

    def publish_once(self, content: Content, account: Account) -> PublishResult:
        from agents.posting.youtube_publisher import upload_short_from_envelope

        row = dict(content.payload)
        if content.media_path and not row.get("video_path"):
            row["video_path"] = content.media_path
        if content.title and not row.get("title"):
            row["title"] = content.title
        if content.body and not row.get("caption"):
            row["caption"] = content.body
        page_name = account.account_id or account.branding
        token_dir = None
        if account.token_file:
            token_dir = str(Path(account.token_file).expanduser().parent)
        try:
            video_id, _url, _publish_at = upload_short_from_envelope(
                row=row,
                page_name=page_name,
                token_dir=token_dir,
            )
        except Exception as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status in TRANSIENT_STATUS:
                raise TransientPublishError(str(exc), status_code=int(status)) from exc
            return PublishResult(
                ok=False,
                platform=self.platform,
                idempotency_key=content.idempotency_key,
                error=str(exc),
            )
        if not video_id:
            return PublishResult(
                ok=False,
                platform=self.platform,
                idempotency_key=content.idempotency_key,
                error="youtube upload returned no video id",
            )
        return PublishResult(
            ok=True,
            platform=self.platform,
            idempotency_key=content.idempotency_key,
            external_id=str(video_id),
        )
