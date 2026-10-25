# -*- coding: utf-8 -*-
"""
pinterest_engine.inventory
---------------------------
Master Inventory -- the single source of truth for all posts.

Merges outputs/library/*.json (individual post records) and
outputs/content_library.json (legacy UUID-indexed library) into one
canonical file: outputs/master_inventory.json.

Every entry carries:
  - Original content fields (original_caption, raw_fact_sheet, imgbb_url ...)
  - Pinterest sales fields (pinterest_title, pinterest_caption, visual_hook, target_url)
  - Multi-platform publication_status object
  - Resolved local_image_path (workspace-relative path repair)
  - channel_id (with legacy page_id read fallback)

This module is imported by scheduler.py, publisher.py, and pinterest_main.py.
"""
from __future__ import annotations
from shared.layer_bridge import Port, Symbol

import html
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from shared.pin_text import (
    fit_pin_title,
    normalize_pin_text,
    polish_pin_text,
    sanitize_pin_title,
    title_case_phrase,
)
from shared.video_still import extract_video_still

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MASTER_INVENTORY_FILENAME = "master_inventory.json"
SCHEMA_VERSION = "2.0"


def _target_url() -> str:
    cfg = Port('production.formats.pins.config')  # noqa: PLC0415
    return cfg.TARGET_URL


def _default_topic() -> str:
    cfg = Port('production.formats.pins.config')  # noqa: PLC0415
    return cfg.DEFAULT_TOPIC


_SOCIAL_CTA_RE = re.compile(
    r"(?:"
    r"[Cc]omment\s+[A-Z]+(?:\s+below)?(?:\s+and\s+I[^.!?]*)?"
    r"|[Tt]ype\s+[A-Z]+\s+(?:below|in\s+the\s+comments)[^.!?]*"
    r"|(?:[Ss]end\s+(?:me\s+)?a\s+(?:DM|message)|DM\s+me)[^.!?]*"
    r"|[Dd]rop\s+a\s+comment[^.!?]*"
    r")[.!?]?",
    re.MULTILINE,
)

# Matches system/researcher bracket tags that precede un-humanized drafts.
# e.g. "[Caption from researcher output - humanizer skipped]"
#      "[Researcher draft   humanizer unavailable]"
_RAW_DRAFT_TAG_RE = re.compile(
    r"^\s*\["
    r"(?:Caption from researcher|Researcher draft|humanizer|RAW FACT SHEET)[^\]]*"
    r"\]\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Markdown section headers used inside fact-sheet blocks:
# "**RAW FACT SHEET**", "**RAW FACT SHEET: Benefits of Celtic Salt**",
# "**1. Verified Mechanisms:**", etc.
_FACT_SHEET_HEADER_RE = re.compile(
    r"^\s*(?:"
    r"\*\*RAW FACT SHEET[^*]*\*\*:?"     # **RAW FACT SHEET** or **RAW FACT SHEET: Topic**
    r"|\*\*\d+\.\s+[^*]+\*\*:?"          # **1. Numbered Section Header:**
    r"|RAW FACT SHEET(?:[:\s].*)?"        # plain RAW FACT SHEET line (no stars)
    r")\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_BANNED_WORDS = ["unlock", "discover", "elevate", "game-changer",
                 "harness", "revolutionary", "dive"]

def _channel_cta_variants() -> list[str]:
    """
    Channel-scoped CTA templates from config pack / env.

    Empty list means: do not inject engine CTAs — keep inventory metadata as-is.
    """
    cfg = Port('production.formats.pins.config')  # noqa: PLC0415
    return list(cfg.CTA_VARIANTS or [])

# ---------------------------------------------------------------------------
# Publication status factory
# ---------------------------------------------------------------------------

def _empty_publication_status() -> dict:
    return {
        "posted_on_pinterest": False,
        "pinterest_pin_id": None,
        "pinterest_post_date": None,
        "posted_on_instagram": False,
        "instagram_post_id": None,
        "instagram_post_date": None,
        "posted_on_facebook": False,
        "facebook_post_id": None,
        "facebook_post_date": None,
    }


# ---------------------------------------------------------------------------
# Caption utilities (re-exported for use by other modules)
# ---------------------------------------------------------------------------

def has_social_cta(text: str) -> bool:
    """Return True if the text contains a social engagement CTA."""
    return bool(_SOCIAL_CTA_RE.search(text))


def is_raw_draft(text: str) -> bool:
    """
    Return True if the text is (or starts with) an un-humanized researcher draft.
    Catches patterns like:
      [Caption from researcher output - humanizer skipped]
      [Researcher draft   humanizer unavailable]
      **RAW FACT SHEET**
    """
    if not text:
        return False
    head = text.strip()[:300]
    return bool(_RAW_DRAFT_TAG_RE.search(head) or _FACT_SHEET_HEADER_RE.search(head))


def strip_draft_headers(text: str) -> str:
    """
    Strip system/researcher bracket tags and markdown fact-sheet wrappers.
    Preserves the underlying science content (bullet points, sentences).

    Examples of what gets removed:
      [Caption from researcher output - humanizer skipped]
      **RAW FACT SHEET**
      **1. Verified Mechanisms:**

    Returns cleaned text, or empty string if nothing useful remains.
    """
    if not text:
        return ""
    lines = text.splitlines()
    clean: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Skip system bracket tags
        if _RAW_DRAFT_TAG_RE.match(line):
            continue
        # Skip standalone "RAW FACT SHEET" / numbered markdown headers
        if _FACT_SHEET_HEADER_RE.match(line):
            continue
        clean.append(line)

    result = "\n".join(clean)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result


def clean_social_ctas(text: str) -> str:
    """Strip all social engagement CTA phrases from the text."""
    cleaned = _SOCIAL_CTA_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def inject_sales_cta(text: str, variant_idx: int = 0) -> str:
    """
    Append a rotating channel CTA when the active channel defines variants.

    If the channel pack has no CTA templates, return *text* unchanged so
    inventory/post metadata remains the sole source of caption content.
    """
    cfg = Port('production.formats.pins.config')  # noqa: PLC0415

    variants = _channel_cta_variants()
    if not variants:
        return text.rstrip()
    template = variants[variant_idx % len(variants)]
    cta = cfg.format_cta_variant(template, _target_url())
    if not cta.strip():
        return text.rstrip()
    return f"{text.rstrip()}\n\n{cta}"


def build_caption_regex(source: str, variant_idx: int = 0) -> str:
    """
    Fast regex-based caption transformation (no API call needed).

    Automatically strips raw researcher draft headers before processing,
    so this function is safe to call on any source regardless of whether
    the humanizer ran. CTAs/URLs come only from the active channel pack.
    """
    url = _target_url()
    variants = _channel_cta_variants()
    if variants:
        cfg = Port('production.formats.pins.config')  # noqa: PLC0415
        fallback = cfg.format_cta_variant(variants[0], url).strip()
    elif url:
        fallback = url
    else:
        fallback = ""

    if not source or source.strip() in ("PENDING_CAPTION", "pending", ""):
        return fallback

    # Strip any researcher draft wrapper before cleaning CTAs
    if is_raw_draft(source):
        source = strip_draft_headers(source)
        if not source.strip():
            return fallback

    cleaned = clean_social_ctas(source)
    result = inject_sales_cta(cleaned, variant_idx)
    for word in _BANNED_WORDS:
        result = re.sub(rf"\b{re.escape(word)}\b", "", result, flags=re.IGNORECASE)
    return re.sub(r"  +", " ", result).strip()


def build_title_template(topic: str) -> str:
    """Keyword title from the topic. No template suffix is appended."""
    return sanitize_pin_title(title_case_phrase(topic))


def build_visual_hook(topic: str) -> str:
    """Short 5-word ALL-CAPS hook for the image overlay."""
    return " ".join(normalize_pin_text(topic).upper().split()[:5])


def normalize_article_url(url: str) -> str:
    """Host plus article path. Scheme, www, query, fragment, and a trailing slash drop out.

    The path stays in the key. A bare host is not an article and produces no key.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+", "/", parts.path or "").rstrip("/").lower()
    if not host or not path:
        return ""
    return f"{host}{path}"


def normalize_topic_key(topic: str) -> str:
    """Full topic, lowercased. Shared words such as ancient or city stay in place."""
    return re.sub(r"[^a-z0-9]+", " ", (topic or "").lower()).strip()


_URL_FIELDS = ("article_url", "target_url", "link", "url", "article")


def article_match_keys(entry: dict) -> set[str]:
    """Identity keys for one pin: the article URL, plus the full topic.

    History and ledger rows do not share one name. ``article_url``,
    ``target_url``, ``link``, ``url``, and the ledger's ``article`` all
    normalize to the same ``url:`` key. The key is the whole host and path,
    and the whole topic. Shared words and a shared host do not match.
    """
    keys: set[str] = set()
    for field in _URL_FIELDS:
        url = normalize_article_url(str(entry.get(field) or ""))
        if url:
            keys.add("url:" + url)
    topic = normalize_topic_key(str(entry.get("topic") or ""))
    if topic:
        keys.add("topic:" + topic)
    return keys


def _row_has_url(row: dict) -> bool:
    return any(normalize_article_url(str(row.get(field) or "")) for field in _URL_FIELDS)


def _inventory_by_post_id(inventory: list[dict] | dict | None) -> dict[str, dict]:
    if not inventory:
        return {}
    entries = inventory.get("entries") if isinstance(inventory, dict) else inventory
    if not isinstance(entries, list):
        return {}
    found: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        post_id = str(entry.get("post_id") or "").strip()
        if post_id:
            found[post_id] = entry
    return found


def _fill_row_from_inventory(row: dict, index: dict[str, dict]) -> dict:
    """Copy URL and topic from master_inventory when the history row lacks them.

    Lines written before the article column, and ledger rows that only stored
    ``post_id``, still name the same article once the inventory entry is known.
    """
    post_id = str(row.get("post_id") or "").strip()
    saved = index.get(post_id)
    if not saved:
        return row
    has_topic = bool(normalize_topic_key(str(row.get("topic") or "")))
    if _row_has_url(row) and has_topic:
        return row
    merged = dict(saved)
    for key, value in row.items():
        if value not in (None, ""):
            merged[key] = value
    return merged


def dedup_limits() -> tuple[int, int]:
    """Recent window for article dedupe. ``0`` turns that axis off.

    ``PINTEREST_DEDUP_RECENT_PINS`` (default 30) and
    ``PINTEREST_DEDUP_RECENT_DAYS`` (default 7) are optional.
    """
    return (
        _nonnegative_env("PINTEREST_DEDUP_RECENT_PINS", 30),
        _nonnegative_env("PINTEREST_DEDUP_RECENT_DAYS", 7),
    )


def _nonnegative_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _parse_history_time(value: str) -> datetime | None:
    text = (value or "").strip()
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


def recent_match_keys(
    rows: list[dict],
    *,
    now: datetime | None = None,
    limit: int | None = None,
    within_days: int | None = None,
    inventory: list[dict] | dict | None = None,
) -> set[str]:
    """URL and topic keys from the newest history rows inside the window."""
    pin_limit, day_limit = dedup_limits()
    if limit is None:
        limit = pin_limit
    if within_days is None:
        within_days = day_limit
    if limit <= 0 and within_days <= 0:
        return set()
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = moment - timedelta(days=within_days) if within_days > 0 else None
    ordered = sorted(
        rows,
        key=lambda row: _parse_history_time(str(row.get("published_utc") or ""))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    index = _inventory_by_post_id(inventory)
    keys: set[str] = set()
    kept = 0
    for row in ordered:
        stamp = _parse_history_time(str(row.get("published_utc") or ""))
        if cutoff is not None and stamp is not None and stamp < cutoff:
            continue
        if limit > 0 and kept >= limit:
            break
        kept += 1
        keys |= article_match_keys(_fill_row_from_inventory(row, index))
    return keys


def take_fresh_articles(
    entries: list[dict],
    keys: set[str],
    count: int,
) -> tuple[list[dict], int]:
    """First ``count`` entries whose article URL or topic is not already recent."""
    chosen: list[dict] = []
    skipped = 0
    seen = set(keys)
    for entry in entries:
        identity = article_match_keys(entry)
        if identity and identity & seen:
            skipped += 1
            continue
        chosen.append(entry)
        seen |= identity
        if count > 0 and len(chosen) >= count:
            break
    return chosen, skipped


def copy_needs_writer(entry: dict, *, force: bool = False) -> bool:
    """True when saved pin copy is missing, unstamped, or from Claude."""
    if force:
        return True
    if not str(entry.get("pinterest_title") or "").strip():
        return True
    if not str(entry.get("pinterest_caption") or "").strip():
        return True
    provider = str(entry.get("copy_provider") or "").strip().lower()
    model = str(entry.get("copy_model") or "").strip().lower()
    generated = str(entry.get("copy_generated_at") or "").strip()
    if not provider or not model or not generated:
        return True
    if provider in {"template", "anthropic"} or "claude" in provider or "claude" in model:
        return True
    return False


def select_pin_batch(
    candidates: list[dict],
    history_rows: list[dict],
    ledger_rows: list[dict],
    count: int,
    *,
    now: datetime | None = None,
    inventory: list[dict] | dict | None = None,
) -> tuple[list[dict], int]:
    """Skip recent article URL or topic matches, then take the next candidates."""
    rows = [row for row in history_rows if isinstance(row, dict)]
    for row in ledger_rows:
        if not isinstance(row, dict) or row.get("pin_recorded") is False:
            continue
        rows.append(row)
    keys = recent_match_keys(rows, now=now, inventory=inventory)
    return take_fresh_articles(candidates, keys, count)


def _copy_stamp(provider: str, model: str) -> dict[str, str]:
    return {
        "copy_provider": provider,
        "copy_model": model,
        "copy_generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def validate_caption_safe(caption: str) -> tuple[bool, str]:
    """
    Check that the caption is Pinterest-safe for live publishing.
    Returns (is_valid: bool, reason: str).

    Fails on:
      - Social engagement CTAs ("Comment X", "DM me", etc.)
      - Missing target / sales URL (when TARGET_URL is configured)
      - Un-humanized researcher draft content (raw fact-sheet headers)
    """
    if is_raw_draft(caption):
        return False, "Contains un-humanized researcher draft header"
    if has_social_cta(caption):
        return False, "Contains social engagement CTA ('Comment', 'DM me', etc.)"
    url = _target_url()
    if url and url not in caption:
        return False, f"Missing target URL: {url}"
    return True, "OK"


# ---------------------------------------------------------------------------
# Image path resolution
# ---------------------------------------------------------------------------

def resolve_image_path(record: dict) -> str:
    """
    Try to find the actual image file for this record.
    Checks: local_image_path -> image_relative / image_path relative to
    the local workspace root (ENGINE_ROOT). Returns empty string if missing.
    """
    cfg = Port('production.formats.pins.config')  # noqa: PLC0415
    from shared.paths import coerce_outputs_path, factory_root

    # 1. Already resolved
    existing = record.get("local_image_path", "")
    if existing and Path(existing).is_file():
        return str(Path(existing).resolve())

    # 2. image_relative / image_path against OUTPUT_PATH, then the repo root
    for rel_field in ("image_relative", "image_path"):
        rel = record.get(rel_field, "")
        if not rel:
            continue
        for candidate in (
            coerce_outputs_path(rel),
            (cfg.OUTPUTS_DIR / rel),
            (factory_root() / rel),
        ):
            resolved = Path(candidate).expanduser()
            if resolved.is_file():
                return str(resolved.resolve())

    return ""


_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}


def resolve_video_path(record: dict) -> str:
    """
    Resolve a local video assigned to this library record.

    Checks ``local_video_path`` / ``video_path``, then ``clips/`` matches
    by subject slug or basename.
    """
    cfg = Port('production.formats.pins.config')  # noqa: PLC0415

    for field in ("local_video_path", "video_path"):
        raw = record.get(field, "")
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            from shared.paths import coerce_outputs_path, factory_root

            for trial in (
                coerce_outputs_path(raw),
                cfg.OUTPUTS_DIR / raw,
                factory_root() / raw,
            ):
                resolved = Path(trial).expanduser()
                if resolved.is_file():
                    return str(resolved.resolve())
        elif candidate.is_file():
            return str(candidate.resolve())

    slug = str(record.get("subject_slug") or "").strip()
    clips_dir = cfg.OUTPUTS_DIR / "clips"
    if slug and clips_dir.is_dir():
        needle = slug.replace("-", "_").lower()
        for clip in clips_dir.iterdir():
            if clip.suffix.lower() not in _VIDEO_EXTS:
                continue
            if needle and needle in clip.stem.lower():
                return str(clip.resolve())
    return ""


def _title_from_clip_name(filename: str) -> str:
    """Filename stem turned into words. Not a pin title — used only to reject that mistake."""
    stem = Path(filename).stem
    stem = re.sub(r"^reel_", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_v\d+$", "", stem, flags=re.IGNORECASE)
    words = [w for w in re.split(r"[_\-]+", stem) if w]
    topic = " ".join(words).strip() or "Ancient Mysteries"
    return topic[:80]


def is_video_filename(name: str) -> bool:
    """True when *name* is a video clip. Those files are never published as pins."""
    return Path(str(name or "")).suffix.lower() in _VIDEO_EXTS


# ---------------------------------------------------------------------------
# Claude AI generation (optional, called when use_ai=True)
# ---------------------------------------------------------------------------

_AI_USER_TPL = """\
TASK: Transform this post into Pinterest sales content.

TOPIC: {topic}

SOURCE CAPTION:
{caption}

RULES:
1. Remove every "Comment [KEYWORD]", "Type [KEYWORD]", "DM me" CTA entirely.
2. End with a humble, non-pushy line{url_rule}.
3. The pinterest_title must lead with the subject keyword, max 100 chars. No suffix such as "| Practical Guide" or "— Key Insights".
4. visual_hook is a truthful curiosity gap about the real topic, at most 8 words.

OUTPUT: Valid JSON only, no markdown:
{{"pinterest_title": "...", "pinterest_caption": "...", "visual_hook": "..."}}
"""


def generate_pin_copy(topic: str, source_text: str, *, variant_idx: int = 0) -> dict:
    """
    Build pin title, caption, and hook from article text.

    Writer_Primary on DeepInfra (``agents.mcp.text_model``) writes the fields.
    Claude is not used here. If that call fails, the same template builders
    as inventory sync fill the fields from the article topic and body. The
    caller must not pass a filename as *topic*.
    """
    topic = normalize_pin_text(topic)
    source = normalize_pin_text(source_text)
    if not topic and not source:
        return {}
    if not topic:
        topic = source.split(".")[0].strip()[:80] or source[:80]
    ai = _generate_cheap(topic, source, source, variant_idx)
    if ai.get("pinterest_title") and ai.get("pinterest_caption"):
        if not ai.get("visual_hook"):
            ai["visual_hook"] = build_visual_hook(topic)
        return ai
    return {
        "pinterest_title": build_title_template(topic),
        "pinterest_caption": build_caption_regex(source or topic, variant_idx),
        "visual_hook": build_visual_hook(topic),
    }


def _generate_deepseek(
    topic: str,
    caption: str,
    fact_sheet: str,
    variant_idx: int,
) -> dict:
    """Same JSON contract as ``_generate_ai``, via the DeepSeek chat API."""

    import requests  # noqa: PLC0415

    cfg = Port('production.formats.pins.config')  # noqa: PLC0415

    api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        return {}
    if not cfg.AI_SYSTEM_PROMPT and not caption and not fact_sheet:
        return {}

    source = caption if caption not in ("PENDING_CAPTION", "") else fact_sheet[:500]
    url = cfg.TARGET_URL
    url_rule = (
        f" pointing to: {url}\n   Example: \"Full details are linked in this pin.\""
        if url else
        " inviting the reader to learn more from the pin link"
    )
    user_msg = _AI_USER_TPL.format(topic=topic, caption=source or topic, url_rule=url_rule)
    base = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
    model = (
        os.getenv("DEEPSEEK_FLASH_MODEL")
        or os.getenv("DEEPSEEK_MODEL")
        or "deepseek-v4-flash"
    ).strip()
    if model.lower() in {"deepseek-chat", "deepseek-coder", "deepseek-chat-v3"}:
        model = "deepseek-v4-flash"
    system = cfg.AI_SYSTEM_PROMPT or (
        "You write Pinterest pin copy from the article text. "
        "Never use a filename as the title."
    )
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
    }
    dropped_format = False
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=60,
            )
        except requests.RequestException as exc:
            log.warning(
                "DeepSeek pin copy failed for '%s' (attempt %s): %s",
                topic,
                attempt + 1,
                type(exc).__name__,
            )
            continue
        if (
            resp.status_code == 400
            and not dropped_format
            and "response_format" in payload
        ):
            payload.pop("response_format", None)
            dropped_format = True
            log.info("DeepSeek rejected response_format; retrying without it")
            continue
        if resp.status_code != 200:
            log.warning(
                "DeepSeek pin copy HTTP %s for '%s' (attempt %s)",
                resp.status_code,
                topic,
                attempt + 1,
            )
            continue
        try:
            raw = resp.json()["choices"][0]["message"]["content"]
            data = _parse_pin_json(raw)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
            log.warning(
                "DeepSeek pin copy parse failed for '%s' (attempt %s): %s",
                topic,
                attempt + 1,
                type(exc).__name__,
            )
            continue
        return {
            "pinterest_title": sanitize_pin_title(str(data.get("pinterest_title", ""))),
            "pinterest_caption": polish_pin_text(str(data.get("pinterest_caption", ""))),
            "visual_hook": normalize_pin_text(str(data.get("visual_hook", ""))),
        }
    return {}


def _generate_cheap(
    topic: str,
    caption: str,
    fact_sheet: str,
    variant_idx: int,
) -> dict:
    """Pin copy through ``text_model`` Writer_Primary on DeepInfra. No Claude."""
    del variant_idx
    complete_writer_primary = Symbol('production.providers.llm.text_model', 'complete_writer_primary')  # noqa: PLC0415
    cfg = Port('production.formats.pins.config')  # noqa: PLC0415

    source = caption if caption not in ("PENDING_CAPTION", "") else fact_sheet[:500]
    url = cfg.TARGET_URL
    url_rule = (
        f" pointing to: {url}\n   Example: \"Full details are linked in this pin.\""
        if url else
        " inviting the reader to learn more from the pin link"
    )
    user_msg = _AI_USER_TPL.format(topic=topic, caption=source or topic, url_rule=url_rule)
    system = cfg.AI_SYSTEM_PROMPT or (
        "You write Pinterest pin copy from the article text. "
        "Never use a filename as the title."
    )
    try:
        result = complete_writer_primary(
            user_msg,
            system=system,
            json_mode=True,
            max_tokens=900,
            temperature=0.4,
        )
        data = _parse_pin_json(result.text)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "SpendCapExceeded":
            raise
        log.warning("Writer pin copy failed for '%s': %s", topic, type(exc).__name__)
        return {}
    parsed = {
        "pinterest_title": sanitize_pin_title(str(data.get("pinterest_title", ""))),
        "pinterest_caption": polish_pin_text(str(data.get("pinterest_caption", ""))),
        "visual_hook": normalize_pin_text(str(data.get("visual_hook", ""))),
    }
    if parsed.get("pinterest_title") and parsed.get("pinterest_caption"):
        parsed.update(
            _copy_stamp(
                str(getattr(result, "provider", "") or "deepinfra"),
                str(getattr(result, "model", "") or "deepseek-ai/DeepSeek-V4-Flash"),
            )
        )
        return parsed
    log.warning("Writer pin copy incomplete for '%s'", topic)
    return {}


def ensure_writer_pin_copy(
    entry: dict,
    *,
    use_ai: bool = True,
    force: bool = False,
) -> bool:
    """Regenerate pin copy with Writer_Primary when it is missing or from Claude.

    Templates fill the fields when the writer call fails. Returns True when
    the entry was rewritten.
    """
    if not copy_needs_writer(entry, force=force):
        return False
    topic = normalize_pin_text(str(entry.get("topic") or ""))
    source = str(entry.get("original_caption") or "")
    fact = str(entry.get("raw_fact_sheet") or "")
    try:
        variant = int(entry.get("variant_index") or 0)
    except (TypeError, ValueError):
        variant = 0
    ai_result = _generate_cheap(topic, source, fact, variant) if use_ai else {}
    if ai_result:
        entry.update(ai_result)
    else:
        entry["pinterest_title"] = ""
        entry["pinterest_caption"] = ""
        entry["visual_hook"] = ""
        fill_pin_templates(entry, source, variant)
        entry.update(_copy_stamp("template", "template"))
    finalize_pin_copy = Symbol('production.formats.pins.pin_copy', 'finalize_pin_copy')  # noqa: PLC0415

    finalize_pin_copy(entry)
    return True


def fill_pin_templates(entry: dict, source_caption: str, variant_idx: int) -> None:
    """Fill empty pin fields from the topic and the article text."""
    topic = entry["topic"]
    if not entry.get("pinterest_title"):
        entry["pinterest_title"] = build_title_template(topic)
    if not entry.get("pinterest_caption"):
        source = source_caption
        if is_raw_draft(source):
            log.debug(
                "  source_caption is raw draft for '%s' -- using raw_fact_sheet.", topic
            )
            fact = entry.get("raw_fact_sheet", "")
            source = strip_draft_headers(fact) if fact else ""
        entry["pinterest_caption"] = build_caption_regex(source, variant_idx)
    if not entry.get("visual_hook"):
        entry["visual_hook"] = build_visual_hook(topic)


_PIN_COPY_MAX_TOKENS = 4096
_AI_FAILURE_LIMIT_DEFAULT = 3
_ai_failure_streak = 0
_ai_circuit_open = False


def reset_ai_circuit() -> None:
    """Allow Claude calls again. One inventory build is one run."""
    global _ai_failure_streak, _ai_circuit_open
    _ai_failure_streak = 0
    _ai_circuit_open = False


def ai_circuit_open() -> bool:
    return _ai_circuit_open


def _ai_failure_limit() -> int:
    raw = (os.getenv("PINTEREST_AI_FAIL_LIMIT") or "").strip()
    if not raw:
        return _AI_FAILURE_LIMIT_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        return _AI_FAILURE_LIMIT_DEFAULT
    return value if value > 0 else _AI_FAILURE_LIMIT_DEFAULT


def _note_ai_failure(topic: str, reason: str) -> dict:
    global _ai_failure_streak, _ai_circuit_open
    _ai_failure_streak += 1
    log.warning("AI generation failed for '%s': %s", topic, reason)
    if _ai_failure_streak >= _ai_failure_limit():
        _ai_circuit_open = True
        log.warning(
            "AI circuit open after %d consecutive failures; "
            "template fallback for the rest of this run.",
            _ai_failure_streak,
        )
    return {}


def _claude_message_text(resp: object) -> str:
    """Join text blocks. ThinkingBlock has no .text and must be skipped."""
    blocks = getattr(resp, "content", None) or []
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict):
            kind = str(block.get("type") or "")
            text = block.get("text")
        else:
            kind = str(getattr(block, "type", "") or "")
            text = getattr(block, "text", None)
        if kind == "thinking" or text is None:
            continue
        parts.append(str(text))
    return "\n".join(parts).strip()


def _create_pin_message(client: object, *, model: str, system: str, user_msg: str):
    """Pin-copy call. Omit ``thinking``; sending disabled returns HTTP 400.

    Text blocks are read afterwards, so a ThinkingBlock in the response is skipped.
    Retired aliases and 3.x ids are replaced before the request.
    """
    from config import resolve_claude_model  # noqa: PLC0415

    return client.messages.create(  # type: ignore[attr-defined]
        model=resolve_claude_model(model),
        max_tokens=_PIN_COPY_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )


def _parse_pin_json(raw: str) -> dict:
    """Parse a pin-copy object, including fenced or prose-wrapped JSON."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise json.JSONDecodeError("no JSON object", text, 0)
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise json.JSONDecodeError("pin copy was not an object", text, start)
    return data


def _generate_ai(
    topic: str,
    caption: str,
    fact_sheet: str,
    client,
    model: str,
    variant_idx: int,
) -> dict:
    cfg = Port('production.formats.pins.config')  # noqa: PLC0415

    # No channel persona/prompt configured → skip AI (use inventory/templates).
    if not cfg.AI_SYSTEM_PROMPT:
        return {}

    source = caption if caption not in ("PENDING_CAPTION", "") else fact_sheet[:500]
    url = cfg.TARGET_URL
    url_rule = (
        f" pointing to: {url}\n   Example: \"Full details are linked in this pin.\""
        if url else
        " inviting the reader to learn more from the pin link"
    )
    global _ai_failure_streak
    if _ai_circuit_open:
        log.info("AI circuit open; template fallback for '%s'.", topic)
        return {}

    user_msg = _AI_USER_TPL.format(topic=topic, caption=source, url_rule=url_rule)
    try:
        resp = _create_pin_message(
            client, model=model, system=cfg.AI_SYSTEM_PROMPT, user_msg=user_msg
        )
        raw = _claude_message_text(resp)
        data = _parse_pin_json(raw)
    except Exception as exc:  # noqa: BLE001
        return _note_ai_failure(topic, type(exc).__name__)
    title = sanitize_pin_title(str(data.get("pinterest_title", "")))
    caption = polish_pin_text(str(data.get("pinterest_caption", "")))
    if not title and not caption:
        return _note_ai_failure(topic, "invalid JSON")
    _ai_failure_streak = 0
    return {
        "pinterest_title": title,
        "pinterest_caption": caption,
        "visual_hook": normalize_pin_text(str(data.get("visual_hook", ""))),
    }


# ---------------------------------------------------------------------------
# MasterInventory
# ---------------------------------------------------------------------------

class MasterInventory:
    """
    Manages the master_inventory.json file.

    Usage:
        inv = MasterInventory()
        data = inv.build(force_regenerate=False)   # first run
        data = inv.load()                          # subsequent runs
        unposted = inv.get_unposted(data)
        inv.mark_posted(data, post_id, pin_id)
        inv.save(data)
    """

    def __init__(self, outputs_dir: Path | None = None) -> None:
        cfg = Port('production.formats.pins.config')  # noqa: PLC0415

        self.outputs_dir: Path = outputs_dir or cfg.OUTPUTS_DIR
        self.library_dir: Path = (
            self.outputs_dir / "library" if outputs_dir is not None else cfg.LIBRARY_DIR
        )
        self.inventory_path: Path = self.outputs_dir / MASTER_INVENTORY_FILENAME
        self.content_library_path: Path = self.outputs_dir / "content_library.json"
        try:
            from shared.store import hydrate_state_file, restore_channel_state

            channel = self.outputs_dir.name
            restore_channel_state(channel)
            self.inventory_path = hydrate_state_file(self.inventory_path)
            self.content_library_path = hydrate_state_file(self.content_library_path)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Load / Save

    def load(self) -> dict:
        """Load master_inventory.json. Returns empty structure if not found."""
        if self.inventory_path.is_file():
            try:
                return json.loads(self.inventory_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to load master_inventory: %s", exc)
        return {"schema_version": SCHEMA_VERSION, "entries": []}

    def save(self, data: dict) -> None:
        """Atomically save master_inventory.json."""
        data["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
        tmp = self.inventory_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.inventory_path)
        try:
            from shared.store import sync_state_file

            sync_state_file(self.inventory_path)
        except Exception:
            pass
        log.debug("Master inventory saved (%d entries).", len(data.get("entries", [])))

    # ------------------------------------------------------------------
    # Build

    def build(
        self,
        use_ai: bool = True,
        ai_delay_sec: float = 0.8,
        force_regenerate: bool = False,
        limit: int | None = None,
        dry_run: bool = False,
        prefer_ids: list[str] | None = None,
    ) -> dict:
        """
        Build (or update) master_inventory.json.

        - Merges all library/*.json + content_library.json
        - Resolves local image paths
        - Generates pinterest_title / pinterest_caption / visual_hook
        - Preserves existing publication_status (never resets posted entries)

        Returns the inventory dict.
        """
        log.info("Building master inventory...")
        reset_ai_circuit()

        # Load existing inventory to preserve publication_status
        existing = self.load()
        existing_map: dict[str, dict] = {
            e["post_id"]: e for e in existing.get("entries", [])
        }

        # Load content_library for cross-reference (UUID + platforms)
        cl_map = self._load_content_library_map()
        log.info("  content_library entries: %d", len(cl_map))

        # Writer_Primary is the default. Claude copy and unstamped copy are rewritten.
        lib_files = sorted(self.library_dir.glob("post_*.json"))
        if prefer_ids:
            order = {post_id: index for index, post_id in enumerate(prefer_ids)}
            lib_files.sort(
                key=lambda path: (path.stem not in order, order.get(path.stem, 0), path.name)
            )
        log.info("  library JSON files: %d", len(lib_files))

        entries: list[dict] = []
        seen_ids: set[str] = set()
        generated = 0

        for lib_path in lib_files:
            post_id = lib_path.stem
            prev_meta = existing_map.get(post_id, {})
            has_saved_meta = bool(
                prev_meta.get("pinterest_title") and prev_meta.get("pinterest_caption")
            )
            needs_new = force_regenerate or copy_needs_writer(prev_meta)
            if limit is not None and needs_new and generated >= limit:
                if has_saved_meta:
                    entries.append(prev_meta)
                    seen_ids.add(post_id)
                continue
            try:
                raw = json.loads(lib_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("Cannot read %s: %s", lib_path.name, exc)
                continue

            cfg = Port('production.formats.pins.config')  # noqa: PLC0415

            post_id = lib_path.stem
            topic = raw.get("topic") or _default_topic()
            subject_slug = raw.get("subject_slug", "")
            variant_idx = raw.get("variant_index", 0)
            source_caption = raw.get("humanized_caption", "")
            raw_fact = raw.get("raw_fact_sheet", "")

            # Cross-reference with content_library
            cl_key = f"{subject_slug}_{variant_idx}"
            cl_entry = cl_map.get(cl_key, {})

            # Pull existing entry to preserve publication_status + pinterest fields
            prev = existing_map.get(post_id, {})
            pub_status = prev.get("publication_status") or _empty_publication_status()

            # channel_id preferred; fall back to legacy page_id on read
            channel_id = (
                cfg.get_record_channel_id(prev)
                or cfg.get_record_channel_id(raw)
                or cfg.CHANNEL_ID
                or ""
            )

            # Resolve local image / video paths (always re-check so new paths are found)
            combined = {**raw, **({"image_path": cl_entry.get("image_path", "")} if cl_entry else {})}
            local_img = resolve_image_path(combined)
            local_vid = resolve_video_path(combined)

            entry: dict = {
                "post_id": post_id,
                "channel_id": channel_id,
                "content_library_id": cl_entry.get("id", ""),
                "topic": topic,
                "subject_slug": subject_slug,
                "variant_index": variant_idx,
                "original_caption": source_caption,
                "raw_fact_sheet": raw_fact,
                "imgbb_url": raw.get("imgbb_url", ""),
                "image_relative": raw.get("image_relative", ""),
                "local_image_path": local_img,
                "local_video_path": local_vid,
                "media_kind": "image",
                "created_utc": raw.get("created_utc", ""),
                "target_url": (
                    str(prev.get("target_url") or "").strip()
                    if (cfg.CHANNEL_ID or "") == "ancient_knowledge"
                    else _target_url()
                ),
                "pinterest_title": prev.get("pinterest_title", ""),
                "pinterest_caption": prev.get("pinterest_caption", ""),
                "visual_hook": prev.get("visual_hook", ""),
                "copy_provider": prev.get("copy_provider", ""),
                "copy_model": prev.get("copy_model", ""),
                "copy_generated_at": prev.get("copy_generated_at", ""),
                "publication_status": pub_status,
            }

            if entry.get("pinterest_title"):
                entry["pinterest_title"] = sanitize_pin_title(entry["pinterest_title"])
            if entry.get("pinterest_caption"):
                entry["pinterest_caption"] = polish_pin_text(entry["pinterest_caption"])
            if entry.get("visual_hook"):
                entry["visual_hook"] = normalize_pin_text(entry["visual_hook"])

            if ensure_writer_pin_copy(entry, use_ai=use_ai, force=force_regenerate):
                log.info("  pin copy refreshed: %s (v%s)", topic, variant_idx)
                generated += 1
                if entry.get("pinterest_caption") and has_social_cta(entry["pinterest_caption"]):
                    entry["pinterest_caption"] = build_caption_regex(
                        entry["pinterest_caption"], variant_idx
                    )

            finalize_pin_copy = Symbol('production.formats.pins.pin_copy', 'finalize_pin_copy')  # noqa: PLC0415

            finalize_pin_copy(entry)
            entries.append(entry)   # always accumulate in memory
            seen_ids.add(post_id)

        for saved_id, saved in existing_map.items():
            if saved_id not in seen_ids and saved:
                entries.append(saved)

        recycled = self._recycle_unpaired_videos(entries, existing_map)
        if recycled:
            log.info("  recycled unpaired video clips: %d", recycled)

        cfg = Port('production.formats.pins.config')  # noqa: PLC0415

        data = {
            "schema_version": SCHEMA_VERSION,
            "channel_id": cfg.CHANNEL_ID or "",
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "total_entries": len(entries),
            "entries": entries,
        }

        if not dry_run:
            self.save(data)
            log.info(
                "Master inventory built: %d entries (%d new metadata generated).",
                len(entries), generated,
            )
        else:
            log.info(
                "[DRY RUN] Would write %d entries (%d with generated metadata).",
                len(entries), generated,
            )

        return data

    # ------------------------------------------------------------------
    # Query helpers

    def get_unposted(self, data: dict) -> list[dict]:
        """Return all entries where posted_on_pinterest == False."""
        return [
            e for e in data.get("entries", [])
            if not e.get("publication_status", {}).get("posted_on_pinterest", False)
            and str(e.get("media_kind") or "image").lower() != "video"
            and e.get("pinterest_title")
            and e.get("pinterest_caption")
        ]

    def get_entry(self, data: dict, post_id: str) -> dict | None:
        """Find an entry by post_id."""
        for e in data.get("entries", []):
            if e["post_id"] == post_id:
                return e
        return None

    def mark_posted(
        self,
        data: dict,
        post_id: str,
        pinterest_pin_id: str,
    ) -> None:
        """Update publication_status after a successful Pinterest publish."""
        entry = self.get_entry(data, post_id)
        if entry is None:
            log.warning("mark_posted: post_id '%s' not found.", post_id)
            return
        status = entry.setdefault("publication_status", _empty_publication_status())
        status["posted_on_pinterest"] = True
        status["pinterest_pin_id"] = pinterest_pin_id
        status["pinterest_post_date"] = datetime.now(timezone.utc).isoformat()

    def count_verified_images(self, data: dict) -> int:
        """Count entries with a verified local image path on disk."""
        return sum(
            1 for e in data.get("entries", [])
            if e.get("local_image_path") and Path(e["local_image_path"]).is_file()
        )

    # ------------------------------------------------------------------
    # Initialise history ledger

    def init_history_ledger(self) -> Path:
        """Create outputs/pinterest_history.json if it does not exist."""
        path = self.outputs_dir / "pinterest_history.json"
        if not path.is_file():
            path.write_text(
                json.dumps({"published": [], "errors": []}, indent=2),
                encoding="utf-8",
            )
            log.info("pinterest_history.json initialised (empty).")
        return path

    # ------------------------------------------------------------------
    # Pre-flight readiness check

    def check_readiness(self) -> dict[str, object]:
        """
        Run the pre-flight checklist.

        Returns a dict with individual check results and an overall 'ready' bool.
        Prints a formatted report to stdout.
        """

        checks: dict[str, object] = {}

        # 1. Token
        token = os.getenv("PINTEREST_ACCESS_TOKEN", "")
        checks["token_set"] = bool(token)
        checks["token_preview"] = (token[:12] + "...") if token else "(not set)"

        # 2. Board ID or auto-create name
        cfg = Port('production.formats.pins.config')  # noqa: PLC0415
        board_id = os.getenv("PINTEREST_BOARD_ID", "")
        board_name = os.getenv("PINTEREST_BOARD_NAME") or getattr(cfg, "BOARD_NAME", "")
        checks["board_id_set"] = bool(board_id or board_name)
        checks["board_id"] = board_id or f"(will auto-create: {board_name or '?'})"

        # 3. Master inventory exists
        inv_exists = self.inventory_path.is_file()
        checks["master_inventory_exists"] = inv_exists

        # 4. Verified images
        verified_count = 0
        total_entries = 0
        unposted_count = 0
        if inv_exists:
            data = self.load()
            total_entries = len(data.get("entries", []))
            verified_count = self.count_verified_images(data)
            unposted_count = len(self.get_unposted(data))
        checks["total_entries"] = total_entries
        checks["verified_images"] = verified_count
        checks["unposted_queue"] = unposted_count
        checks["has_20_verified"] = verified_count >= 20

        # 5. History ledger
        history_path = self.init_history_ledger()
        checks["history_initialized"] = history_path.is_file()

        # 6. Overall
        critical = ["token_set", "board_id_set", "master_inventory_exists",
                    "has_20_verified", "history_initialized"]
        checks["ready"] = all(checks[k] for k in critical)

        # Print report
        status = lambda ok: "OK " if ok else "FAIL"  # noqa: E731
        print("\n=== Pinterest Engine Pre-Flight Check ===")
        print(f"  [{status(checks['token_set'])}] Pinterest access token: {checks['token_preview']}")
        print(f"  [{status(checks['board_id_set'])}] Pinterest board ID: {checks['board_id']}")
        print(f"  [{status(checks['master_inventory_exists'])}] master_inventory.json exists")
        print(f"  [{status(checks['has_20_verified'])}] Verified G: Drive images: {verified_count} (need >= 20)")
        print(f"  [ OK] Total entries in inventory: {total_entries}")
        print(f"  [ OK] Unposted queue: {unposted_count} pins ready")
        print(f"  [{status(checks['history_initialized'])}] pinterest_history.json initialized")
        print(f"\n  {'[GREEN LIGHT] Ready to publish!' if checks['ready'] else '[RED] Fix the FAIL items above before publishing.'}")
        print()

        return checks

    # ------------------------------------------------------------------
    # Private helpers

    def _load_content_library_map(self) -> dict[str, dict]:
        """Return {subject_slug_variantN: entry} from content_library.json.
        Tries UTF-8, UTF-16, and UTF-8-sig to handle Google Drive encoding issues.
        """
        if not self.content_library_path.is_file():
            return {}
        raw_bytes = self.content_library_path.read_bytes()
        text = None
        for enc in ("utf-8-sig", "utf-16", "utf-8", "latin-1"):
            try:
                text = raw_bytes.decode(enc)
                break
            except (UnicodeDecodeError, ValueError):
                continue
        if text is None:
            log.warning("content_library.json: could not decode with any encoding.")
            return {}
        try:
            entries = json.loads(text)
            if not isinstance(entries, list):
                return {}
            result = {
                f"{e.get('subject_slug', '')}_{e.get('variant_index', 0)}": e
                for e in entries
                if isinstance(e, dict)
            }
            log.debug("content_library loaded: %d entries", len(result))
            return result
        except Exception as exc:  # noqa: BLE001
            log.warning("content_library.json parse failed: %s", exc)
            return {}

    def _load_ai_client(self) -> tuple:
        try:
            from config import claude_opted_in, resolve_claude_model  # noqa: PLC0415

            if not claude_opted_in():
                return None, None
            import anthropic  # noqa: PLC0415

            cfg = Port('production.formats.pins.config')  # noqa: PLC0415

            if not cfg.ANTHROPIC_API_KEY:
                return None, None
            client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)
            model = resolve_claude_model()
            log.info("AI client ready (model: %s)", model)
            return client, model
        except Exception as exc:  # noqa: BLE001
            log.debug("AI client init failed: %s", exc)
            return None, None

    def _fill_templates(
        self, entry: dict, source_caption: str, variant_idx: int
    ) -> None:
        """Fill empty pin fields from the topic and the article text."""
        fill_pin_templates(entry, source_caption, variant_idx)

    def _recycle_unpaired_videos(
        self,
        entries: list[dict],
        existing_map: dict[str, dict],
    ) -> int:
        """
        Video clips are not pins.

        ``clips/`` used to be turned into inventory rows titled from the
        filename. Image cards come from library images or B2 image objects.
        """
        del entries, existing_map
        return 0
