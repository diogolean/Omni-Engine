# -*- coding: utf-8 -*-
"""Content library contract.

:class:`ContentItem` (schema version 1) is the row production writes to
``channels_config/<channel>/store/content_library.json``, mirrored through
:mod:`shared.store`. Distribution reads those rows here and changes only
``posting_status`` via :func:`update_publish_status`.

:class:`DistributionRecord` is the pre-v1 shape (``schema_version`` ``"1.0"``).
Lean publish dicts, :class:`distribution.publishers.base.Content` dicts, and Pinterest library
objects are read through :func:`content_item_from_any`. This module does not
import production or distribution.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.0"

TITLE_MAX_CHARS = 100
X_CAPTION_MAX_CHARS = 280
YOUTUBE_TITLE_MAX_CHARS = 100
YOUTUBE_CATEGORY_SCIENCE_TECH = "28"
SHORTS_TAG = "#Shorts"
SHORTS_TITLE_SUFFIX = " #Shorts"

# Peak US engagement: 18:00 America/New_York (EDT → 22:00 UTC, EST → 23:00 UTC).
US_PEAK_TZ = ZoneInfo("America/New_York")
US_PEAK_HOUR = 18
US_PEAK_MINUTE = 0
US_PEAK_SLOT_INTERVAL = timedelta(days=1)
UTC_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

HIGH_RPM_HASHTAGS: tuple[str, ...] = (
    "#futuretech",
    "#artificialintelligence",
    "#tech",
)
HIGH_RPM_SEARCH_TAGS: tuple[str, ...] = (
    "futuretech",
    "artificialintelligence",
    "tech",
)

DEFAULT_HASHTAGS: tuple[str, ...] = (
    "#aiwake",
    "#ai",
    "#aiconsciousness",
    "#futuretech",
    "#artificialintelligence",
    "#tech",
)

DISTRIBUTION_PLATFORMS: tuple[str, ...] = (
    "youtube",
    "instagram",
    "tiktok",
    "facebook",
    "x",
    "pinterest",
    "kwai",
)

_WORD_CUT_RE = re.compile(r"\s+")


class PostingState(str, Enum):
    """Lifecycle of one network slot. Publishers own transitions off pending."""

    PENDING = "pending"
    SCHEDULED = "scheduled"
    POSTED = "posted"
    FAILED = "failed"
    SKIPPED = "skipped"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def first_us_peak_slot(*, now: datetime | None = None) -> datetime:
    """Tomorrow at 18:00 America/New_York (15:00 PT)."""
    current = (now or datetime.now(US_PEAK_TZ)).astimezone(US_PEAK_TZ)
    day = current.date() + timedelta(days=1)
    return datetime(
        day.year,
        day.month,
        day.day,
        US_PEAK_HOUR,
        US_PEAK_MINUTE,
        0,
        tzinfo=US_PEAK_TZ,
    )


def build_us_peak_slots(
    count: int,
    *,
    now: datetime | None = None,
    first: datetime | None = None,
    interval: timedelta | None = None,
) -> list[datetime]:
    start = first or first_us_peak_slot(now=now)
    start = start.astimezone(US_PEAK_TZ).replace(
        hour=US_PEAK_HOUR,
        minute=US_PEAK_MINUTE,
        second=0,
        microsecond=0,
    )
    gap = interval if isinstance(interval, timedelta) and interval.total_seconds() > 0 else US_PEAK_SLOT_INTERVAL
    return [start + (gap * index) for index in range(max(0, count))]


def slot_iso_utc(slot: datetime) -> str:
    """Strict UTC Z: ``YYYY-MM-DDTHH:MM:SSZ``."""
    utc = slot.astimezone(timezone.utc).replace(microsecond=0)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def is_utc_z(value: str | None) -> bool:
    return bool(UTC_Z_RE.match(str(value or "").strip()))


def coerce_scheduled_time(value: str | datetime | None) -> str:
    """Normalize a timestamp to ``YYYY-MM-DDTHH:MM:SSZ``. Empty stays empty."""
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return slot_iso_utc(aware)
    text = str(value or "").strip()
    if not text:
        return ""
    if is_utc_z(text):
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return slot_iso_utc(parsed)


_SHORTS_IN_TITLE = re.compile(r"(?:^|\s)#shorts\b", re.IGNORECASE)


def strip_shorts_title(title: str, *, limit: int = YOUTUBE_TITLE_MAX_CHARS) -> str:
    """Remove format hashtag ``#Shorts`` and fit the hook into *limit* chars."""
    clean = _WORD_CUT_RE.sub(" ", (title or "").strip())
    clean = _SHORTS_IN_TITLE.sub("", clean)
    clean = clean.strip(" -|—–,")
    return clip_text(clean, limit) if clean else ""


def ensure_shorts_title(title: str, *, limit: int = YOUTUBE_TITLE_MAX_CHARS) -> str:
    """Deprecated: Aiwake titles no longer append ``#Shorts``. Strips it instead."""
    return strip_shorts_title(title, limit=limit)


def merge_high_rpm_hashtags(hashtags: Iterable[str] | None = None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw in (*(hashtags or ()), *HIGH_RPM_HASHTAGS):
        token = " ".join(str(raw or "").split()).strip()
        if not token:
            continue
        if not token.startswith("#"):
            token = f"#{token.lstrip('#')}"
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(token)
    return merged


def merge_high_rpm_search_tags(tags: Iterable[str] | None = None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw in (*(tags or ()), *HIGH_RPM_SEARCH_TAGS):
        token = " ".join(str(raw or "").replace("#", " ").split()).strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        merged.append(token)
    return merged


def _nested_dict(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    cursor: Any = row
    for key in keys:
        if not isinstance(cursor, dict):
            return {}
        cursor = cursor.get(key)
    return cursor if isinstance(cursor, dict) else {}


def validate_queue_ready(
    row: dict[str, Any] | None,
    *,
    scheduled_time: str | None = None,
    require_scheduled_time: bool = True,
) -> list[str]:
    """Return schema errors that block the processing queue. Empty = ready."""
    errors: list[str] = []
    if not isinstance(row, dict):
        return ["row is not an object"]
    base = row.get("base_metadata")
    if not isinstance(base, dict) or not base:
        errors.append("base_metadata is missing")
    else:
        if not str(base.get("title") or "").strip():
            errors.append("base_metadata.title is empty")
        if not str(base.get("caption") or "").strip():
            errors.append("base_metadata.caption is empty")
        tags = base.get("search_tags")
        if not isinstance(tags, list) or not any(str(tag).strip() for tag in tags):
            errors.append("search_tags is empty")
    if require_scheduled_time:
        youtube = _nested_dict(row, "platform_overrides", "youtube")
        slot = scheduled_time if scheduled_time is not None else youtube.get("scheduled_time")
        if not is_utc_z(str(slot or "").strip()):
            errors.append(
                "platform_overrides.youtube.scheduled_time must be YYYY-MM-DDTHH:MM:SSZ"
            )
    return errors


def clip_text(text: str, limit: int) -> str:
    """Fit *text* into *limit* characters, preferring a word boundary."""
    clean = _WORD_CUT_RE.sub(" ", (text or "").strip())
    if limit <= 0:
        return ""
    if len(clean) <= limit:
        return clean
    window = clean[: max(1, limit - 1)].rstrip(" |—–,;:-")
    if " " in window:
        window = window.rsplit(" ", 1)[0].rstrip(" |—–,;:-")
    if not window:
        window = clean[: max(1, limit - 1)].rstrip()
    clipped = window + "…"
    return clipped if len(clipped) <= limit else window[:limit]


def default_posting_status() -> dict[str, str]:
    return {name: PostingState.PENDING.value for name in DISTRIBUTION_PLATFORMS}


def merge_posting_status(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Keep non-pending publisher writes; fill the rest from *incoming* or pending."""
    allowed = {state.value for state in PostingState}
    merged = default_posting_status()
    if isinstance(incoming, dict):
        for name in DISTRIBUTION_PLATFORMS:
            value = str(incoming.get(name) or "").strip().lower()
            if value in allowed:
                merged[name] = value
    if isinstance(existing, dict):
        for name in DISTRIBUTION_PLATFORMS:
            value = str(existing.get(name) or "").strip().lower()
            if value and value != PostingState.PENDING.value:
                merged[name] = value
    return merged


class BaseMetadata(BaseModel):
    """Channel-agnostic copy pack used when a network has no override."""

    model_config = ConfigDict(extra="allow")

    title: str = Field(..., max_length=TITLE_MAX_CHARS)
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    search_tags: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _clip_title(cls, value: str) -> str:
        return clip_text(value, TITLE_MAX_CHARS)

    @field_validator("hashtags", "search_tags", "hooks")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in value or []:
            token = " ".join(str(raw or "").split()).strip()
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
        return out


class XOverride(BaseModel):
    caption: str = Field(..., max_length=X_CAPTION_MAX_CHARS)

    @field_validator("caption")
    @classmethod
    def _clip_x(cls, value: str) -> str:
        return clip_text(value, X_CAPTION_MAX_CHARS)


class PinterestOverride(BaseModel):
    board_name: str = "AI Consciousness & Tech"
    destination_url: str = ""


class YouTubeOverride(BaseModel):
    """YouTube Shorts payload. Extra keys (video_id, scheduled_time) are legal."""

    model_config = ConfigDict(extra="allow")

    category_id: str = YOUTUBE_CATEGORY_SCIENCE_TECH
    is_short: bool = True
    title: str = ""
    caption: str = ""
    video_id: str = ""
    scheduled_time: str = ""

    @field_validator("category_id")
    @classmethod
    def _category(cls, value: str) -> str:
        return str(value or YOUTUBE_CATEGORY_SCIENCE_TECH).strip() or YOUTUBE_CATEGORY_SCIENCE_TECH

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str) -> str:
        return strip_shorts_title(str(value or "").strip())

    @field_validator("scheduled_time")
    @classmethod
    def _utc_z_slot(cls, value: str) -> str:
        return coerce_scheduled_time(value)


class PlatformOverrides(BaseModel):
    """Sparse per-network deltas. Unlisted networks use ``base_metadata``."""

    model_config = ConfigDict(extra="allow")

    x: XOverride
    pinterest: PinterestOverride = Field(default_factory=PinterestOverride)
    youtube: YouTubeOverride = Field(default_factory=YouTubeOverride)


class PostingStatus(BaseModel):
    youtube: PostingState = PostingState.PENDING
    instagram: PostingState = PostingState.PENDING
    tiktok: PostingState = PostingState.PENDING
    facebook: PostingState = PostingState.PENDING
    x: PostingState = PostingState.PENDING
    pinterest: PostingState = PostingState.PENDING
    kwai: PostingState = PostingState.PENDING

    def as_map(self) -> dict[str, str]:
        return {name: getattr(self, name).value for name in DISTRIBUTION_PLATFORMS}


class DistributionRecord(BaseModel):
    """One distributable asset. Serialises as a content_library row."""

    model_config = ConfigDict(extra="allow")

    schema_version: str = SCHEMA_VERSION
    channel_id: str
    post_type: str
    session_id: str = ""
    topic: str = ""
    video_path: str
    final_caption: str = ""
    timestamp: str = Field(default_factory=utcnow_iso)
    imgbb_url: str = ""
    transcript_path: str = ""
    asset_id: str = ""
    base_metadata: BaseMetadata
    platform_overrides: PlatformOverrides
    posting_status: PostingStatus = Field(default_factory=PostingStatus)

    def to_library_row(self) -> dict[str, Any]:
        """JSON row with lean aliases for existing Facebook/YouTube readers."""
        payload = self.model_dump(mode="json")
        caption = self.base_metadata.caption
        payload["final_caption"] = self.final_caption or caption
        payload["humanized_caption"] = caption
        payload["facebook_caption"] = caption
        payload.setdefault("imgbb_url", self.imgbb_url or "")
        return payload


def build_platform_overrides(
    *,
    x_caption: str,
    board_name: str = "AI Consciousness & Tech",
    destination_url: str = "",
    youtube_category_id: str = YOUTUBE_CATEGORY_SCIENCE_TECH,
    is_short: bool = True,
    youtube_title: str = "",
    youtube_caption: str = "",
    scheduled_time: str | datetime | None = "",
) -> PlatformOverrides:
    return PlatformOverrides(
        x=XOverride(caption=x_caption),
        pinterest=PinterestOverride(
            board_name=board_name,
            destination_url=destination_url,
        ),
        youtube=YouTubeOverride(
            category_id=str(youtube_category_id or YOUTUBE_CATEGORY_SCIENCE_TECH),
            is_short=bool(is_short),
            title=youtube_title or "",
            caption=youtube_caption or "",
            scheduled_time=coerce_scheduled_time(scheduled_time),
        ),
    )


def posting_status_from_map(raw: dict[str, Any] | None) -> PostingStatus:
    merged = merge_posting_status(raw, default_posting_status())
    return PostingStatus.model_validate(merged)


def content_library_path(channel: str, *, outputs_dir: Path | None = None) -> Path:
    """Primary: ``channels_config/<channel>/store/content_library.json``.

    Pass *outputs_dir* only for tests or an explicit override. Production
    callers should omit it so reads/writes go through the durable store.
    """
    if outputs_dir is not None:
        return Path(outputs_dir) / "content_library.json"
    from shared.paths import channel_store_dir

    return channel_store_dir(channel) / "content_library.json"


def load_distribution_library(path: Path) -> list[dict[str, Any]]:
    """Return library rows. Accepts a bare array or ``{entries: [...]}``."""
    from shared.store import hydrate_state_file

    path = hydrate_state_file(path)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        return [row for row in raw["entries"] if isinstance(row, dict)]
    return []


def save_distribution_library(path: Path, rows: Iterable[dict[str, Any]]) -> Path:
    from shared.store import sync_state_file

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = list(rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    sync_state_file(path)
    return path


def _row_identity(row: dict[str, Any]) -> tuple[str, str]:
    video = str(row.get("video_path") or "").replace("\\", "/").rstrip("/").lower()
    session = str(row.get("session_id") or "").strip().lower()
    return video, session


def _youtube_block(row: dict[str, Any]) -> dict[str, Any]:
    return dict(_nested_dict(row, "platform_overrides", "youtube"))


def _preserve_youtube_identity(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Keep live YouTube ids / slots when a catalog refresh omits them."""
    overrides = incoming.get("platform_overrides")
    if not isinstance(overrides, dict):
        return incoming
    existing_yt = _youtube_block(existing)
    incoming_yt = dict(overrides.get("youtube") or {}) if isinstance(overrides.get("youtube"), dict) else {}
    if existing_yt.get("video_id") and not incoming_yt.get("video_id"):
        incoming_yt["video_id"] = existing_yt["video_id"]
    existing_status = str((existing.get("posting_status") or {}).get("youtube") or "").lower()
    if existing_status in {"scheduled", "posted"} and existing_yt.get("scheduled_time"):
        incoming_yt["scheduled_time"] = existing_yt["scheduled_time"]
    elif not str(incoming_yt.get("scheduled_time") or "").strip() and existing_yt.get("scheduled_time"):
        incoming_yt["scheduled_time"] = existing_yt["scheduled_time"]
    merged_overrides = dict(overrides)
    merged_overrides["youtube"] = incoming_yt
    updated = dict(incoming)
    updated["platform_overrides"] = merged_overrides
    return updated


def upsert_distribution_row(
    path: Path,
    record: DistributionRecord | dict[str, Any],
) -> dict[str, Any]:
    """Insert or refresh one row. Never resets a non-pending posting_status."""
    incoming = record.to_library_row() if isinstance(record, DistributionRecord) else dict(record)
    incoming["posting_status"] = merge_posting_status(
        None, incoming.get("posting_status")
    )
    video_key, session_key = _row_identity(incoming)
    rows = load_distribution_library(path)
    updated = False
    for index, existing in enumerate(rows):
        existing_video, existing_session = _row_identity(existing)
        matched = bool(video_key and existing_video == video_key)
        if not matched and session_key and existing_session == session_key:
            matched = True
        if not matched:
            continue
        incoming = _preserve_youtube_identity(existing, incoming)
        merged = dict(existing)
        merged.update(incoming)
        merged["posting_status"] = merge_posting_status(
            existing.get("posting_status"), incoming.get("posting_status")
        )
        rows[index] = merged
        incoming = merged
        updated = True
        break
    if not updated:
        rows.append(incoming)
    save_distribution_library(path, rows)
    return incoming


CONTENT_ITEM_SCHEMA_VERSION = 1

# Keys old readers still look up. They ride along on the v1 row.
_PASSTHROUGH_KEYS = (
    "youtube_video_id",
    "subject_slug",
    "variant_index",
    "post_id",
    "idempotency_key",
    "pinterest_title",
    "pinterest_caption",
    "image_relative",
    "image_path",
    "local_image_path",
    "local_video",
    "clip_path",
    "media_path",
    "target_url",
    "b2_image_key",
)


class ContentItem(DistributionRecord):
    """One library row at schema version 1.

    Production is the only writer of new items. Distribution updates
    ``posting_status`` and does not replace the row.
    """

    schema_version: int = CONTENT_ITEM_SCHEMA_VERSION
    carousel_images: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _version_one(cls, value: Any) -> int:
        if value in (1, "1", "1.0", 1.0):
            return CONTENT_ITEM_SCHEMA_VERSION
        raise ValueError(f"ContentItem schema_version must be 1, got {value!r}")

    def to_library_row(self) -> dict[str, Any]:
        payload = super().to_library_row()
        if not payload.get("carousel_images"):
            payload.pop("carousel_images", None)
        payload["schema_version"] = CONTENT_ITEM_SCHEMA_VERSION
        return payload


def _title_or_untitled(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return "Untitled"
    text = clip_text(str(value or "").strip(), TITLE_MAX_CHARS)
    return text or "Untitled"


def _text(value: Any) -> str:
    if isinstance(value, (dict, list)) or value is None:
        return ""
    return str(value).strip()


def _passthrough(row: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for key in _PASSTHROUGH_KEYS:
        if key not in row:
            continue
        value = row[key]
        if value is None or value == "" or value == [] or value == {}:
            continue
        extra[key] = value
    return extra


def _status_payload(row: dict[str, Any]) -> dict[str, str]:
    raw = row.get("posting_status")
    if not isinstance(raw, dict):
        raw = None
    return posting_status_from_map(raw).model_dump(mode="json")


def _item_from_parts(
    row: dict[str, Any],
    *,
    channel_id: str,
    post_type: str,
    topic: str,
    title: str,
    caption: str,
    video_path: str,
    imgbb_url: str = "",
    session_id: str = "",
    asset_id: str = "",
    board_name: str = "",
    destination_url: str = "",
    timestamp: str = "",
    carousel_images: list[dict[str, Any]] | None = None,
) -> ContentItem:
    clean_title = _title_or_untitled(title)
    clean_caption = _text(caption) or clean_title
    data: dict[str, Any] = _passthrough(row)
    hashtags = row.get("hashtags")
    data.update(
        {
            "schema_version": CONTENT_ITEM_SCHEMA_VERSION,
            "channel_id": _text(channel_id) or _text(row.get("channel_id")) or "channel",
            "post_type": _text(post_type) or _text(row.get("post_type")) or "post",
            "session_id": _text(session_id),
            "asset_id": _text(asset_id),
            "topic": _text(topic),
            "video_path": _text(video_path),
            "final_caption": _text(row.get("final_caption")) or clean_caption,
            "imgbb_url": _text(imgbb_url) or _text(row.get("imgbb_url")),
            "base_metadata": {
                "title": clean_title,
                "caption": clean_caption,
                "hashtags": list(hashtags) if isinstance(hashtags, list) else [],
                "search_tags": [],
                "hooks": [],
            },
            "platform_overrides": build_platform_overrides(
                x_caption=clean_caption,
                board_name=_text(board_name) or "Pins",
                destination_url=_text(destination_url),
            ).model_dump(mode="json"),
            "posting_status": _status_payload(row),
        }
    )
    when = _text(timestamp) or _text(row.get("timestamp"))
    if when:
        data["timestamp"] = when
    images = carousel_images if carousel_images is not None else row.get("carousel_images")
    if isinstance(images, list) and images:
        data["carousel_images"] = images
    facebook = _text(row.get("facebook_caption"))
    if facebook:
        data["facebook_caption"] = facebook
    return ContentItem.model_validate(data)


def _is_distribution_row(row: dict[str, Any]) -> bool:
    return isinstance(row.get("base_metadata"), dict) and isinstance(
        row.get("platform_overrides"), dict
    )


def _is_omni_content(row: dict[str, Any]) -> bool:
    if _is_distribution_row(row):
        return False
    if not _text(row.get("idempotency_key")):
        return False
    return any(key in row for key in ("body", "media_path", "media_bytes", "link", "title"))


def _is_pinterest_library(row: dict[str, Any]) -> bool:
    if _is_distribution_row(row):
        return False
    return any(
        _text(row.get(key))
        for key in (
            "pinterest_title",
            "subject_slug",
            "image_relative",
            "local_image_path",
            "b2_image_key",
        )
    )


def _from_publish(row: dict[str, Any], *, channel_id: str) -> ContentItem:
    topic = _text(row.get("topic"))
    caption = (
        _text(row.get("final_caption"))
        or _text(row.get("caption"))
        or _text(row.get("humanized_caption"))
        or _text(row.get("facebook_caption"))
    )
    title = _text(row.get("title")) or topic or caption
    video = (
        _text(row.get("video_path"))
        or _text(row.get("clip_path"))
        or _text(row.get("local_video"))
    )
    return _item_from_parts(
        row,
        channel_id=_text(row.get("channel_id")) or channel_id,
        post_type=_text(row.get("post_type")),
        topic=topic,
        title=title,
        caption=caption,
        video_path=video,
        imgbb_url=_text(row.get("imgbb_url")),
        session_id=_text(row.get("session_id")),
        asset_id=_text(row.get("asset_id")),
        timestamp=_text(row.get("timestamp")),
    )


def _from_omni(row: dict[str, Any], *, channel_id: str) -> ContentItem:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    title = _text(row.get("title")) or _text(payload.get("title")) or _text(payload.get("topic"))
    caption = _text(row.get("body")) or _text(payload.get("caption")) or title
    link = _text(row.get("link"))
    key = _text(row.get("idempotency_key"))
    merged = dict(payload)
    merged.update(row)
    return _item_from_parts(
        merged,
        channel_id=_text(payload.get("channel_id")) or _text(row.get("channel_id")) or channel_id,
        post_type=_text(payload.get("post_type")) or _text(row.get("post_type")),
        topic=_text(payload.get("topic")) or _text(row.get("topic")) or title,
        title=title,
        caption=caption,
        video_path=_text(row.get("media_path")) or _text(payload.get("video_path")),
        imgbb_url=link if link.startswith("http") else _text(row.get("imgbb_url")),
        session_id=key,
        asset_id=key,
        destination_url=link if link.startswith("http") else "",
    )


def _from_pinterest(row: dict[str, Any], *, channel_id: str) -> ContentItem:
    title = _text(row.get("pinterest_title")) or _text(row.get("title")) or _text(row.get("topic"))
    caption = (
        _text(row.get("pinterest_caption"))
        or _text(row.get("final_caption"))
        or _text(row.get("caption"))
        or _text(row.get("description"))
        or title
    )
    media = (
        _text(row.get("video_path"))
        or _text(row.get("local_image_path"))
        or _text(row.get("image_path"))
        or _text(row.get("image_relative"))
    )
    post_id = _text(row.get("post_id")) or _text(row.get("subject_slug"))
    return _item_from_parts(
        row,
        channel_id=_text(row.get("channel_id")) or channel_id,
        post_type=_text(row.get("post_type")) or "pin",
        topic=_text(row.get("topic")) or title,
        title=title,
        caption=caption,
        video_path=media,
        session_id=post_id,
        asset_id=post_id,
        board_name=_text(row.get("board_name")) or _text(row.get("board")),
        destination_url=_text(row.get("target_url"))
        or _text(row.get("destination_url"))
        or _text(row.get("link")),
    )


def _minimal(row: dict[str, Any], *, channel_id: str) -> ContentItem:
    return _from_publish(
        {
            "topic": _text(row.get("topic")) or "Untitled",
            "final_caption": _text(row.get("final_caption")) or "Untitled",
            "video_path": _text(row.get("video_path")),
            "channel_id": channel_id or "channel",
            "post_type": "post",
            "title": "Untitled",
        },
        channel_id=channel_id or "channel",
    )


def content_item_from_any(row: dict[str, Any], *, channel_id: str = "") -> ContentItem:
    """Read one of the four stored shapes as :class:`ContentItem` v1."""
    if not isinstance(row, dict):
        raise TypeError("content row must be an object")
    if _is_distribution_row(row):
        try:
            data = dict(row)
            data["schema_version"] = CONTENT_ITEM_SCHEMA_VERSION
            return ContentItem.model_validate(data)
        except ValueError:
            pass
    try:
        if _is_omni_content(row):
            return _from_omni(row, channel_id=channel_id)
        if _is_pinterest_library(row):
            return _from_pinterest(row, channel_id=channel_id)
        return _from_publish(row, channel_id=channel_id)
    except ValueError:
        return _minimal(row, channel_id=channel_id)


def new_content_item(
    *,
    topic: str,
    final_caption: str,
    imgbb_url: str = "",
    video_path: str = "",
    carousel_images: list[dict[str, Any]] | None = None,
    channel_id: str = "",
    post_type: str = "",
    title: str = "",
    timestamp: str = "",
) -> ContentItem:
    """Build a v1 item. This is the production write shape."""
    row: dict[str, Any] = {
        "topic": topic,
        "final_caption": final_caption,
        "imgbb_url": imgbb_url,
        "video_path": video_path,
        "channel_id": channel_id,
        "post_type": post_type,
        "title": title or topic,
    }
    if carousel_images:
        row["carousel_images"] = list(carousel_images)
    if timestamp:
        row["timestamp"] = timestamp
    return _from_publish(row, channel_id=channel_id)


def load_content_items(path: Path, *, channel_id: str = "") -> list[ContentItem]:
    """Read a library file. Missing files yield an empty list.

    Accepts a bare array or ``{\"entries\": [...]}``. Each row is adapted
    from whichever of the four shapes it uses.
    """
    return [
        content_item_from_any(row, channel_id=channel_id)
        for row in load_distribution_library(path)
    ]


def update_publish_status(
    path: Path,
    *,
    platform: str,
    status: PostingState | str,
    video_path: str = "",
    session_id: str = "",
    asset_id: str = "",
) -> dict[str, Any]:
    """Write one ``posting_status`` field. Every other field stays as stored.

    A non-pending status is not moved back to ``pending``.
    """
    name = _text(platform).lower()
    if name not in DISTRIBUTION_PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}")
    state = status.value if isinstance(status, PostingState) else _text(status).lower()
    allowed = {item.value for item in PostingState}
    if state not in allowed:
        raise ValueError(f"unknown posting status {status!r}")
    if not any((_text(video_path), _text(session_id), _text(asset_id))):
        raise ValueError("video_path, session_id, or asset_id is required")

    rows = load_distribution_library(path)
    video_key = _text(video_path).replace("\\", "/").rstrip("/").lower()
    session_key = _text(session_id).lower()
    asset_key = _text(asset_id).lower()
    target: dict[str, Any] | None = None
    for row in rows:
        row_video, row_session = _row_identity(row)
        row_asset = _text(row.get("asset_id")).lower()
        if video_key and row_video == video_key:
            target = row
            break
        if session_key and row_session == session_key:
            target = row
            break
        if asset_key and row_asset == asset_key:
            target = row
            break
    if target is None:
        raise KeyError("content item not found")
    current = dict(target.get("posting_status") or {})
    current[name] = state
    target["posting_status"] = merge_posting_status(current, None)
    save_distribution_library(path, rows)
    return target


def _library_channel(path: Path) -> str:
    parts = Path(path).parts
    for index, part in enumerate(parts):
        if part == "channels_config" and index + 1 < len(parts):
            return parts[index + 1]
        if part == "store" and index > 0:
            return parts[index - 1]
    return ""


def _row_is_v1(row: dict[str, Any]) -> bool:
    if row.get("schema_version") != CONTENT_ITEM_SCHEMA_VERSION:
        return False
    try:
        ContentItem.model_validate(dict(row))
    except ValueError:
        return False
    return True


def _upgraded_row(row: dict[str, Any], *, channel_id: str) -> dict[str, Any]:
    item = content_item_from_any(row, channel_id=channel_id)
    facebook = _text(row.get("facebook_caption"))
    humanized = _text(row.get("humanized_caption"))
    merged = dict(row)
    merged.update(item.to_library_row())
    if facebook:
        merged["facebook_caption"] = facebook
    if humanized:
        merged["humanized_caption"] = humanized
    merged["schema_version"] = CONTENT_ITEM_SCHEMA_VERSION
    return merged


def upgrade_content_library(path: Path, *, channel_id: str = "") -> int:
    """Upgrade one library file to ContentItem v1. The first run writes ``*.bak``.

    A JSON array stays an array. An object with ``entries`` keeps that
    envelope. A single object stays an object. A file that is already v1 is
    not rewritten. Returns the number of rows written.
    """
    library = Path(path)
    if not library.is_file():
        return 0
    try:
        raw = json.loads(library.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {library}") from exc

    envelope: dict[str, Any] | None | str
    if isinstance(raw, list):
        rows = [row for row in raw if isinstance(row, dict)]
        envelope = None
    elif isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        rows = [row for row in raw["entries"] if isinstance(row, dict)]
        envelope = dict(raw)
    elif isinstance(raw, dict):
        rows = [raw]
        envelope = "single"
    else:
        raise ValueError(f"{library} is not a content library")
    if not rows or all(_row_is_v1(row) for row in rows):
        return 0

    slug = _text(channel_id) or _library_channel(library)
    upgraded = [_upgraded_row(row, channel_id=slug) for row in rows]
    backup = library.with_name(library.name + ".bak")
    if not backup.exists():
        backup.write_bytes(library.read_bytes())
    if envelope is None:
        payload: Any = upgraded
    elif envelope == "single":
        payload = upgraded[0] if upgraded else {}
    else:
        payload = dict(envelope)
        payload["entries"] = upgraded
    tmp = library.with_suffix(library.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(library)
    from shared.store import sync_state_file

    sync_state_file(library)
    return len(upgraded)


__all__ = [
    "DEFAULT_HASHTAGS",
    "DISTRIBUTION_PLATFORMS",
    "HIGH_RPM_HASHTAGS",
    "HIGH_RPM_SEARCH_TAGS",
    "CONTENT_ITEM_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "SHORTS_TAG",
    "TITLE_MAX_CHARS",
    "US_PEAK_HOUR",
    "US_PEAK_TZ",
    "X_CAPTION_MAX_CHARS",
    "YOUTUBE_CATEGORY_SCIENCE_TECH",
    "BaseMetadata",
    "ContentItem",
    "DistributionRecord",
    "PinterestOverride",
    "PlatformOverrides",
    "PostingState",
    "PostingStatus",
    "XOverride",
    "YouTubeOverride",
    "build_platform_overrides",
    "build_us_peak_slots",
    "clip_text",
    "coerce_scheduled_time",
    "content_item_from_any",
    "content_library_path",
    "default_posting_status",
    "ensure_shorts_title",
    "strip_shorts_title",
    "first_us_peak_slot",
    "is_utc_z",
    "load_content_items",
    "load_distribution_library",
    "new_content_item",
    "merge_high_rpm_hashtags",
    "merge_high_rpm_search_tags",
    "merge_posting_status",
    "posting_status_from_map",
    "save_distribution_library",
    "slot_iso_utc",
    "update_publish_status",
    "upgrade_content_library",
    "upsert_distribution_row",
    "utcnow_iso",
    "validate_queue_ready",
]
