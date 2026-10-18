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
import time
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
_FUZZY_FLOOR = 0.72
_DEFAULT_CATALOG_TTL = 6 * 60 * 60
_STOP_TOKENS = {
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
    "at",
    "to",
    "by",
    "that",
    "this",
    "still",
    "has",
    "have",
    "who",
    "whose",
    "what",
    "about",
    "was",
    "were",
    "are",
    "not",
    "but",
    "into",
    "their",
    "they",
    "than",
    "over",
    "after",
    "before",
    "out",
    "when",
    "where",
    "which",
    "while",
    "there",
    "just",
    "more",
    "also",
    "only",
    "does",
    "did",
    "been",
}
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


def ensure_trailing_slash(url: str) -> str:
    """Article permalinks redirect without the trailing slash."""
    from urllib.parse import urlunsplit  # noqa: PLC0415

    text = (url or "").strip()
    if not text or text.upper() == "TODO":
        return ""
    parts = urlsplit(text)
    if not parts.scheme or not parts.netloc:
        return text if text.endswith("/") else text + "/"
    path = parts.path or "/"
    if not path.endswith("/"):
        path += "/"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _article_url(site: str, slug: str) -> str:
    if not site or not slug or site.strip().upper() == "TODO":
        return ""
    return ensure_trailing_slash(f"{site.rstrip('/')}/{slug.strip('/')}")


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


def catalog_ttl_seconds() -> int:
    raw = (os.getenv("ARTICLE_CATALOG_TTL_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_CATALOG_TTL
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_CATALOG_TTL
    return value if value > 0 else _DEFAULT_CATALOG_TTL


def catalog_cache_path(account_id: str, outputs_dir: Path) -> Path:
    """JSON cache on the outputs volume (the Docker mount), not the git tree."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", (account_id or "site").strip()) or "site"
    return outputs_dir / "article_cache" / f"{safe}.json"


def _articles_from_payload(payload: object) -> list[Article]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("articles")
    if not isinstance(rows, list):
        return []
    articles: list[Article] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        slug = str(row.get("slug") or "").strip()
        if not url or not slug:
            continue
        articles.append(
            Article(
                url=url,
                topic=str(row.get("topic") or slug),
                body=str(row.get("body") or ""),
                slug=slug,
            )
        )
    return articles


def _read_catalog_cache(path: Path, site_url: str) -> tuple[list[Article], float] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if str(payload.get("site") or "").rstrip("/") != site_url.rstrip("/"):
        return None
    try:
        fetched_at = float(payload.get("fetched_at") or 0)
    except (TypeError, ValueError):
        return None
    articles = _articles_from_payload(payload)
    if not articles or fetched_at <= 0:
        return None
    return articles, fetched_at


def _write_catalog_cache(path: Path, site_url: str, articles: list[Article]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "site": site_url.rstrip("/"),
        "fetched_at": time.time(),
        "articles": [article.__dict__ for article in articles],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def fetch_wordpress_catalog(
    site_url: str,
    *,
    verify: bool = True,
    account_id: str = "",
    outputs_dir: Path | None = None,
    ttl_seconds: int | None = None,
) -> list[Article]:
    """List the site's posts, using an outputs-volume cache until the TTL expires.

    A TLS or HTTP failure returns the stale cache when one exists, otherwise [].
    """
    if not site_url or site_url.strip().upper() == "TODO":
        return []
    ttl = catalog_ttl_seconds() if ttl_seconds is None else ttl_seconds
    cached: tuple[list[Article], float] | None = None
    cache_path: Path | None = None
    if outputs_dir is not None:
        cache_path = catalog_cache_path(account_id, outputs_dir)
        cached = _read_catalog_cache(cache_path, site_url)
        if cached is not None and (time.time() - cached[1]) < ttl:
            log.info(
                "Article catalog cache hit for %s (%d posts).",
                account_id or "site",
                len(cached[0]),
            )
            return cached[0]
    articles = _fetch_wordpress_pages(site_url, verify=verify, account_id=account_id)
    if articles:
        if cache_path is not None:
            try:
                _write_catalog_cache(cache_path, site_url, articles)
            except OSError as exc:
                log.info("Article catalog cache write failed (%s).", type(exc).__name__)
        return articles
    if cached is not None:
        log.info(
            "Article catalog fetch failed; using stale cache (%d posts).",
            len(cached[0]),
        )
        return cached[0]
    return []


def _fetch_wordpress_pages(
    site_url: str,
    *,
    verify: bool,
    account_id: str,
) -> list[Article]:
    """GET /wp-json/wp/v2/posts?per_page=100 and follow X-WP-TotalPages."""
    endpoint = site_url.rstrip("/") + "/wp-json/wp/v2/posts"
    articles: list[Article] = []
    for page in range(1, 21):
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
            url = ensure_trailing_slash(link) if link else _article_url(site_url, slug)
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
    """Words in a filename or a topic. A slash inside a title is a word break.

    ``Path.stem`` would keep only ``Mudflood`` from ``Tartaria/Mudflood``.
    Real image keys still use the file stem so parent folders do not match.
    """
    text = str(filename or "").replace("\\", "/")
    if "/" in text:
        tail = text.rsplit("/", 1)[-1]
        if re.search(r"\.[A-Za-z0-9]{1,5}$", tail):
            text = tail
    if re.search(r"\.[A-Za-z0-9]{1,5}$", text):
        text = Path(text).stem
    tokens: list[str] = []
    for token in re.split(r"[_\-\s./]+", text.lower()):
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


def _stem_token(token: str) -> str:
    """Fold simple plurals so ``cities`` and ``city`` are the same word."""
    word = token.lower()
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _content_tokens(text: str) -> list[str]:
    """Stemmed content words. Stopwords drop out. Generic words stay, as extras."""
    out: list[str] = []
    for token in _filename_tokens(text):
        if token in _STOP_TOKENS or len(token) <= 2:
            continue
        stemmed = _stem_token(token)
        if len(stemmed) <= 2 or stemmed in _STOP_TOKENS:
            continue
        out.append(stemmed)
    return out


def _is_sentence(text: str) -> bool:
    """True for a headline. Filenames and short compounds are not sentences."""
    raw = _filename_tokens(text)
    if any(token in _STOP_TOKENS for token in raw):
        return True
    return len(raw) >= 8


def _slug_distinctive(slug: str) -> list[str]:
    out: list[str] = []
    for part in _raw_slug_tokens(slug):
        if part in _STOP_TOKENS or len(part) <= 2:
            continue
        stemmed = _stem_token(part)
        if len(stemmed) <= 2:
            continue
        out.append(stemmed)
    return out


def _compact_score(query_tokens: list[str], slug_tokens: list[str]) -> float:
    """Near-spellings such as baalbec/baalbek. Kept below a real token hit."""
    if not query_tokens or not slug_tokens:
        return 0.0
    file_compact = "".join(query_tokens)
    slug_compact = "".join(slug_tokens)
    shorter = min(len(file_compact), len(slug_compact))
    longer = max(len(file_compact), len(slug_compact))
    if shorter < 5 or longer == 0 or shorter / longer < _COMPACT_LENGTH_FLOOR:
        return 0.0
    ratio = SequenceMatcher(None, file_compact, slug_compact).ratio()
    if ratio < _SLUG_MATCH_FLOOR:
        return 0.0
    return min(ratio, 0.89)


def _query_slug_score(query: str, slug: str, title: str = "") -> float:
    """High when every distinctive slug token appears in *query*.

    A sentence may add filler (``Oak Island and the 200-year mystery...``).
    A filename may add one extra word (``baalbek-trilithon``, ``Tartaria/Mudflood``).
    Two extra subject words are a different subject (``giants-tomb-sardinia``),
    unless the article title already contains them.
    Longer slugs score higher so ``easter-island-moai`` beats ``easter-island``.
    """
    wanted = _slug_distinctive(slug)
    query_tokens = _content_tokens(query)
    if not wanted or not query_tokens:
        return 0.0
    wanted_set = set(wanted)
    if wanted_set <= set(query_tokens):
        extras = [token for token in query_tokens if token not in wanted_set]
        title_set = set(_content_tokens(title)) | wanted_set
        uncovered = [token for token in extras if token not in title_set]
        if uncovered and not _is_sentence(query) and len(uncovered) > 1:
            return 0.0
        return min(0.99, 0.90 + 0.01 * len(wanted_set))
    return _compact_score(query_tokens, wanted)


def match_article_slug(filename: str, slugs: list[str]) -> str:
    """Best site slug for a library name or topic. Empty when the overlap is weak."""
    best = ""
    best_score = 0.0
    ordered = sorted(
        {slug.strip() for slug in slugs if slug and slug.strip()},
        key=lambda slug: (len(_slug_distinctive(slug)), len(slug)),
        reverse=True,
    )
    for slug in ordered:
        score = _query_slug_score(filename, slug)
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
    flat_stem = re.sub(r"[^a-z0-9]+", "", stem)
    flat_name = re.sub(r"[^a-z0-9]+", "", lowered)
    best_key = ""
    best: str | None = None
    for key, slug in rows.items():
        flat_key = re.sub(r"[^a-z0-9]+", "", key)
        exact = key in {lowered, stem, Path(name).name.lower()} or (
            bool(flat_key) and flat_key in {flat_stem, flat_name}
        )
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


def article_link_required(account_id: str = "") -> bool:
    """Ancient Knowledge pins must link to an article. Never a sales URL."""
    from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415

    channel = (account_id or cfg.CHANNEL_ID or "").strip()
    return channel == "ancient_knowledge"


def resolve_pin_link(record: dict | None) -> str:
    """Article URL for the pin. Ancient Knowledge does not fall back to a sales URL."""
    row = record or {}
    url = str(row.get("target_url") or "").strip()
    if url:
        return url
    channel = str(row.get("channel_id") or row.get("page_id") or "")
    if article_link_required(channel):
        return ""
    from agents.posting.pinterest_engine import config as cfg  # noqa: PLC0415

    if article_link_required():
        return ""
    return (cfg.TARGET_URL or "").strip()


def _match_tokens(text: str) -> list[str]:
    """Content words. Stopwords drop out. Words like ``tomb`` stay so they count as extras."""
    out: list[str] = []
    for token in _filename_tokens(text):
        if token in _STOP_TOKENS or len(token) <= 2:
            continue
        out.append(token)
    return out


def _keyword_tokens(article: Article) -> list[str]:
    blob = f"{article.slug.replace('-', ' ')} {article.topic}"
    seen: list[str] = []
    for token in _match_tokens(blob):
        if token not in seen:
            seen.append(token)
    return seen


def score_article_match(query: str, article: Article) -> float:
    """0..1. Slug tokens must appear in the query; the article title covers extras."""
    return _query_slug_score(query, article.slug, title=article.topic or "")


def match_catalog_article(name: str, catalog: list[Article]) -> Article | None:
    slug = match_article_slug(name, [article.slug for article in catalog])
    if slug:
        found = article_for_slug(slug, catalog)
        if found is not None:
            return found
    best: Article | None = None
    best_score = 0.0
    for article in catalog:
        score = score_article_match(name, article)
        if score > best_score:
            best_score = score
            best = article
    if best is not None and best_score >= _FUZZY_FLOOR:
        return best
    return None


def _entry_names(entry: dict) -> list[str]:
    names: list[str] = []
    for key in ("subject_slug", "topic", "pinterest_title", "post_id"):
        value = str(entry.get(key) or "").strip()
        if value and value not in names:
            names.append(value)
    for key in ("local_image_path", "image_relative", "image_path"):
        raw = str(entry.get(key) or "").strip()
        if not raw:
            continue
        for value in (Path(raw).name, Path(raw).stem):
            if value and value not in names:
                names.append(value)
    return names


def best_catalog_match(names: list[str], catalog: list[Article]) -> Article | None:
    """Best article across filename, slug, title, and topic. Slug hits win immediately."""
    best: Article | None = None
    best_score = 0.0
    for name in names:
        slug = match_article_slug(name, [article.slug for article in catalog])
        if slug:
            found = article_for_slug(slug, catalog)
            if found is not None:
                return found
        for article in catalog:
            score = score_article_match(name, article)
            if score > best_score:
                best_score = score
                best = article
    if best is not None and best_score >= _FUZZY_FLOOR:
        return best
    return None


def attach_article_url(
    entry: dict,
    catalog: list[Article],
    account_id: str,
    site_url: str = "",
) -> str | None:
    """Set ``target_url`` from the catalog, then the image map.

    Returns ``skip`` when the map says skip, ``no-link`` when an article is
    required and none matched, otherwise None.
    """
    names = _entry_names(entry)
    for name in names:
        if mapped_slug(account_id, name) == "skip":
            entry["target_url"] = ""
            return "skip"
    article = best_catalog_match(names, catalog)
    if article is None:
        mapped = ""
        for name in names:
            slug = mapped_slug(account_id, name)
            if slug and slug != "skip":
                mapped = slug
                break
        if mapped:
            article = article_for_slug(mapped, catalog)
            if article is None and site_url:
                url = _article_url(site_url, mapped)
                if url:
                    entry["target_url"] = url
                    return None
    if article is not None:
        entry["target_url"] = ensure_trailing_slash(article.url)
        return None
    if article_link_required(account_id):
        entry["target_url"] = ""
        return "no-link"
    return None


def dumps_articles(articles: list[Article]) -> str:
    return json.dumps([article.__dict__ for article in articles])
