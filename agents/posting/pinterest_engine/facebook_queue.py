# -*- coding: utf-8 -*-
"""Queue a new Pinterest card as a Facebook post in Post Planner.

The sheet has three columns: date/time, caption, media URL. The card image
and both workbooks (the run file and the cumulative file) are uploaded under
``<channel>/postplanner/`` so the orchestrator can fetch them. A missing
Facebook export does not fail the pin.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

_PRESIGN_SECONDS = 604800  # B2 SigV4 maximum is seven days.
_XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_DEFAULT_TEMPLATE = "{hook}\n\n{lines}\n\n{url}"
_LINK_LINE = re.compile(r"https?://|read the full article", re.IGNORECASE)


def facebook_settings(context: dict | None) -> dict[str, Any]:
    """Page flag from the scheduler context, or from ``pinterest_accounts.yaml``."""
    ctx = context or {}
    channel = str(ctx.get("account_id") or "").strip()
    if not channel:
        from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415

        channel = str(cfg.CHANNEL_ID or "").strip()
    keys = ("facebook_postplanner", "facebook_page_name", "facebook_caption_template")
    if any(key in ctx for key in keys):
        return {
            "enabled": _truthy(ctx.get("facebook_postplanner")),
            "page_name": str(ctx.get("facebook_page_name") or "").strip(),
            "template": str(ctx.get("facebook_caption_template") or "").strip(),
            "channel_id": channel,
        }
    account = _account_for_channel(channel)
    extra = account.extra if account is not None else {}
    return {
        "enabled": _truthy(extra.get("facebook_postplanner")),
        "page_name": str(extra.get("facebook_page_name") or "").strip(),
        "template": str(extra.get("facebook_caption_template") or "").strip(),
        "channel_id": channel,
    }


def facebook_body_lines(record: dict) -> list[str]:
    """Two or three plain sentences. The article URL is added separately."""
    blobs: list[str] = []
    for key in ("original_caption", "humanized_caption", "pinterest_caption"):
        text = str(record.get(key) or "").strip()
        if text:
            blobs.append(text)
    topic = str(record.get("topic") or "").strip()
    title = str(record.get("pinterest_title") or "").strip()
    source = blobs[0] if blobs else topic
    lines: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+|\n+", source):
        sentence = part.strip()
        if not sentence or _LINK_LINE.search(sentence):
            continue
        if sentence not in lines:
            lines.append(sentence)
        if len(lines) == 3:
            break
    for extra in (topic, title):
        if len(lines) >= 2:
            break
        if extra and not _contains(lines, extra):
            lines.append(extra)
    return lines[:3]


def build_facebook_caption(
    record: dict,
    *,
    page_name: str = "",
    template: str = "",
) -> str:
    """Hook, two or three lines, then the article link."""
    from agents.posting.pinterest_engine.articles import resolve_pin_link  # noqa: PLC0415
    from agents.posting.pinterest_engine.pin_copy import (  # noqa: PLC0415
        _fill,
        card_hook_text,
    )

    hook = card_hook_text(record)
    lines = facebook_body_lines(record)
    url = resolve_pin_link(record)
    topic = str(record.get("topic") or "").strip()
    filled = _fill(
        (template or "").strip() or _DEFAULT_TEMPLATE,
        hook=hook,
        lines="\n".join(lines),
        line1=lines[0] if len(lines) > 0 else "",
        line2=lines[1] if len(lines) > 1 else "",
        line3=lines[2] if len(lines) > 2 else "",
        url=url,
        page=page_name,
        topic=topic,
    )
    return re.sub(r"\n{3,}", "\n\n", filled).strip()


def enqueue_facebook_card(
    record: dict,
    card_bytes: bytes,
    *,
    channel_id: str,
    page_name: str,
    planner_dir: Path,
    run_stamp: str,
    caption_template: str = "",
    client: Any | None = None,
    bucket: str | None = None,
    posting_time: str | None = None,
) -> bool:
    """Append one Post Planner row and upload the card plus both workbooks.

    Returns False when there is no media URL. Callers keep the pin either way.
    """
    post_id = str(record.get("post_id") or "card")
    try:
        media_url = _card_media_url(
            card_bytes,
            post_id=post_id,
            channel_id=channel_id,
            client=client,
            bucket=bucket,
        )
    except Exception as exc:  # noqa: BLE001
        log.info("Facebook media upload failed for %s (%s).", post_id, type(exc).__name__)
        return False
    if not media_url:
        log.info("No Facebook media URL for %s; Post Planner row skipped.", post_id)
        return False

    caption = build_facebook_caption(
        record,
        page_name=page_name,
        template=caption_template,
    )
    if posting_time is None:
        from agents.posting.post_planner import scheduled_bulk_post_display  # noqa: PLC0415

        posting_time = scheduled_bulk_post_display(
            variant_index=int(record.get("variant_index") or 0)
        )

    from agents.posting.post_planner import (  # noqa: PLC0415
        append_planner_row,
        append_postplanner_xlsx_row,
    )

    folder = Path(planner_dir)
    cumulative = folder / "cumulative.xlsx"
    append_planner_row(
        cumulative,
        posting_time=posting_time,
        caption=caption,
        media_url=media_url,
    )
    run_path = append_postplanner_xlsx_row(
        folder,
        run_stamp=run_stamp,
        posting_time=posting_time,
        caption=caption,
        media_url=media_url,
    )
    prefix = f"{channel_id.strip().strip('/')}/postplanner"
    _upload_workbook(cumulative, f"{prefix}/cumulative.xlsx", client=client, bucket=bucket)
    _upload_workbook(run_path, f"{prefix}/{run_path.name}", client=client, bucket=bucket)
    log.info(
        "Queued Facebook post for %s on page %s (%s).",
        post_id,
        page_name or channel_id,
        media_url,
    )
    return True


def _card_media_url(
    card_bytes: bytes,
    *,
    post_id: str,
    channel_id: str,
    client: Any | None,
    bucket: str | None,
) -> str:
    from agents.media.b2_client import media_bucket_name, upload_object  # noqa: PLC0415

    bucket_name = (bucket or "").strip() or media_bucket_name()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", post_id.strip()) or "card"
    key = f"{channel_id.strip().strip('/')}/postplanner/cards/{safe}.jpg"
    uploaded = False
    try:
        upload_object(
            key,
            card_bytes,
            client=client,
            bucket=bucket_name,
            content_type="image/jpeg",
        )
        uploaded = True
    except Exception as exc:  # noqa: BLE001
        log.info("B2 card upload failed (%s); trying ImgBB.", type(exc).__name__)
    if uploaded:
        signed = _presign_or_public(client, bucket_name, key)
        if signed:
            return signed
    return _imgbb_url(channel_id, safe, card_bytes)


def _presign_or_public(client: Any | None, bucket: str, key: str) -> str:
    signer = getattr(client, "generate_presigned_url", None) if client is not None else None
    if callable(signer):
        try:
            signed = signer(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=_PRESIGN_SECONDS,
            )
            if signed:
                return str(signed)
        except Exception as exc:  # noqa: BLE001
            log.info("B2 presign failed (%s); using the public URL.", type(exc).__name__)
    endpoint = (os.getenv("B2_ENDPOINT_URL") or "https://s3.us-east-005.backblazeb2.com").strip()
    host = urlsplit(endpoint).netloc or "s3.us-east-005.backblazeb2.com"
    return f"https://{bucket}.{host}/{key.lstrip('/')}"


def _imgbb_url(channel_id: str, safe_id: str, card_bytes: bytes) -> str:
    api_key = (os.getenv("IMGBB_API_KEY") or "").strip()
    if not api_key:
        return ""
    from agents.media.imgbb_client import upload_image_file_to_imgbb  # noqa: PLC0415
    from utils.pipeline_paths import temp_path  # noqa: PLC0415

    dest = temp_path(channel_id, "pinterest", f"fb_{safe_id}.jpg")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(card_bytes)
    try:
        url = upload_image_file_to_imgbb(api_key, dest) or ""
    finally:
        dest.unlink(missing_ok=True)
        _remove_empty_dirs(dest.parent)
    return url


def _remove_empty_dirs(start: Path) -> None:
    """Remove the scratch folder once the ImgBB upload no longer needs it."""
    current = start
    while True:
        try:
            current.rmdir()
        except OSError:
            return
        if current.name == "_tmp":
            return
        current = current.parent


def _upload_workbook(path: Path, key: str, *, client: Any | None, bucket: str | None) -> None:
    from agents.media.b2_client import upload_object  # noqa: PLC0415

    try:
        upload_object(key, path, client=client, bucket=bucket, content_type=_XLSX_TYPE)
    except Exception as exc:  # noqa: BLE001
        log.info("B2 postplanner workbook upload failed (%s): %s", type(exc).__name__, key)


def _account_for_channel(channel_id: str):
    if not channel_id:
        return None
    try:
        from omni.pinterest_accounts import load_pinterest_accounts  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    try:
        accounts = load_pinterest_accounts()
    except Exception:  # noqa: BLE001
        return None
    return next((row for row in accounts if row.account_id == channel_id), None)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _contains(lines: list[str], extra: str) -> bool:
    needle = extra.casefold()
    return any(needle in line.casefold() for line in lines)
