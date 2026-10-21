# -*- coding: utf-8 -*-
"""Free-license providers. No Google image scraping.

Pexels, Unsplash, and Smithsonian need API keys. A missing key skips that
provider. The other four are queried without credentials.
"""

from __future__ import annotations

import html
import logging
import os
import re
from typing import Any

from production.providers.image.free.http import HttpClient
from production.providers.image.free.models import Candidate

logger = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")


def _plain(value: Any) -> str:
    text = html.unescape(_TAG.sub(" ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def _meta_value(block: Any) -> str:
    if isinstance(block, dict):
        return _plain(block.get("value") or "")
    return _plain(block)


def clean_author(author: str) -> str:
    """Collapse a doubled credit such as ``Unknown author Unknown author``."""
    text = re.sub(r"\s+", " ", str(author or "").replace("\xa0", " ")).strip(" ,;")
    if not text:
        return ""
    parts = text.split()
    while len(parts) >= 2 and len(parts) % 2 == 0:
        half = len(parts) // 2
        if [part.casefold() for part in parts[:half]] != [part.casefold() for part in parts[half:]]:
            break
        parts = parts[:half]
    return " ".join(parts)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class WikimediaProvider:
    name = "wikimedia"
    endpoint = "https://commons.wikimedia.org/w/api.php"

    def enabled(self) -> bool:
        return True

    def search(self, query: str, client: HttpClient, *, limit: int = 10) -> list[Candidate]:
        response = client.get(
            self.endpoint,
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": f"filetype:bitmap {query}",
                "gsrnamespace": "6",
                "gsrlimit": str(limit),
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
            },
        )
        if not response.ok:
            return []
        return parse_wikimedia(response.json())


def parse_wikimedia(payload: Any) -> list[Candidate]:
    pages = (((payload or {}).get("query") or {}).get("pages") or {})
    if isinstance(pages, dict):
        rows = list(pages.values())
    elif isinstance(pages, list):
        rows = pages
    else:
        return []
    found: list[Candidate] = []
    for page in rows:
        if not isinstance(page, dict):
            continue
        info_rows = page.get("imageinfo") or []
        info = info_rows[0] if info_rows and isinstance(info_rows[0], dict) else {}
        meta = info.get("extmetadata") or {}
        title = _meta_value(meta.get("ObjectName")) or _plain(page.get("title"))
        title = title.replace("File:", "").strip()
        found.append(
            Candidate(
                provider="wikimedia",
                title=title,
                description=_meta_value(meta.get("ImageDescription")),
                author=clean_author(
                    _meta_value(meta.get("Artist")) or _meta_value(meta.get("Credit"))
                ),
                license_raw=_meta_value(meta.get("LicenseShortName")),
                license_url=_meta_value(meta.get("LicenseUrl")),
                source_url=str(info.get("descriptionurl") or page.get("fullurl") or ""),
                download_url=str(info.get("url") or ""),
                thumbnail_url=str(info.get("thumburl") or ""),
                width=_int(info.get("width")),
                height=_int(info.get("height")),
            )
        )
    return [item for item in found if item.download_url]


class OpenverseProvider:
    name = "openverse"
    endpoint = "https://api.openverse.org/v1/images/"

    def enabled(self) -> bool:
        return True

    def search(self, query: str, client: HttpClient, *, limit: int = 10) -> list[Candidate]:
        response = client.get(
            self.endpoint,
            params={
                "q": query,
                "license": "cc0,pdm,by,by-sa",
                "page_size": str(limit),
            },
        )
        if not response.ok:
            return []
        return parse_openverse(response.json())


def parse_openverse(payload: Any) -> list[Candidate]:
    rows = (payload or {}).get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    found: list[Candidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        license_name = str(row.get("license") or "")
        version = str(row.get("license_version") or "").strip()
        raw = f"cc {license_name} {version}".strip() if license_name else ""
        if license_name.lower() in {"pdm", "cc0"}:
            raw = license_name
        found.append(
            Candidate(
                provider="openverse",
                title=_plain(row.get("title")),
                description="",
                author=_plain(row.get("creator")),
                license_raw=raw,
                license_url=str(row.get("license_url") or ""),
                source_url=str(row.get("foreign_landing_url") or row.get("url") or ""),
                download_url=str(row.get("url") or ""),
                thumbnail_url=str(row.get("thumbnail") or ""),
                width=_int(row.get("width")),
                height=_int(row.get("height")),
            )
        )
    return [item for item in found if item.download_url]


class NasaProvider:
    name = "nasa"
    endpoint = "https://images-api.nasa.gov/search"

    def enabled(self) -> bool:
        return True

    def search(self, query: str, client: HttpClient, *, limit: int = 10) -> list[Candidate]:
        response = client.get(
            self.endpoint,
            params={"q": query, "media_type": "image"},
        )
        if not response.ok:
            return []
        return parse_nasa(response.json())[:limit]

    def resolve_download(self, candidate: Candidate, client: HttpClient) -> Candidate:
        nasa_id = str(candidate.extra.get("nasa_id") or "").strip()
        if candidate.download_url or not nasa_id:
            return candidate
        response = client.get(f"https://images-api.nasa.gov/asset/{nasa_id}")
        if not response.ok:
            return candidate
        hrefs = []
        payload = response.json()
        for item in (payload or {}).get("collection", {}).get("items", []):
            if isinstance(item, dict) and item.get("href"):
                hrefs.append(str(item["href"]))
        chosen = _largest_nasa_file(hrefs)
        if not chosen:
            return candidate
        candidate.download_url = chosen
        return candidate


def parse_nasa(payload: Any) -> list[Candidate]:
    items = (
        ((payload or {}).get("collection") or {}).get("items")
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(items, list):
        return []
    found: list[Candidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        data_rows = item.get("data") or []
        data = data_rows[0] if data_rows and isinstance(data_rows[0], dict) else {}
        nasa_id = str(data.get("nasa_id") or "").strip()
        links = item.get("links") or []
        thumb = ""
        if links and isinstance(links[0], dict):
            thumb = str(links[0].get("href") or "")
        author = _plain(data.get("photographer") or data.get("secondary_creator") or "NASA")
        found.append(
            Candidate(
                provider="nasa",
                title=_plain(data.get("title")),
                description=_plain(data.get("description")),
                author=author or "NASA",
                license_raw="Public Domain",
                license_url="https://www.nasa.gov/multimedia/guidelines/index.html",
                source_url=f"https://images.nasa.gov/details/{nasa_id}" if nasa_id else "",
                download_url="",
                thumbnail_url=thumb,
                extra={"nasa_id": nasa_id, "asset_url": str(item.get("href") or "")},
            )
        )
    return found


def _largest_nasa_file(hrefs: list[str]) -> str:
    images = [
        href
        for href in hrefs
        if href.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff"))
    ]
    if not images:
        return ""
    originals = [href for href in images if "~orig" in href.lower()]
    large = [href for href in images if "~large" in href.lower()]
    pool = originals or large or images
    return pool[-1]


class LibraryOfCongressProvider:
    name = "loc"
    endpoint = "https://www.loc.gov/pictures/search/"

    def enabled(self) -> bool:
        return True

    def search(self, query: str, client: HttpClient, *, limit: int = 10) -> list[Candidate]:
        response = client.get(
            self.endpoint,
            params={"q": query, "fo": "json", "c": str(limit)},
        )
        if not response.ok:
            return []
        return parse_loc(response.json())


def parse_loc(payload: Any) -> list[Candidate]:
    rows = (payload or {}).get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    found: list[Candidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        image = row.get("image") or {}
        links = row.get("links") or {}
        download = ""
        if isinstance(image, dict):
            download = str(image.get("full") or image.get("thumb") or "")
        rights = _plain(
            row.get("rights_advisory")
            or row.get("rights")
            or row.get("rights_information")
            or ""
        )
        author = _plain(
            row.get("contributor") or row.get("creator") or "Library of Congress"
        )
        item_url = ""
        if isinstance(links, dict):
            item_url = str(links.get("item") or row.get("id") or "")
        thumb = ""
        if isinstance(image, dict):
            thumb = str(image.get("thumb") or "")
        found.append(
            Candidate(
                provider="loc",
                title=_plain(row.get("title")),
                description=_plain(row.get("description") or row.get("summary") or ""),
                author=author,
                license_raw=rights,
                source_url=item_url,
                download_url=download,
                thumbnail_url=thumb,
            )
        )
    return [item for item in found if item.download_url]


class SmithsonianProvider:
    name = "smithsonian"
    endpoint = "https://api.si.edu/openaccess/api/v1.0/search"

    def enabled(self) -> bool:
        return bool(_env("SMITHSONIAN_API_KEY"))

    def search(self, query: str, client: HttpClient, *, limit: int = 10) -> list[Candidate]:
        key = _env("SMITHSONIAN_API_KEY")
        if not key:
            return []
        response = client.get(
            self.endpoint,
            params={"q": query, "api_key": key, "rows": str(limit)},
        )
        if not response.ok:
            return []
        return parse_smithsonian(response.json())


def parse_smithsonian(payload: Any) -> list[Candidate]:
    rows = None
    if isinstance(payload, dict):
        rows = ((payload.get("response") or {}).get("rows"))
    if not isinstance(rows, list):
        return []
    found: list[Candidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        content = row.get("content") or {}
        descriptive = content.get("descriptiveNonRepeating") or {}
        title_block = descriptive.get("title") or {}
        if isinstance(title_block, dict):
            title = _plain(title_block.get("content"))
        else:
            title = _plain(row.get("title"))
        media_rows = ((descriptive.get("online_media") or {}).get("media") or [])
        download = ""
        for media in media_rows:
            if not isinstance(media, dict):
                continue
            kind = str(media.get("type") or "").lower()
            href = str(media.get("content") or "")
            is_image = "image" in kind or href.lower().split("?")[0].endswith(
                (".jpg", ".jpeg", ".png")
            )
            if href and is_image:
                download = href
                break
        names = ((content.get("freetext") or {}).get("name") or [])
        author = ""
        if names and isinstance(names[0], dict):
            author = _plain(names[0].get("content"))
        notes = ((content.get("freetext") or {}).get("notes") or [])
        description = ""
        if notes and isinstance(notes[0], dict):
            description = _plain(notes[0].get("content"))
        usage = _plain(
            ((descriptive.get("metadata") or {}).get("usage") or "")
            if isinstance(descriptive.get("metadata"), dict)
            else ""
        )
        found.append(
            Candidate(
                provider="smithsonian",
                title=title,
                description=description,
                author=author or "Smithsonian Institution",
                license_raw=usage or "CC0",
                license_url="https://www.si.edu/openaccess",
                source_url=str(descriptive.get("record_link") or row.get("url") or ""),
                download_url=download,
            )
        )
    return [item for item in found if item.download_url]


class PexelsProvider:
    name = "pexels"
    endpoint = "https://api.pexels.com/v1/search"

    def enabled(self) -> bool:
        return bool(_env("PEXELS_API_KEY"))

    def search(self, query: str, client: HttpClient, *, limit: int = 10) -> list[Candidate]:
        key = _env("PEXELS_API_KEY")
        if not key:
            return []
        response = client.get(
            self.endpoint,
            params={"query": query, "per_page": str(limit), "orientation": "portrait"},
            headers={"Authorization": key},
        )
        if not response.ok:
            return []
        return parse_pexels(response.json())


def parse_pexels(payload: Any) -> list[Candidate]:
    rows = (payload or {}).get("photos") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    found: list[Candidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        src = row.get("src") or {}
        download = str(src.get("original") or src.get("large2x") or src.get("large") or "")
        found.append(
            Candidate(
                provider="pexels",
                title=_plain(row.get("alt")),
                description=_plain(row.get("alt")),
                author=_plain(row.get("photographer")),
                license_raw="Pexels License",
                license_url="https://www.pexels.com/license/",
                source_url=str(row.get("url") or ""),
                download_url=download,
                thumbnail_url=str(src.get("medium") or src.get("small") or ""),
                width=_int(row.get("width")),
                height=_int(row.get("height")),
            )
        )
    return [item for item in found if item.download_url]


class UnsplashProvider:
    name = "unsplash"
    endpoint = "https://api.unsplash.com/search/photos"

    def enabled(self) -> bool:
        return bool(_env("UNSPLASH_ACCESS_KEY"))

    def search(self, query: str, client: HttpClient, *, limit: int = 10) -> list[Candidate]:
        key = _env("UNSPLASH_ACCESS_KEY")
        if not key:
            return []
        response = client.get(
            self.endpoint,
            params={"query": query, "per_page": str(limit), "orientation": "portrait"},
            headers={"Authorization": f"Client-ID {key}"},
        )
        if not response.ok:
            return []
        return parse_unsplash(response.json())


def parse_unsplash(payload: Any) -> list[Candidate]:
    rows = (payload or {}).get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    found: list[Candidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        urls = row.get("urls") or {}
        user = row.get("user") or {}
        links = row.get("links") or {}
        download = ""
        thumb = ""
        if isinstance(urls, dict):
            download = str(urls.get("raw") or urls.get("full") or "")
            thumb = str(urls.get("small") or "")
        page = ""
        if isinstance(links, dict):
            page = str(links.get("html") or "")
        found.append(
            Candidate(
                provider="unsplash",
                title=_plain(row.get("description") or row.get("alt_description")),
                description=_plain(row.get("alt_description")),
                author=_plain(user.get("name") if isinstance(user, dict) else ""),
                license_raw="Unsplash License",
                license_url="https://unsplash.com/license",
                source_url=page,
                download_url=download,
                thumbnail_url=thumb,
                width=_int(row.get("width")),
                height=_int(row.get("height")),
            )
        )
    return [item for item in found if item.download_url]


def default_providers() -> list:
    providers = [
        WikimediaProvider(),
        OpenverseProvider(),
        NasaProvider(),
        LibraryOfCongressProvider(),
        SmithsonianProvider(),
        PexelsProvider(),
        UnsplashProvider(),
    ]
    enabled = []
    for provider in providers:
        if provider.enabled():
            enabled.append(provider)
        else:
            logger.info("Skipping image provider %s — API key is not set.", provider.name)
    return enabled


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()
