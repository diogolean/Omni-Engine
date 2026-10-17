# -*- coding: utf-8 -*-
"""Match a library record to a site article and set the pin link.

The same slug rules run on the PC and in Docker. WordPress is used only
for the URL. Featured images and title graphics are never downloaded.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit

import requests
import yaml

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_DATE_TOKEN = re.compile(r"^\d{1,4}$")
_NOISE_TOKEN = re.compile(
    r"^(ancient|ancientknowl|ancientknowledge|reel)$",
    re.IGNORECASE,
)
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
_SLUG_MATCH_FLOOR = 0.84
_COMPACT_LENGTH_FLOOR = 0.72
_tls_off_logged: set[str] = set()


@dataclass
class Article:
    url: str
    topic: str
    body: str
    slug: str


def _plain(raw_html: str) -> str:
    from agents.posting.pinterest_engine.inventory import normalize_pin_text  # noqa: PLC0415

    return normalize_pin_text(_TAG_RE.sub(" ", raw_html or ""))


def _article_url(site: str, slug: str) -> str:
    if not site or not slug or site.strip().upper() == "TODO":
        return ""
    return f"{site.rstrip('/')}/{slug.strip('/')}"


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _verify_for_url(url: str, site_url: str, verify: bool, account_id: str) -> bool:
    if verify:
        return True
    site_host = _host(site_url)
    if site_host and _host(url) == site_host:
        if account_id not in _tls_off_logged:
            _tls_off_logged.add(account_id)
            log.warning(
                "Article lookup TLS verification is OFF for %s "
                "(the site certificate does not match the host). "
                "Pinterest publishing still verifies TLS.",
                account_id,
            )
        return False
    return True


def _wp_get(url: str, *, params: dict | None, verify: bool, site_url: str, account_id: str):
    use_verify = _verify_for_url(url, site_url, verify, account_id)
    try:
        return requests.get(url, params=params, timeout=20, verify=use_verify)
    except requests.exceptions.SSLError:
        log.error(
            "Article lookup TLS failed for %s. The certificate does not match the site "
            "(often a shared *.web-hosting.com certificate). Set article_tls_verify: false "
            "for article lookups only. Pinterest publishing stays verified.",
            url,
        )
        return None
    except requests.RequestException as exc:
        log.info("Article lookup failed: %s", type(exc).__name__)
        return None


def fetch_wordpress_catalog(
    site_url: str,
    *,
    verify: bool = True,
    account_id: str = "",
) -> list[Article]:
    """List the site's posts once. A TLS or HTTP failure returns an empty list."""
    if not site_url or site_url.strip().upper() == "TODO":
        return []
    endpoint = site_url.rstrip("/") + "/wp-json/wp/v2/posts"
    articles: list[Article] = []
    for page in range(1, 6):
        resp = _wp_get(
            endpoint,
            params={"per_page": 100, "page": page, "_fields": "slug,link,title,excerpt,content"},
            verify=verify,
            site_url=site_url,
            account_id=account_id,
        )
        if resp is None or resp.status_code != 200:
            break
        try:
            posts = resp.json()
        except ValueError:
            break
        if not isinstance(posts, list) or not posts:
            break
        for post in posts:
            if not isinstance(post, dict):
                continue
            slug = str(post.get("slug") or "").strip()
            title = _plain(str((post.get("title") or {}).get("rendered") or ""))
            excerpt = _plain(str((post.get("excerpt") or {}).get("rendered") or ""))
            content = _plain(str((post.get("content") or {}).get("rendered") or ""))
            link = str(post.get("link") or "").strip()
            url = link.rstrip("/") if link else _article_url(site_url, slug)
            if slug and url and (title or excerpt or content):
                articles.append(
                    Article(url=url, topic=title or slug, body=excerpt or content or title, slug=slug)
                )
        try:
            total_pages = int((getattr(resp, "headers", None) or {}).get("X-WP-TotalPages") or 1)
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


def _raw_slug_tokens(slug: str) -> list[str]:
    return [part for part in re.split(r"[-_]+", slug.strip().lower()) if part]


def _slug_prefixes_topic(slug: str, topic_tokens: list[str]) -> bool:
    slug_tokens = _raw_slug_tokens(slug)
    if not slug_tokens or len(topic_tokens) != len(slug_tokens) + 1:
        return False
    return topic_tokens[: len(slug_tokens)] == slug_tokens


def match_article_slug(filename: str, slugs: list[str]) -> str:
    """Best site slug for a library name or topic. Empty when the overlap is weak."""
    topic_tokens = _filename_tokens(filename)
    distinctive = _distinctive_tokens(topic_tokens)
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
        if _slug_prefixes_topic(slug, topic_tokens):
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


def image_map_path() -> Path:
    override = (os.getenv("PINTEREST_IMAGE_MAP") or "").strip()
    if override:
        return Path(override).expanduser()
    from omni.config import repo_root  # noqa: PLC0415

    return repo_root() / "config" / "pinterest_image_map.yaml"


def load_image_map(path: Path | None = None) -> dict[str, dict[str, str]]:
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


def mapped_slug(account_id: str, name: str) -> str | None:
    """``skip``, a slug, or None when this record has no map entry."""
    rows = load_image_map().get(account_id) or {}
    if not rows:
        return None
    stem = Path(name).stem.lower()
    lowered = name.lower()
    best_key = ""
    best: str | None = None
    for key, slug in rows.items():
        exact = key in {lowered, stem, Path(name).name.lower()}
        suffix = stem.endswith("_" + key) or stem.endswith("-" + key)
        if (exact or suffix) and len(key) > len(best_key):
            best_key = key
            best = slug
    return best


def article_for_slug(slug: str, catalog: list[Article]) -> Article | None:
    wanted = slug.strip().lower()
    for article in catalog:
        if article.slug.lower() == wanted:
            return article
    return None


def match_catalog_article(name: str, catalog: list[Article]) -> Article | None:
    slug = match_article_slug(name, [article.slug for article in catalog])
    if not slug:
        return None
    return article_for_slug(slug, catalog)


def attach_article_url(entry: dict, catalog: list[Article], account_id: str) -> str | None:
    """Set ``target_url`` from the matched article. Return ``skip`` to drop the pin."""
    name = str(
        entry.get("subject_slug")
        or entry.get("post_id")
        or entry.get("topic")
        or ""
    )
    mapped = mapped_slug(account_id, name)
    if mapped == "skip":
        return "skip"
    article = article_for_slug(mapped, catalog) if mapped else None
    if article is None:
        article = match_catalog_article(name, catalog)
    if article is None and entry.get("topic"):
        article = match_catalog_article(str(entry.get("topic")), catalog)
    if article is not None:
        entry["target_url"] = article.url
    return None


def dumps_articles(articles: list[Article]) -> str:
    return json.dumps([article.__dict__ for article in articles])
