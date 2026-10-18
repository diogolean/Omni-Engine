# -*- coding: utf-8 -*-
"""Download one real photograph for a knowledge-base entity.

Wikimedia Commons is first. The Metropolitan Museum open-access API is
next, and Smithsonian only when SMITHSONIAN_API_KEY is set. Museo del Oro
has no public image API: high-resolution files are requested by email, so
this module does not call it.

Only Public Domain, CC0, CC BY, and CC BY-SA are kept. BY and BY-SA store
a caption credit. A file below the still minimum is kept as a reference
for Nano Banana and is not the published frame.
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote

from PIL import Image

from agents.media.free_images.attribution import credit_line, write_sidecar
from agents.media.free_images.http import USER_AGENT, HttpClient, RequestsClient
from agents.media.free_images.licenses import license_allowed
from agents.media.free_images.models import Attribution, Candidate
from agents.media.free_images.providers import parse_wikimedia
from modules.reel_artefact.crop import (
    STILL_MIN_SHORT,
    prepare_reel_frame,
    usable_as_reference,
    usable_as_still,
)
from modules.reel_artefact.motion import write_motion_sidecar
from modules.reel_artefact.source import write_source_sidecar
from modules.shot_planner.entities import Entity

logger = logging.getLogger(__name__)

_MAX_BYTES = 25_000_000
_MAX_ATTEMPTS = 3
_THUMB_WIDTH = "1280"
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_COMMONS = "https://commons.wikimedia.org/w/api.php"
_MET_SEARCH = "https://collectionapi.metmuseum.org/public/collection/v1/search"
_MET_OBJECT = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{object_id}"
_BAD_TITLE = (
    "battalion",
    "air defense",
    "regiment",
    "squad leader",
    "coat of arms",
    "escudo",
    "wikidata",
    "locator map",
    "london gazette",
    "flag of",
    "army",
    "soldier",
    "air force",
    "boeing",
    "airbus",
    "airport",
    "squadron",
    "rocket",
    "dvids",
    "missile",
    "brigade",
    "humvee",
    "helmet",
    "gourd",
    "poporo",
    "mural",
    "paisaje",
    "cafe ",
    "container",
    "mapa",
    " map",
    "museo",
    "popor",
    "quindio",
)
_GENERIC_TOKEN = {
    "golden",
    "ornaments",
    "ornament",
    "gold",
    "ancient",
    "small",
    "figure",
    "planes",
    "plane",
    "jets",
    "jet",
    "airplanes",
    "airplane",
    "aircraft",
}
_VECTOR = (".svg", ".djvu", ".pdf")
_MAX_PHOTOS = 6


@dataclass
class FetchedArtefact:
    still_path: Path | None
    reference_path: Path
    usable_as_still: bool
    provider: str
    license_name: str
    credit_line: str
    title: str
    source_url: str
    author: str
    width: int
    height: int


def credit_required(license_name: str) -> bool:
    return license_name in {"cc_by", "cc_by_sa"}


def fetch_artefact(
    entity: Entity,
    *,
    channel: str,
    dest: Path | None = None,
    cache_dir: Path | None = None,
    client: HttpClient | None = None,
) -> FetchedArtefact | None:
    """Return a cached or freshly downloaded photograph. None when nothing passes."""
    folder = Path(cache_dir) if cache_dir else _default_cache(channel, entity.id)
    folder.mkdir(parents=True, exist_ok=True)
    gallery = _read_gallery(folder)
    if not gallery:
        single = _read_cache(folder)
        if single is not None:
            gallery = [single]
    if gallery:
        best = _prefer_still(gallery)
        if dest is not None and best.still_path is not None:
            _publish(best, dest, entity)
            return _point_at(best, dest)
        return best
    http = client or RequestsClient(timeout=20.0)
    saved = _download_gallery(entity, folder, http)
    if not saved:
        return None
    best = _prefer_still(saved)
    if dest is not None and best.still_path is not None:
        _publish(best, dest, entity)
        return _point_at(best, dest)
    return best


def fetch_gallery(
    entity: Entity,
    *,
    channel: str,
    cache_dir: Path | None = None,
    client: HttpClient | None = None,
) -> list[FetchedArtefact]:
    """Every cached photograph for ``entity``, stills first."""
    folder = Path(cache_dir) if cache_dir else _default_cache(channel, entity.id)
    folder.mkdir(parents=True, exist_ok=True)
    gallery = _read_gallery(folder)
    if gallery:
        return gallery
    single = _read_cache(folder)
    if single is not None:
        return [single]
    http = client or RequestsClient(timeout=20.0)
    return _download_gallery(entity, folder, http)


def list_candidates(entity: Entity, client: HttpClient | None = None) -> list[Candidate]:
    """Licensed search hits with sizes. Does not download bytes."""
    http = client or RequestsClient(timeout=20.0)
    return _collect(entity, http)


def artefact_cache_dir(channel: str, entity_id: str) -> Path:
    """Permanent reference cache: ``channels_config/<channel>/store/artefact_cache/``.

    A Commons file is downloaded once. ``_tmp`` is scratch and must not hold it.
    """
    from utils.pipeline_paths import channel_store_dir

    slug = re.sub(r"[^a-z0-9_-]+", "_", (entity_id or "entity").strip().lower()).strip("_")
    root = channel_store_dir(channel or "ancient_knowledge", create=True)
    return root / "artefact_cache" / (slug or "entity")


def _default_cache(channel: str, entity_id: str) -> Path:
    return artefact_cache_dir(channel, entity_id)


def _prefer_still(photos: list[FetchedArtefact]) -> FetchedArtefact:
    for photo in photos:
        if photo.usable_as_still:
            return photo
    return photos[0]


def _collect(entity: Entity, client: HttpClient) -> list[Candidate]:
    """Title-locked files plus licensed search hits. Army and modern aircraft drop out."""
    found: list[Candidate] = []
    locked: list[Candidate] = []
    seen: set[str] = set()

    def add(items: list[Candidate], *, title_lock: bool) -> None:
        for item in items:
            if not _accept(item, entity, title_lock=title_lock):
                continue
            key = (item.download_url or item.source_url or item.title).split("?")[0].lower()
            if not key or key in seen:
                continue
            seen.add(key)
            if title_lock:
                locked.append(item)
            else:
                found.append(item)

    add(_title_candidates(entity, client), title_lock=True)
    for query in _queries(entity):
        add(_commons_search(query, client), title_lock=False)
        add(_openverse_search(query, client), title_lock=False)
        add(_met_search(query, client), title_lock=False)
        add(_smithsonian_search(query, client), title_lock=False)
        add(_europeana_search(query, client), title_lock=False)
    for category in getattr(entity, "commons_categories", ()) or ():
        add(_commons_category(str(category), client), title_lock=False)
    def rank(item: Candidate) -> tuple[int, float]:
        return (_usable_rank(item), _score(item, entity))

    locked.sort(key=rank, reverse=True)
    found.sort(key=rank, reverse=True)
    # The known file stays even when search returns a full page of larger hits.
    return (locked + found)[:_MAX_PHOTOS]


def _usable_rank(candidate: Candidate) -> int:
    long_side = max(candidate.width, candidate.height)
    if long_side >= 1080:
        return 3
    if long_side >= 720:
        return 2
    if min(candidate.width, candidate.height) >= 200:
        return 1
    return 0


def _queries(entity: Entity) -> list[str]:
    queries = [item for item in entity.commons_search if item.strip()]
    if not queries:
        queries = [entity.name]
    return queries[:6]


def _title_candidates(entity: Entity, client: HttpClient) -> list[Candidate]:
    titles = [
        item if item.startswith("File:") else f"File:{item}"
        for item in entity.commons_titles
    ]
    if not titles:
        return []
    response = client.get(
        _COMMONS,
        params={
            "action": "query",
            "format": "json",
            "titles": "|".join(titles[:8]),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": _THUMB_WIDTH,
        },
        headers={"User-Agent": USER_AGENT},
    )
    if not response.ok:
        return []
    return parse_wikimedia(response.json())


def _commons_search(query: str, client: HttpClient) -> list[Candidate]:
    try:
        response = client.get(
            _COMMONS,
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": f"filetype:bitmap {query}",
                "gsrnamespace": "6",
                "gsrlimit": "8",
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": _THUMB_WIDTH,
            },
            headers={"User-Agent": USER_AGENT},
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Commons search failed (%s)", type(exc).__name__)
        return []
    if not response.ok:
        logger.warning("Commons search failed | status=%s | query=%s", response.status, query)
        return []
    return parse_wikimedia(response.json())


def _commons_category(category: str, client: HttpClient) -> list[Candidate]:
    title = category if category.lower().startswith("category:") else f"Category:{category}"
    try:
        response = client.get(
            _COMMONS,
            params={
                "action": "query",
                "format": "json",
                "list": "categorymembers",
                "cmtitle": title,
                "cmtype": "file",
                "cmlimit": "10",
            },
            headers={"User-Agent": USER_AGENT},
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Commons category failed (%s)", type(exc).__name__)
        return []
    if not response.ok:
        logger.warning("Commons category failed | status=%s | category=%s", response.status, title)
        return []
    try:
        members = ((response.json().get("query") or {}).get("categorymembers") or [])
    except Exception:  # noqa: BLE001
        return []
    titles = [
        str(row.get("title") or "")
        for row in members
        if isinstance(row, dict) and str(row.get("title") or "").lower().endswith(
            (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")
        )
    ]
    if not titles:
        return []
    try:
        info = client.get(
            _COMMONS,
            params={
                "action": "query",
                "format": "json",
                "titles": "|".join(titles[:8]),
                "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "iiurlwidth": _THUMB_WIDTH,
            },
            headers={"User-Agent": USER_AGENT},
        )
    except Exception:  # noqa: BLE001
        return []
    if not info.ok:
        return []
    return parse_wikimedia(info.json())


def _openverse_search(query: str, client: HttpClient) -> list[Candidate]:
    try:
        from agents.media.free_images.providers import OpenverseProvider

        return OpenverseProvider().search(query, client, limit=6)
    except Exception as exc:  # noqa: BLE001
        logger.info("Openverse search failed (%s)", type(exc).__name__)
        return []


def _europeana_search(query: str, client: HttpClient) -> list[Candidate]:
    """Europeana's search API is free and key-gated. No key means skip."""
    import os

    key = (os.getenv("EUROPEANA_API_KEY") or "").strip()
    if not key:
        return []
    try:
        response = client.get(
            "https://api.europeana.eu/record/v2/search.json",
            params={
                "wskey": key,
                "query": query,
                "media": "true",
                "reusability": "open",
                "qf": "TYPE:IMAGE",
                "rows": "6",
                "profile": "rich",
            },
            headers={"User-Agent": USER_AGENT},
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Europeana search failed (%s)", type(exc).__name__)
        return []
    if response.status == 401 or response.status == 403:
        logger.warning("Europeana search skipped | status=%s", response.status)
        return []
    if not response.ok:
        logger.warning("Europeana search failed | status=%s", response.status)
        return []
    try:
        items = (response.json().get("items") or [])
    except Exception:  # noqa: BLE001
        return []
    found: list[Candidate] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        shown = row.get("edmIsShownBy") or row.get("edmPreview") or []
        url = shown[0] if isinstance(shown, list) and shown else ""
        rights = row.get("rights") or []
        right = rights[0] if isinstance(rights, list) and rights else ""
        title = row.get("title") or []
        name = title[0] if isinstance(title, list) and title else ""
        creators = row.get("dcCreator") or []
        author = creators[0] if isinstance(creators, list) and creators else ""
        license_raw = _europeana_license(str(right))
        if license_allowed(license_raw) is None:
            continue
        found.append(
            Candidate(
                provider="europeana",
                title=str(name),
                description="",
                author=str(author),
                license_raw=license_raw,
                license_url=str(right),
                source_url=str(row.get("guid") or url),
                download_url=str(url),
                thumbnail_url=str(url),
                width=0,
                height=0,
            )
        )
    return [item for item in found if item.download_url]


def _europeana_license(right: str) -> str:
    """Map a Europeana rights URL onto a string the allowlist already understands."""
    text = (right or "").lower()
    if "publicdomain/zero" in text or "creativecommons.org/publicdomain/zero" in text:
        return "CC0"
    if "publicdomain/mark" in text or "noc-oklr" in text:
        return "Public domain"
    if "by-sa" in text:
        return "CC BY-SA"
    if "/by/" in text or "licenses/by" in text:
        return "CC BY"
    return right


def _met_get(client: HttpClient, url: str, params: dict | None = None):
    """One try, then one retry. HTTP 410 is skipped immediately."""
    last = None
    for _attempt in range(2):
        try:
            last = client.get(url, params=params, headers={"User-Agent": USER_AGENT})
        except Exception as exc:  # noqa: BLE001
            logger.info("Met request failed (%s)", type(exc).__name__)
            last = None
            continue
        if last.status == 410:
            logger.info("Met search skipped | status=410")
            return None
        if last.ok:
            return last
        if last.status not in {429, 500, 502, 503, 504}:
            logger.warning("Met search failed | status=%s", last.status)
            return None
    if last is not None and not last.ok:
        logger.warning("Met search failed | status=%s", last.status)
    return None


def _met_search(query: str, client: HttpClient) -> list[Candidate]:
    """Met open access. A 410 or a missing key is an empty result, not an error."""
    response = _met_get(client, _MET_SEARCH, {"q": query, "hasImages": "true"})
    if response is None:
        return []
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        return []
    ids = payload.get("objectIDs") if isinstance(payload, dict) else None
    if not isinstance(ids, list):
        return []
    found: list[Candidate] = []
    for object_id in ids[:3]:
        detail = _met_get(client, _MET_OBJECT.format(object_id=object_id))
        if detail is None:
            continue
        try:
            row = detail.json()
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(row, dict) or not row.get("isPublicDomain"):
            continue
        image = str(row.get("primaryImage") or "")
        if not image:
            continue
        found.append(
            Candidate(
                provider="met",
                title=str(row.get("title") or ""),
                description=str(row.get("objectName") or ""),
                author=str(row.get("artistDisplayName") or "The Metropolitan Museum of Art"),
                license_raw="CC0",
                license_url="https://www.metmuseum.org/about-the-met/policies-and-documents/image-resources",
                source_url=str(row.get("objectURL") or ""),
                download_url=image,
                width=0,
                height=0,
            )
        )
    return found


def _smithsonian_search(query: str, client: HttpClient) -> list[Candidate]:
    import os

    if not (os.getenv("SMITHSONIAN_API_KEY") or "").strip():
        return []
    try:
        from agents.media.free_images.providers import SmithsonianProvider

        return SmithsonianProvider().search(query, client, limit=4)
    except Exception as exc:  # noqa: BLE001
        logger.info("Smithsonian search failed (%s)", type(exc).__name__)
        return []


def _bitmap(candidate: Candidate) -> bool:
    url = (candidate.download_url or "").lower().split("?")[0]
    return not url.endswith(_VECTOR)


def _accept(candidate: Candidate, entity: Entity, *, title_lock: bool) -> bool:
    if not candidate.download_url or not _bitmap(candidate):
        return False
    if license_allowed(candidate.license_raw) is None:
        return False
    title = (candidate.title or "").lower()
    if not title_lock and any(bad in title for bad in _BAD_TITLE):
        return False
    if title_lock:
        return True
    distinctive = _distinctive_tokens(entity)
    # The title has to name the artefact. Descriptions on museum photos say
    # "Quimbaya" for helmets, gourds, and human pendants that are not this object.
    return any(token in title for token in distinctive)


def _distinctive_tokens(entity: Entity) -> list[str]:
    blob = " ".join((entity.name, *entity.aliases))
    return [token for token in _tokens(blob) if token not in _GENERIC_TOKEN]


def _tokens(text: str) -> list[str]:
    stop = {"the", "of", "and", "a", "an", "file"}
    return [
        token
        for token in re.findall(r"[a-z0-9]{4,}", text.lower())
        if token not in stop
    ]


def _score(candidate: Candidate, entity: Entity) -> float:
    tokens = _tokens(entity.name + " " + entity.canonical_shape)
    blob = f"{candidate.title} {candidate.description}".lower()
    hits = sum(1 for token in tokens if token in blob)
    short = min(candidate.width, candidate.height) if candidate.width and candidate.height else 0
    return hits * 2.0 + min(short / 2000.0, 1.0)


def _download(candidate: Candidate, client: HttpClient) -> bytes | None:
    """Prefer a 1280px thumbnail. The original is the fallback."""
    thumb = (candidate.thumbnail_url or "").strip() or _thumbnail_url(candidate, client)
    if thumb and thumb != (candidate.download_url or ""):
        got = _download_url(thumb, client)
        if got is not None:
            return got
    return _download_url(candidate.download_url, client)


def _download_url(url: str, client: HttpClient) -> bytes | None:
    if not url:
        return None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = client.get(url, headers={"User-Agent": USER_AGENT})
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Artefact download failed | status=error | error=%s | attempt=%d/%d | url=%s",
                type(exc).__name__,
                attempt,
                _MAX_ATTEMPTS,
                url,
            )
            if attempt >= _MAX_ATTEMPTS:
                return None
            _pause(float(2 ** (attempt - 1)))
            continue
        if _usable_body(response):
            return response.content
        status = int(getattr(response, "status", 0) or 0)
        logger.warning(
            "Artefact download failed | status=%s | attempt=%d/%d | url=%s",
            status,
            attempt,
            _MAX_ATTEMPTS,
            url,
        )
        if status not in _RETRY_STATUSES or attempt >= _MAX_ATTEMPTS:
            return None
        _pause(_retry_wait(response, attempt))
    return None


def _usable_body(response) -> bool:
    if not response.ok or not response.content:
        return False
    if len(response.content) > _MAX_BYTES:
        return False
    return response.content[:1] != b"<"


def _retry_wait(response, attempt: int) -> float:
    headers = getattr(response, "headers", None) or {}
    raw = str(headers.get("retry-after") or "").strip()
    if raw:
        try:
            return min(max(float(raw), 0.0), 15.0)
        except ValueError:
            try:
                when = parsedate_to_datetime(raw)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                delay = (when - datetime.now(timezone.utc)).total_seconds()
                return min(max(delay, 0.0), 15.0)
            except (TypeError, ValueError, OverflowError):
                pass
    return float(2 ** (attempt - 1))


def _pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _thumbnail_url(candidate: Candidate, client: HttpClient) -> str:
    """A 1280px JPEG. Originals are larger and more often rate-limited."""
    title = ""
    source = candidate.source_url or ""
    if "/wiki/" in source:
        title = unquote(source.split("/wiki/", 1)[1])
    elif candidate.title:
        bare = candidate.title
        title = bare if bare.startswith("File:") else f"File:{bare}"
    if not title:
        return candidate.thumbnail_url or ""
    try:
        response = client.get(
            _COMMONS,
            params={
                "action": "query",
                "format": "json",
                "titles": title,
                "prop": "imageinfo",
                "iiprop": "url",
                "iiurlwidth": _THUMB_WIDTH,
            },
            headers={"User-Agent": USER_AGENT},
        )
    except Exception:  # noqa: BLE001
        return candidate.thumbnail_url or ""
    if not response.ok:
        logger.warning(
            "Artefact thumbnail lookup failed | status=%s | title=%s",
            getattr(response, "status", 0),
            title,
        )
        return candidate.thumbnail_url or ""
    rows = parse_wikimedia(response.json())
    if rows and rows[0].thumbnail_url:
        return rows[0].thumbnail_url
    return candidate.thumbnail_url or ""


def _download_gallery(
    entity: Entity,
    folder: Path,
    client: HttpClient,
) -> list[FetchedArtefact]:
    saved: list[FetchedArtefact] = []
    for index, candidate in enumerate(_collect(entity, client)):
        photo = _save_candidate(entity, folder, client, candidate, index)
        if photo is not None:
            saved.append(photo)
    if not saved:
        return []
    _write_gallery(folder, entity, saved)
    primary = _prefer_still(saved)
    _write_meta(
        folder,
        entity=entity,
        candidate=_candidate_from(primary),
        license_name=primary.license_name,
        width=primary.width,
        height=primary.height,
        still_path=primary.still_path,
        reference_path=primary.reference_path,
        fit="",
    )
    return saved


def _candidate_from(photo: FetchedArtefact) -> Candidate:
    return Candidate(
        provider=photo.provider,
        title=photo.title,
        author=photo.author,
        license_raw=photo.license_name,
        source_url=photo.source_url,
        download_url=photo.source_url,
    )


def _save_candidate(
    entity: Entity,
    folder: Path,
    client: HttpClient,
    candidate: Candidate,
    index: int,
) -> FetchedArtefact | None:
    raw = _download(candidate, client)
    if raw is None:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            image = opened.convert("RGB")
            width, height = image.size
    except Exception as exc:  # noqa: BLE001
        logger.info("Artefact image unreadable (%s)", type(exc).__name__)
        return None
    if not usable_as_reference(width, height):
        logger.info(
            "Artefact image for %s is below the reference floor (%dx%d)",
            entity.id,
            width,
            height,
        )
        return None
    from modules.reel_artefact.pixels import image_defect

    defect = image_defect(image, nbytes=len(raw))
    if defect:
        logger.info(
            "Artefact image rejected | entity=%s | %s | %dx%d",
            entity.id,
            defect,
            width,
            height,
        )
        return None
    canonical = license_allowed(candidate.license_raw) or ""
    reference = folder / f"photo_{index:02d}.jpg"
    image.save(reference, format="JPEG", quality=90)
    still_path: Path | None = None
    fit = ""
    if usable_as_still(width, height):
        frame, fit = prepare_reel_frame(image)
        still_path = folder / f"still_{index:02d}.jpg"
        frame.save(still_path, format="JPEG", quality=92)
        write_motion_sidecar(still_path, profile="slow_push")
    else:
        logger.info(
            "Artefact file for %s is %dx%d; reference only (long side under %d)",
            entity.id,
            width,
            height,
            STILL_MIN_SHORT,
        )
    needs_credit = credit_required(canonical)
    line = ""
    if needs_credit:
        line = credit_line(
            title=candidate.title,
            author=candidate.author,
            license_name=canonical,
            source_url=candidate.source_url,
        )
    provider = "commons" if candidate.provider == "wikimedia" else candidate.provider
    return FetchedArtefact(
        still_path=still_path,
        reference_path=reference,
        usable_as_still=still_path is not None,
        provider=provider,
        license_name=canonical,
        credit_line=line,
        title=candidate.title,
        source_url=candidate.source_url,
        author=candidate.author,
        width=width,
        height=height,
    )


def _write_gallery(folder: Path, entity: Entity, photos: list[FetchedArtefact]) -> None:
    payload = {
        "entity_id": entity.id,
        "photos": [
            {
                "reference": photo.reference_path.name,
                "still": photo.still_path.name if photo.still_path else "",
                "usable_as_still": photo.usable_as_still,
                "provider": photo.provider,
                "license": photo.license_name,
                "credit_line": photo.credit_line,
                "title": photo.title,
                "author": photo.author,
                "source_url": photo.source_url,
                "width": photo.width,
                "height": photo.height,
                "fit": "",
            }
            for photo in photos
        ],
    }
    (folder / "gallery.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_gallery(folder: Path) -> list[FetchedArtefact]:
    path = folder / "gallery.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("photos") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    found: list[FetchedArtefact] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        reference = folder / str(row.get("reference") or "")
        if not reference.is_file():
            continue
        still_name = str(row.get("still") or "")
        still = folder / still_name if still_name else None
        if still is not None and not still.is_file():
            still = None
        found.append(
            FetchedArtefact(
                still_path=still,
                reference_path=reference,
                usable_as_still=bool(row.get("usable_as_still")) and still is not None,
                provider=str(row.get("provider") or "commons"),
                license_name=str(row.get("license") or ""),
                credit_line=str(row.get("credit_line") or ""),
                title=str(row.get("title") or ""),
                source_url=str(row.get("source_url") or ""),
                author=str(row.get("author") or ""),
                width=int(row.get("width") or 0),
                height=int(row.get("height") or 0),
            )
        )
    return found


def _write_meta(
    folder: Path,
    *,
    entity: Entity,
    candidate: Candidate,
    license_name: str,
    width: int,
    height: int,
    still_path: Path | None,
    reference_path: Path,
    fit: str,
) -> dict:
    needs_credit = credit_required(license_name)
    line = credit_line(
        title=candidate.title,
        author=candidate.author,
        license_name=license_name,
        source_url=candidate.source_url,
    )
    payload = {
        "entity_id": entity.id,
        "provider": "commons" if candidate.provider == "wikimedia" else candidate.provider,
        "license": license_name,
        "credit_required": needs_credit,
        "credit_line": line if needs_credit else "",
        "title": candidate.title,
        "author": candidate.author,
        "source_url": candidate.source_url,
        "license_url": candidate.license_url,
        "width": width,
        "height": height,
        "usable_as_still": still_path is not None,
        "still": still_path.name if still_path else "",
        "reference": reference_path.name,
        "fit": fit,
        "canonical_shape": entity.canonical_shape,
        "min_short_side": STILL_MIN_SHORT,
    }
    (folder / "meta.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return payload


def _read_cache(folder: Path) -> FetchedArtefact | None:
    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    reference = folder / str(payload.get("reference") or "reference.jpg")
    if not reference.is_file():
        return None
    return _from_meta(folder, payload)


def _from_meta(folder: Path, payload: dict) -> FetchedArtefact:
    still_name = str(payload.get("still") or "")
    still = folder / still_name if still_name else None
    if still is not None and not still.is_file():
        still = None
    reference = folder / str(payload.get("reference") or "reference.jpg")
    return FetchedArtefact(
        still_path=still,
        reference_path=reference,
        usable_as_still=bool(payload.get("usable_as_still")) and still is not None,
        provider=str(payload.get("provider") or "commons"),
        license_name=str(payload.get("license") or ""),
        credit_line=str(payload.get("credit_line") or ""),
        title=str(payload.get("title") or ""),
        source_url=str(payload.get("source_url") or ""),
        author=str(payload.get("author") or ""),
        width=int(payload.get("width") or 0),
        height=int(payload.get("height") or 0),
    )


def _publish(fetched: FetchedArtefact, dest: Path, entity: Entity) -> None:
    if fetched.still_path is None:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(fetched.still_path.read_bytes())
    write_motion_sidecar(dest)
    attribution = Attribution(
        title=fetched.title,
        author=fetched.author,
        license=fetched.license_name,
        source_url=fetched.source_url,
        provider=fetched.provider,
    )
    write_sidecar(dest, attribution, credit=fetched.credit_line)
    write_source_sidecar(
        dest,
        {
            "provider": fetched.provider,
            "origin": fetched.provider,
            "license": fetched.license_name,
            "credit_line": fetched.credit_line,
            "credit_required": bool(fetched.credit_line),
            "reference_image": str(fetched.reference_path),
            "canonical_shape": entity.canonical_shape,
            "title": fetched.title,
            "source_url": fetched.source_url,
            "model_id": "",
        },
    )


def _point_at(fetched: FetchedArtefact, dest: Path) -> FetchedArtefact:
    return FetchedArtefact(
        still_path=dest if dest.is_file() else fetched.still_path,
        reference_path=fetched.reference_path,
        usable_as_still=fetched.usable_as_still and dest.is_file(),
        provider=fetched.provider,
        license_name=fetched.license_name,
        credit_line=fetched.credit_line,
        title=fetched.title,
        source_url=fetched.source_url,
        author=fetched.author,
        width=fetched.width,
        height=fetched.height,
    )
