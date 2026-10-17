"""One Pinterest wake: image cards from B2, titled from the article.

The VM scheduler used to publish video clips and name them from the
filename. This drip lists image objects, downloads only the file it is
about to publish, builds the same 2:3 card as the PC engine, and links
the pin to the related article. The temp file is deleted afterwards.
Nothing in B2 is deleted.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
import yaml

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
    featured_image_url: str = ""
    featured_media_id: str = ""
    content_image_urls: tuple[str, ...] = ()


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
    pattern = account.extra.get("b2_filename_pattern", "")
    images = [
        key
        for key in keys
        if _is_image_key(key) and _filename_allowed(key, pattern)
    ]
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
    transformer = PinTransformer(outputs_dir=cfg.OUTPUTS_DIR, method="cover")
    site = account.extra.get("site_url", "")
    verify_tls = _article_tls_enabled(account)
    catalog_rows: list[Article] | None = None

    def _catalog() -> list[Article]:
        nonlocal catalog_rows
        if catalog_rows is None:
            if is_unset(site):
                catalog_rows = []
            else:
                catalog_rows = fetch_wordpress_catalog(
                    site,
                    verify=verify_tls,
                    account_id=account.account_id,
                )
        return catalog_rows

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
            mapped = _mapped_slug(account.account_id, key)
            if mapped == "skip":
                log.info(
                    "Skipping %s — pinterest_image_map.yaml says skip",
                    key,
                )
                stats["skipped"] += 1
                continue
            if mapped:
                article = _article_for_slug(mapped, _catalog())
                if article is None:
                    log.info(
                        "Skipping %s — mapped slug %s is not on the site",
                        key,
                        mapped,
                    )
                    stats["skipped"] += 1
                    continue
            else:
                article = _match_catalog_article(key, _catalog())
        if article is None:
            log.info(
                "Skipping %s — %s",
                key,
                _no_match_reason(key),
            )
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
            if not _ensure_source_image(
                key,
                dest,
                article,
                client=client,
                bucket=bucket_name,
                download_object=download_object,
                site=site,
                verify=verify_tls,
                account_id=account.account_id,
            ):
                log.info(
                    "Skipping %s — no real photo (title graphics are not pinned)",
                    key,
                )
                stats["skipped"] += 1
                continue
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
                card_path = _write_dry_run_card(account.account_id, key, pin_bytes)
                log.info(
                    "[DRY RUN] account=%s b2=%s article=%s title=%s bytes=%d card=%s",
                    account.account_id,
                    key,
                    article.url,
                    fields["pinterest_title"],
                    len(pin_bytes),
                    card_path,
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


def _filename_allowed(key: str, pattern: str) -> bool:
    """True when the object basename matches the account filename pattern."""
    if not (pattern or "").strip():
        return True
    try:
        return re.search(pattern, Path(key).name) is not None
    except re.error:
        log.warning("Invalid b2_filename_pattern %r; accepting image names", pattern)
        return True


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


def _plain(raw_html: str) -> str:
    from agents.posting.pinterest_engine.inventory import normalize_pin_text  # noqa: PLC0415

    return normalize_pin_text(_TAG_RE.sub(" ", raw_html or ""))


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
        normalize_pin_text,
        polish_pin_text,
    )

    title = polish_pin_text(article.pinterest_title)
    caption = polish_pin_text(article.pinterest_caption)
    hook = normalize_pin_text(article.visual_hook)
    if _title_is_filename(title, object_key):
        title = ""
    topic = polish_pin_text(article.topic)
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
    return _article_from_library(account.branding, object_key, site)


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


_NOISE_TOKEN = re.compile(r"^(ancient|ancientknowl|ancientknowledge|reel)$", re.IGNORECASE)
_DATE_TOKEN = re.compile(r"^\d{1,4}$")
_SLUG_MATCH_FLOOR = 0.84
_COMPACT_LENGTH_FLOOR = 0.72
_GENERIC_TOKENS = {
    "tomb",
    "tombs",
    "line",
    "lines",
    "mystery",
    "mysteries",
    "lost",
    "secret",
    "secrets",
    "hidden",
    "old",
    "the",
    "and",
    "of",
    "a",
    "an",
    "in",
    "on",
    "for",
    "with",
    "from",
    "its",
    "temple",
    "ruins",
    "ruin",
    "stone",
    "stones",
    "site",
    "sites",
    "hoax",
    "truth",
    "real",
    "history",
    "historical",
    "legend",
    "legends",
    "myth",
    "myths",
    "fact",
    "facts",
}
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_tls_off_logged: set[str] = set()


def _article_tls_enabled(account: Account) -> bool:
    """True unless this account explicitly turns article-lookup TLS off."""
    raw = (account.extra.get("article_tls_verify") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _verify_for_url(url: str, site_url: str, verify: bool, account_id: str) -> bool:
    """Keep TLS on except for this account's own site when the flag is off.

    Pinterest and any other host stay verified. The flag exists because the
    article host currently presents a shared ``*.web-hosting.com`` certificate.
    """
    if verify:
        return True
    site_host = _host(site_url)
    if site_host and _host(url) == site_host:
        if account_id not in _tls_off_logged:
            _tls_off_logged.add(account_id)
            log.warning(
                "Article lookup TLS verification is OFF for %s "
                "(the site certificate does not match the host; often a shared "
                "*.web-hosting.com certificate). Pinterest publishing still verifies TLS.",
                account_id,
            )
        return False
    return True


def _log_ssl_failure(url: str) -> None:
    log.error(
        "Article lookup TLS failed for %s. The certificate does not match the site "
        "(often a shared *.web-hosting.com certificate). Set article_tls_verify: false "
        "on this account to look up articles only. Pinterest publishing stays verified.",
        url,
    )


def _wp_get(
    url: str,
    *,
    params: dict | None,
    verify: bool,
    site_url: str,
    account_id: str,
):
    use_verify = _verify_for_url(url, site_url, verify, account_id)
    try:
        return requests.get(url, params=params, timeout=20, verify=use_verify)
    except requests.exceptions.SSLError:
        _log_ssl_failure(url)
        return None
    except requests.RequestException as exc:
        log.info("Article lookup failed: %s", type(exc).__name__)
        return None


def _featured_from_post(post: dict) -> tuple[str, str]:
    media_id = str(post.get("featured_media") or "").strip()
    if media_id in {"", "0"}:
        media_id = ""
    embedded = post.get("_embedded") or {}
    media = embedded.get("wp:featuredmedia") if isinstance(embedded, dict) else None
    if isinstance(media, list):
        for item in media:
            if isinstance(item, dict):
                source = str(item.get("source_url") or "").strip()
                if source:
                    return source, media_id
    return "", media_id


def _article_from_wp_post(post: dict, site_url: str) -> Article | None:
    if not isinstance(post, dict):
        return None
    title = _plain(str((post.get("title") or {}).get("rendered") or ""))
    excerpt = _plain(str((post.get("excerpt") or {}).get("rendered") or ""))
    raw_content = str((post.get("content") or {}).get("rendered") or "")
    content = _plain(raw_content)
    body = excerpt or content
    post_slug = str(post.get("slug") or "").strip()
    if not post_slug or (not title and not body):
        return None
    link = str(post.get("link") or "").strip()
    url = link.rstrip("/") if link else _article_url(site_url, post_slug)
    if not url:
        return None
    featured_url, media_id = _featured_from_post(post)
    return Article(
        url=url,
        topic=title,
        body=body or title,
        slug=post_slug,
        featured_image_url=featured_url,
        featured_media_id=media_id,
        content_image_urls=tuple(_content_image_urls(raw_content, site_url)),
    )


def fetch_wordpress_catalog(
    site_url: str,
    *,
    verify: bool = True,
    account_id: str = "",
) -> list[Article]:
    """List the site's posts once. A TLS or HTTP failure returns an empty list."""
    if is_unset(site_url):
        return []
    endpoint = site_url.rstrip("/") + "/wp-json/wp/v2/posts"
    articles: list[Article] = []
    for page in range(1, 6):
        resp = _wp_get(
            endpoint,
            params={"per_page": 100, "page": page, "_embed": "wp:featuredmedia"},
            verify=verify,
            site_url=site_url,
            account_id=account_id,
        )
        if resp is None:
            break
        if resp.status_code != 200:
            if page == 1:
                log.info("Article catalog HTTP %s for %s", resp.status_code, site_url)
            break
        try:
            posts = resp.json()
        except ValueError:
            break
        if not isinstance(posts, list) or not posts:
            break
        for post in posts:
            article = _article_from_wp_post(post, site_url)
            if article is not None:
                articles.append(article)
        total_pages = 1
        headers = getattr(resp, "headers", None) or {}
        try:
            total_pages = int(headers.get("X-WP-TotalPages") or 1)
        except (TypeError, ValueError):
            total_pages = 1
        if page >= total_pages:
            break
    return articles


def _filename_tokens(filename: str) -> list[str]:
    stem = Path(filename).stem.lower()
    tokens: list[str] = []
    for token in re.split(r"[_\-\s.]+", stem):
        token = token.strip()
        if not token or _DATE_TOKEN.match(token) or _NOISE_TOKEN.match(token):
            continue
        tokens.append(token)
    return tokens


def _distinctive_tokens(tokens: list[str]) -> list[str]:
    """Drop dates and generic words so one shared word cannot pick an article."""
    out: list[str] = []
    for token in tokens:
        if not token or token in _GENERIC_TOKENS or _DATE_TOKEN.match(token):
            continue
        if len(token) <= 2:
            continue
        out.append(token)
    return out


def _slug_tokens(slug: str) -> list[str]:
    return _distinctive_tokens(
        [part for part in re.split(r"[-_]+", slug.strip().lower()) if part]
    )


def match_article_slug(filename: str, slugs: list[str]) -> str:
    """Best site slug for a card filename. Empty when the overlap is weak.

    A match needs the same distinctive words as the slug. Generic words
    (``tomb``, ``ancient``, ``lines``) do not count, so ``giants-tomb-sardinia``
    does not attach to the ``giants`` article. A one-token typo such as
    ``baalbec`` can still reach ``baalbek``.
    """
    distinctive = _distinctive_tokens(_filename_tokens(filename))
    if not distinctive:
        return ""
    file_set = set(distinctive)
    file_compact = "".join(distinctive)
    best = ""
    best_score = 0.0
    ordered = sorted(
        {slug.strip() for slug in slugs if slug and slug.strip()},
        key=lambda slug: (len(_slug_tokens(slug)), len(slug)),
        reverse=True,
    )
    for slug in ordered:
        slug_tokens = _slug_tokens(slug)
        if not slug_tokens:
            continue
        if set(slug_tokens) == file_set:
            return slug
        slug_compact = "".join(slug_tokens)
        shorter = min(len(file_compact), len(slug_compact))
        longer = max(len(file_compact), len(slug_compact))
        if shorter < 5 or longer == 0 or shorter / longer < _COMPACT_LENGTH_FLOOR:
            continue
        score = SequenceMatcher(None, file_compact, slug_compact).ratio()
        if score > best_score:
            best_score = score
            best = slug
    if best_score >= _SLUG_MATCH_FLOOR:
        return best
    return ""


def _no_match_reason(object_key: str) -> str:
    tokens = _distinctive_tokens(_filename_tokens(object_key))
    shown = ", ".join(tokens) if tokens else "(none)"
    return f"no strong article match (distinctive tokens: {shown})"


def image_map_path() -> Path:
    override = (os.getenv("PINTEREST_IMAGE_MAP") or "").strip()
    if override:
        return Path(override).expanduser()
    from omni.config import repo_root  # noqa: PLC0415

    return repo_root() / "config" / "pinterest_image_map.yaml"


def load_image_map(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Account id -> {filename stem or key -> slug or ``skip``}."""
    source = path or image_map_path()
    if not source.is_file():
        return {}
    try:
        loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("Ignoring image map %s: %s", source, type(exc).__name__)
        return {}
    if not isinstance(loaded, dict):
        return {}
    parsed: dict[str, dict[str, str]] = {}
    for account_id, rows in loaded.items():
        if not isinstance(rows, dict):
            continue
        account_rows: dict[str, str] = {}
        for raw_key, raw_slug in rows.items():
            key = str(raw_key or "").strip().lower()
            if not key:
                continue
            slug = str(raw_slug or "").strip().lower()
            account_rows[key] = "skip" if slug in {"", "skip", "-", "none"} else slug
        if account_rows:
            parsed[str(account_id).strip()] = account_rows
    return parsed


def _mapped_slug(account_id: str, object_key: str) -> str | None:
    """``skip``, a slug, or None when this image has no map entry."""
    rows = load_image_map().get(account_id) or {}
    if not rows:
        return None
    stem = Path(object_key).stem.lower()
    name = Path(object_key).name.lower()
    for candidate in (object_key.lower(), name, stem):
        if candidate in rows:
            return rows[candidate]
    return None


def _article_for_slug(slug: str, catalog: list[Article]) -> Article | None:
    wanted = slug.strip().lower()
    for article in catalog:
        if article.slug.lower() == wanted:
            return article
    return None


def _match_catalog_article(object_key: str, catalog: list[Article]) -> Article | None:
    slug = match_article_slug(object_key, [article.slug for article in catalog])
    if not slug:
        return None
    for article in catalog:
        if article.slug.lower() == slug.lower():
            return article
    return None


def _img_source(tag: str) -> str:
    attrs: dict[str, str] = {}
    for key, value in re.findall(r"""([A-Za-z0-9:-]+)\s*=\s*["']([^"']+)["']""", tag):
        attrs[key.lower()] = html.unescape(value)
    for name in ("data-orig-file", "data-large-file", "data-src", "data-lazy-src", "src"):
        if attrs.get(name):
            return attrs[name]
    return ""


def _content_image_urls(raw_html: str, site_url: str) -> list[str]:
    """Image URLs in post HTML, in order, skipping data URIs and svg icons."""
    found: list[str] = []
    seen: set[str] = set()
    for tag in _IMG_TAG_RE.findall(raw_html or ""):
        url = _absolute_url(_img_source(tag), site_url)
        if not url or url in seen:
            continue
        path = urlsplit(url).path.lower()
        if path.endswith(".svg") or "spacer" in path or "pixel.gif" in path:
            continue
        seen.add(url)
        found.append(url)
    return found


def _absolute_url(url: str, site_url: str) -> str:
    text = (url or "").strip()
    if not text or text.startswith("data:"):
        return ""
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("/"):
        parts = urlsplit(site_url)
        if not parts.scheme or not parts.netloc:
            return ""
        return f"{parts.scheme}://{parts.netloc}{text}"
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return ""


def _same_image(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return urlsplit(left).path.rstrip("/").lower() == urlsplit(right).path.rstrip("/").lower()


def _image_is_text_graphic(img) -> bool:
    """True for flat title cards (dark field, few colors), false for photographs."""
    from PIL import Image  # noqa: PLC0415

    small = img.convert("RGB").resize((48, 72), Image.Resampling.BOX)
    pixels = list(small.getdata())
    count = len(pixels)
    if count == 0:
        return True
    mean = [sum(pixel[channel] for pixel in pixels) / count for channel in range(3)]
    variance = sum(
        (pixel[0] - mean[0]) ** 2
        + (pixel[1] - mean[1]) ** 2
        + (pixel[2] - mean[2]) ** 2
        for pixel in pixels
    ) / count
    spread = variance ** 0.5
    buckets: dict[tuple[int, int, int], int] = {}
    for pixel in pixels:
        key = (pixel[0] // 16, pixel[1] // 16, pixel[2] // 16)
        buckets[key] = buckets.get(key, 0) + 1
    dominant = max(buckets.values()) / count
    if spread < 26 and dominant >= 0.34:
        return True
    if dominant >= 0.48:
        return True
    # This site's title template: a dark teal field with a few gold marks.
    teal_field = (
        mean[0] < 55
        and 35 <= mean[1] <= 125
        and 45 <= mean[2] <= 145
        and dominant >= 0.36
    )
    return teal_field


def is_text_graphic(path: Path) -> bool:
    """True when the file is a title graphic rather than a photograph."""
    from PIL import Image  # noqa: PLC0415

    try:
        with Image.open(path) as img:
            return _image_is_text_graphic(img)
    except Exception:  # noqa: BLE001
        return True


def _image_readable(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as img:
            img.convert("RGB").load()
        return True
    except Exception:  # noqa: BLE001
        path.unlink(missing_ok=True)
        return False


def _download_featured(
    url: str,
    dest: Path,
    *,
    site: str,
    verify: bool,
    account_id: str,
) -> bool:
    resp = _wp_get(
        url,
        params=None,
        verify=verify,
        site_url=site,
        account_id=account_id,
    )
    if resp is None or resp.status_code != 200 or not getattr(resp, "content", b""):
        log.info("Featured image HTTP %s", getattr(resp, "status_code", "none"))
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return _image_readable(dest)


def _lookup_featured_url(
    article: Article,
    *,
    site: str,
    verify: bool,
    account_id: str,
) -> str:
    if article.featured_image_url:
        return article.featured_image_url
    media_id = (article.featured_media_id or "").strip()
    if not media_id or is_unset(site):
        return ""
    endpoint = site.rstrip("/") + f"/wp-json/wp/v2/media/{media_id}"
    resp = _wp_get(
        endpoint,
        params={"_fields": "source_url"},
        verify=verify,
        site_url=site,
        account_id=account_id,
    )
    if resp is None or resp.status_code != 200:
        return ""
    try:
        payload = resp.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("source_url") or "").strip()


def _write_dry_run_card(account_id: str, object_key: str, pin_bytes: bytes) -> Path:
    """Save a dry-run card under outputs/pinterest/dryrun/ for inspection."""
    from utils.pipeline_paths import outputs_root  # noqa: PLC0415

    name = Path(object_key).name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{account_id}_{name}")
    if not safe.lower().endswith((".jpg", ".jpeg")):
        safe += ".jpg"
    dest = outputs_root() / "pinterest" / "dryrun" / safe
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pin_bytes)
    return dest


def _accept_photo(path: Path, *, label: str) -> bool:
    if not _image_readable(path):
        log.info("%s is not a readable image", label)
        return False
    if is_text_graphic(path):
        log.info("%s looks like a title graphic, not a photo", label)
        path.unlink(missing_ok=True)
        return False
    return True


def _ensure_source_image(
    object_key: str,
    dest: Path,
    article: Article,
    *,
    client: Any | None,
    bucket: str,
    download_object,
    site: str,
    verify: bool,
    account_id: str,
) -> bool:
    """Prefer the B2 photo, then a body photo, then a real featured photo.

    Title-card graphics are skipped. If nothing left is a photograph, the
    caller does not publish.
    """
    try:
        download_object(object_key, dest, client=client, bucket=bucket)
    except Exception as exc:  # noqa: BLE001
        log.info("B2 download failed for %s: %s", object_key, type(exc).__name__)
        dest.unlink(missing_ok=True)
    else:
        if _accept_photo(dest, label=f"B2 object {object_key}"):
            return True
        dest.unlink(missing_ok=True)

    featured = _lookup_featured_url(
        article,
        site=site,
        verify=verify,
        account_id=account_id,
    )
    for url in article.content_image_urls:
        if featured and _same_image(url, featured):
            continue
        log.info("Trying article photo %s for %s", url, object_key)
        if _download_featured(
            url,
            dest,
            site=site,
            verify=verify,
            account_id=account_id,
        ) and _accept_photo(dest, label=f"article photo {url}"):
            return True
        dest.unlink(missing_ok=True)
    if not featured:
        return False
    log.info("Trying featured image for %s", object_key)
    if not _download_featured(
        featured,
        dest,
        site=site,
        verify=verify,
        account_id=account_id,
    ):
        return False
    if _accept_photo(dest, label=f"featured image {featured}"):
        return True
    dest.unlink(missing_ok=True)
    return False
