"""One Pinterest wake: image cards from B2, titled from the article.

The VM scheduler used to publish video clips and name them from the
filename. This drip lists image objects, downloads only the file it is
about to publish, builds the same 2:3 card as the PC engine, and links
the pin to the related article. The temp file is deleted afterwards.
Nothing in B2 is deleted.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from omni.accounts import is_unset, pinterest_account_env, remember_rotated_token
from omni.models import Account, Content
from omni.pinterest_accounts import account_blockers
from omni.publisher import FileIdempotencyStore

log = logging.getLogger("omni.pinterest_drip")

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_TAG_RE = re.compile(r"<[^>]+>")
_REEL_NAME = re.compile(r"^reel_|_v\d+$", re.IGNORECASE)


@dataclass
class Article:
    url: str
    topic: str
    body: str
    slug: str
    pinterest_title: str = ""
    pinterest_caption: str = ""
    visual_hook: str = ""


@dataclass
class PublishedPin:
    account: str
    b2_object: str
    article: str
    pin_id: str
    title: str
    published_utc: str


def published_pins_path() -> Path:
    override = (os.getenv("PINTEREST_PUBLISHED_PINS_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    from utils.pipeline_paths import outputs_root  # noqa: PLC0415

    return outputs_root() / "pinterest" / "published_pins.json"


class PublishedPins:
    """Persistent record of account + B2 object + article on the outputs volume."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or published_pins_path()

    def seen_object(self, account: str, b2_object: str) -> bool:
        return any(
            row.account == account and row.b2_object == b2_object
            for row in self._load()
        )

    def record(
        self,
        *,
        account: str,
        b2_object: str,
        article: str,
        pin_id: str,
        title: str,
    ) -> None:
        rows = self._load()
        if any(row.account == account and row.b2_object == b2_object for row in rows):
            return
        rows.append(
            PublishedPin(
                account=account,
                b2_object=b2_object,
                article=article,
                pin_id=pin_id,
                title=title,
                published_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )
        self._save(rows)

    def _load(self) -> list[PublishedPin]:
        if not self.path.is_file():
            return []
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        raw_rows = loaded.get("pins") if isinstance(loaded, dict) else None
        if not isinstance(raw_rows, list):
            return []
        rows: list[PublishedPin] = []
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            rows.append(
                PublishedPin(
                    account=str(raw.get("account") or ""),
                    b2_object=str(raw.get("b2_object") or ""),
                    article=str(raw.get("article") or ""),
                    pin_id=str(raw.get("pin_id") or ""),
                    title=str(raw.get("title") or ""),
                    published_utc=str(raw.get("published_utc") or ""),
                )
            )
        return rows

    def _save(self, rows: list[PublishedPin]) -> None:
        payload = {
            "pins": [
                {
                    "account": row.account,
                    "b2_object": row.b2_object,
                    "article": row.article,
                    "pin_id": row.pin_id,
                    "title": row.title,
                    "published_utc": row.published_utc,
                }
                for row in rows
            ]
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def publish_account(
    account: Account,
    *,
    pins: int = 1,
    dry_run: bool = False,
    client: Any | None = None,
    bucket: str | None = None,
) -> dict:
    """Publish up to *pins* image cards for one account."""
    stats = {
        "published": 0,
        "skipped": 0,
        "errors": 0,
        "remaining": 0,
        "dry_run": dry_run,
    }
    blockers = account_blockers(account)
    if blockers:
        log.info(
            "Pinterest account %s skipped until config is set: %s",
            account.account_id,
            ", ".join(blockers),
        )
        stats["skipped_reason"] = ",".join(blockers)
        return stats

    from agents.media.b2_client import (  # noqa: PLC0415
        download_object,
        list_object_keys,
        media_bucket_name,
        read_object_text,
    )
    from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415
    from agents.posting.pinterest_engine.image_transformer import PinTransformer  # noqa: PLC0415
    from omni.adapters.pinterest import PinterestAccountPublisher  # noqa: PLC0415

    cfg.configure(channel_id=account.branding)
    if not cfg.DOTENV_LOADED_FROM_FILE:
        cfg.DOTENV_LOADED_FROM_FILE = True

    prefix = account.extra.get("b2_prefix", "")
    bucket_name = (bucket or "").strip() or media_bucket_name()
    try:
        keys = list_object_keys(prefix, client=client, bucket=bucket_name)
    except Exception as exc:  # noqa: BLE001
        log.error("B2 list failed for %s: %s", account.account_id, type(exc).__name__)
        stats["errors"] = 1
        return stats

    ledger = PublishedPins()
    images = [key for key in keys if _is_image_key(key)]
    videos = [key for key in keys if _is_video_key(key)]
    if videos:
        log.info(
            "Skipping %d video object(s) under %s",
            len(videos),
            prefix or "/",
        )
    pending = [key for key in images if not ledger.seen_object(account.account_id, key)]
    stats["remaining"] = len(pending)
    if pins < 1 or not pending:
        return stats

    store = FileIdempotencyStore(ledger.path.with_name("publish_ledger.json"))
    publisher = PinterestAccountPublisher(store=store, max_retries=1)
    transformer = PinTransformer(outputs_dir=cfg.OUTPUTS_DIR)
    site = account.extra.get("site_url", "")

    for key in pending:
        if stats["published"] >= pins:
            break
        article = _resolve_article(
            account,
            key,
            site=site,
            client=client,
            bucket=bucket_name,
            read_text=read_object_text,
        )
        if article is None:
            log.info("Skipping %s — no article content (titles are not taken from filenames)", key)
            stats["skipped"] += 1
            continue
        previous_url = cfg.TARGET_URL
        cfg.TARGET_URL = article.url
        try:
            fields = _pin_fields(article, key)
        finally:
            cfg.TARGET_URL = previous_url
        if fields is None:
            log.info("Skipping %s — pin copy was empty or looked like a filename", key)
            stats["skipped"] += 1
            continue

        dest = _temp_image_path(account.branding, key)
        try:
            download_object(key, dest, client=client, bucket=bucket_name)
            record = _record(account, key, article, fields, dest)
            cfg.TARGET_URL = article.url
            try:
                pin_bytes = transformer.get_pin_bytes(record)
            finally:
                cfg.TARGET_URL = previous_url
            if not pin_bytes:
                log.warning("Image card failed for %s", key)
                stats["errors"] += 1
                continue

            idem = _idempotency_key(account.account_id, key, article.url)
            if dry_run:
                log.info(
                    "[DRY RUN] account=%s b2=%s article=%s title=%s bytes=%d",
                    account.account_id,
                    key,
                    article.url,
                    fields["pinterest_title"],
                    len(pin_bytes),
                )
                stats["published"] += 1
                continue

            content = Content(
                idempotency_key=idem,
                title=fields["pinterest_title"],
                body=fields["pinterest_caption"],
                link=article.url,
                media_path=str(dest),
                media_bytes=pin_bytes,
                payload=record,
            )
            with pinterest_account_env(account):
                try:
                    result = publisher.publish(content, account)
                finally:
                    remember_rotated_token(account, cfg.DOTENV_PATH)
            if result.duplicate:
                ledger.record(
                    account=account.account_id,
                    b2_object=key,
                    article=article.url,
                    pin_id=result.external_id or "",
                    title=fields["pinterest_title"],
                )
                stats["skipped"] += 1
                continue
            if not result.ok or not result.external_id:
                log.warning(
                    "Pin failed for %s: %s",
                    key,
                    result.error or "no pin id",
                )
                stats["errors"] += 1
                continue
            ledger.record(
                account=account.account_id,
                b2_object=key,
                article=article.url,
                pin_id=result.external_id,
                title=fields["pinterest_title"],
            )
            stats["published"] += 1
            log.info(
                "Published image pin account=%s b2=%s article=%s pin=%s",
                account.account_id,
                key,
                article.url,
                result.external_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Pin error for %s: %s", key, type(exc).__name__)
            stats["errors"] += 1
        finally:
            dest.unlink(missing_ok=True)

    stats["remaining"] = max(0, len(pending) - stats["published"])
    return stats


def _idempotency_key(account: str, b2_object: str, article: str) -> str:
    return f"pinterest:{account}:{b2_object}:{article}"


def _temp_image_path(channel: str, object_key: str) -> Path:
    from utils.pipeline_paths import temp_path  # noqa: PLC0415

    return temp_path(channel, "pinterest", Path(object_key).name)


def _is_image_key(key: str) -> bool:
    return Path(key).suffix.lower() in _IMAGE_EXTS


def _is_video_key(key: str) -> bool:
    from agents.posting.pinterest_engine.inventory import is_video_filename  # noqa: PLC0415

    return is_video_filename(key)


def _sidecar_key(object_key: str) -> str:
    return str(Path(object_key).with_suffix(".json")).replace("\\", "/")


def _lookup_slug(object_key: str) -> str:
    """Slug used only to find an article. Reel export names are not slugs."""
    stem = Path(object_key).stem
    if _REEL_NAME.search(stem):
        return ""
    return stem


def _article_url(site: str, slug: str, explicit: str = "") -> str:
    if explicit and not is_unset(explicit):
        return explicit.rstrip("/")
    if is_unset(site) or not slug:
        return ""
    return f"{site.rstrip('/')}/{slug.strip('/')}"


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", html or "")).strip()


def _title_is_filename(title: str, object_key: str) -> bool:
    from agents.posting.pinterest_engine.inventory import _title_from_clip_name  # noqa: PLC0415

    text = (title or "").strip().lower()
    if not text:
        return False
    name = Path(object_key).name
    stem = Path(object_key).stem
    banned = {
        name.lower(),
        stem.lower(),
        stem.replace("_", " ").replace("-", " ").lower(),
        _title_from_clip_name(name).lower(),
    }
    return text in banned


def _pin_fields(article: Article, object_key: str) -> dict[str, str] | None:
    from agents.posting.pinterest_engine.inventory import (  # noqa: PLC0415
        build_visual_hook,
        generate_pin_copy,
    )

    title = article.pinterest_title.strip()
    caption = article.pinterest_caption.strip()
    hook = article.visual_hook.strip()
    if _title_is_filename(title, object_key):
        title = ""
    topic = article.topic.strip()
    if _title_is_filename(topic, object_key):
        topic = ""
    body = article.body.strip()
    if title and caption and not _title_is_filename(title, object_key):
        return {
            "pinterest_title": title[:100],
            "pinterest_caption": caption,
            "visual_hook": hook or build_visual_hook(topic or title),
        }
    if not topic and not body:
        return None
    generated = generate_pin_copy(topic, body or topic)
    gen_title = str(generated.get("pinterest_title") or "")
    gen_caption = str(generated.get("pinterest_caption") or "")
    if not gen_title or not gen_caption or _title_is_filename(gen_title, object_key):
        return None
    return {
        "pinterest_title": gen_title[:100],
        "pinterest_caption": gen_caption,
        "visual_hook": str(generated.get("visual_hook") or build_visual_hook(topic or gen_title)),
    }


def _record(
    account: Account,
    object_key: str,
    article: Article,
    fields: dict[str, str],
    image_path: Path,
) -> dict[str, Any]:
    slug = article.slug or _lookup_slug(object_key) or "pin"
    return {
        "post_id": slug,
        "channel_id": account.branding,
        "topic": article.topic or fields["pinterest_title"],
        "subject_slug": slug,
        "variant_index": 0,
        "original_caption": article.body,
        "raw_fact_sheet": article.body,
        "humanized_caption": article.body,
        "pinterest_title": fields["pinterest_title"],
        "pinterest_caption": fields["pinterest_caption"],
        "visual_hook": fields["visual_hook"],
        "target_url": article.url,
        "local_image_path": str(image_path),
        "local_video_path": "",
        "media_kind": "image",
        "b2_object": object_key,
        "publication_status": {"posted_on_pinterest": False},
    }


def _resolve_article(
    account: Account,
    object_key: str,
    *,
    site: str,
    client: Any | None,
    bucket: str,
    read_text,
) -> Article | None:
    sidecar_raw = read_text(_sidecar_key(object_key), client=client, bucket=bucket)
    if sidecar_raw:
        try:
            data = json.loads(sidecar_raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            article = _article_from_mapping(data, site=site, object_key=object_key)
            if article is not None:
                return article
    library = _article_from_library(account.branding, object_key, site)
    if library is not None:
        return library
    slug = _lookup_slug(object_key)
    if slug and not is_unset(site):
        return fetch_wordpress_article(site, slug)
    return None


def _article_from_mapping(data: dict, *, site: str, object_key: str) -> Article | None:
    slug = str(
        data.get("slug") or data.get("article_slug") or _lookup_slug(object_key) or ""
    ).strip()
    explicit = str(
        data.get("article_url") or data.get("target_url") or data.get("url") or ""
    ).strip()
    topic = str(data.get("topic") or data.get("title") or "").strip()
    body = str(
        data.get("humanized_caption")
        or data.get("content")
        or data.get("body")
        or data.get("excerpt")
        or ""
    ).strip()
    title = str(data.get("pinterest_title") or "").strip()
    caption = str(data.get("pinterest_caption") or "").strip()
    hook = str(data.get("visual_hook") or "").strip()
    if _title_is_filename(topic, object_key):
        topic = ""
    if _title_is_filename(title, object_key):
        title = ""
    if not any((topic, body, title, caption)):
        return None
    url = _article_url(site, slug, explicit)
    if not url:
        return None
    return Article(
        url=url,
        topic=topic,
        body=body,
        slug=slug,
        pinterest_title=title,
        pinterest_caption=caption,
        visual_hook=hook,
    )


def _article_from_library(channel: str, object_key: str, site: str) -> Article | None:
    from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415

    library = cfg.OUTPUTS_DIR / "library"
    if not library.is_dir():
        return None
    name = Path(object_key).name
    for path in sorted(library.glob("post_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        names = {
            Path(str(data.get("b2_key") or "")).name,
            Path(str(data.get("b2_url") or "").split("?")[0]).name,
            Path(str(data.get("local_image_path") or "")).name,
            Path(str(data.get("image_relative") or "")).name,
            str(data.get("b2_key") or ""),
        }
        if object_key not in names and name not in names:
            continue
        article = _article_from_mapping(data, site=site, object_key=object_key)
        if article is not None:
            return article
    return None


def fetch_wordpress_article(site_url: str, slug: str) -> Article | None:
    """Read one post from the site. A miss returns None; the filename is not used."""
    if is_unset(site_url) or not slug:
        return None
    endpoint = site_url.rstrip("/") + "/wp-json/wp/v2/posts"
    try:
        resp = requests.get(
            endpoint,
            params={"slug": slug, "_fields": "link,slug,title,excerpt,content"},
            timeout=20,
        )
    except requests.RequestException as exc:
        log.info("Article lookup failed for %s: %s", slug, type(exc).__name__)
        return None
    if resp.status_code != 200:
        log.info("Article lookup HTTP %s for slug %s", resp.status_code, slug)
        return None
    try:
        posts = resp.json()
    except ValueError:
        return None
    if not isinstance(posts, list) or not posts:
        return None
    post = posts[0]
    if not isinstance(post, dict):
        return None
    title = _plain(str((post.get("title") or {}).get("rendered") or ""))
    excerpt = _plain(str((post.get("excerpt") or {}).get("rendered") or ""))
    content = _plain(str((post.get("content") or {}).get("rendered") or ""))
    body = excerpt or content
    link = str(post.get("link") or "").strip()
    post_slug = str(post.get("slug") or slug).strip()
    if not title and not body:
        return None
    url = link.rstrip("/") if link else _article_url(site_url, post_slug)
    if not url:
        return None
    return Article(url=url, topic=title, body=body or title, slug=post_slug)
