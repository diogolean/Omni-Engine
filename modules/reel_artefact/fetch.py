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
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from PIL import Image

from agents.media.free_images.attribution import credit_line, write_sidecar
from agents.media.free_images.http import HttpClient, RequestsClient
from agents.media.free_images.licenses import license_allowed
from agents.media.free_images.models import Attribution, Candidate
from agents.media.free_images.providers import WikimediaProvider, parse_wikimedia
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
)
_VECTOR = (".svg", ".djvu", ".pdf")


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
    cached = _read_cache(folder)
    if cached is not None:
        if dest is not None and cached.still_path is not None:
            _publish(cached, dest, entity)
            return _point_at(cached, dest)
        return cached
    http = client or RequestsClient(timeout=20.0)
    chosen, locked_small = _select(entity, http)
    if chosen is None:
        return None
    if locked_small:
        logger.info(
            "Commons file for %s is below %dpx; reference only",
            entity.id,
            STILL_MIN_SHORT,
        )
    raw = _download(chosen, http)
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
        return None
    canonical = license_allowed(chosen.license_raw) or ""
    still_ok = usable_as_still(width, height)
    reference = folder / "reference.jpg"
    image.save(reference, format="JPEG", quality=90)
    still_path: Path | None = None
    fit = ""
    if still_ok:
        frame, fit = prepare_reel_frame(image)
        still_path = folder / "still.jpg"
        frame.save(still_path, format="JPEG", quality=92)
        write_motion_sidecar(still_path)
    record = _write_meta(
        folder,
        entity=entity,
        candidate=chosen,
        license_name=canonical,
        width=width,
        height=height,
        still_path=still_path,
        reference_path=reference,
        fit=fit,
    )
    fetched = _from_meta(folder, record)
    if dest is not None and fetched.still_path is not None:
        _publish(fetched, dest, entity)
        return _point_at(fetched, dest)
    return fetched


def _default_cache(channel: str, entity_id: str) -> Path:
    from utils.pipeline_paths import temp_path

    return temp_path(channel or "ancient_knowledge", "artefact_cache", entity_id)


def _select(entity: Entity, client: HttpClient) -> tuple[Candidate | None, bool]:
    """Return the candidate and whether a title lock forbids using it as a still.

    A curated file that is too small is the shape reference. A larger search
    hit of a different object must not replace it.
    """
    locked = _title_candidates(entity, client)
    locked_ok = [item for item in locked if _accept(item, entity, title_lock=True)]
    if locked_ok:
        best = locked_ok[0]
        small = not usable_as_still(best.width, best.height)
        return best, small
    found: list[Candidate] = []
    for query in _queries(entity):
        found.extend(_commons_search(query, client))
        found.extend(_met_search(query, client))
        found.extend(_smithsonian_search(query, client))
    ranked = [item for item in found if _accept(item, entity, title_lock=False)]
    ranked.sort(key=lambda item: _score(item, entity), reverse=True)
    if not ranked:
        return None, False
    return ranked[0], False


def _queries(entity: Entity) -> list[str]:
    queries = [item for item in entity.commons_search if item.strip()]
    if not queries:
        queries = [entity.name]
    return queries[:4]


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
        },
    )
    if not response.ok:
        return []
    return parse_wikimedia(response.json())


def _commons_search(query: str, client: HttpClient) -> list[Candidate]:
    try:
        return WikimediaProvider().search(query, client, limit=8)
    except Exception as exc:  # noqa: BLE001
        logger.info("Commons search failed (%s)", type(exc).__name__)
        return []


def _met_search(query: str, client: HttpClient) -> list[Candidate]:
    """Met open access. A 410 or a missing key is an empty result, not an error."""
    try:
        response = client.get(
            _MET_SEARCH,
            params={"q": query, "hasImages": "true"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Met search failed (%s)", type(exc).__name__)
        return []
    if not response.ok:
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
        try:
            detail = client.get(_MET_OBJECT.format(object_id=object_id))
        except Exception:  # noqa: BLE001
            continue
        if not detail.ok:
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
    tokens = _tokens(entity.name + " " + " ".join(entity.commons_search))
    blob = title + " " + (candidate.description or "").lower()
    return any(token in blob for token in tokens)


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
    original = _download_url(candidate.download_url, client)
    if original is not None:
        return original
    thumb = _thumbnail_url(candidate, client)
    if not thumb:
        return None
    return _download_url(thumb, client)


def _download_url(url: str, client: HttpClient) -> bytes | None:
    if not url:
        return None
    try:
        response = client.get(url)
    except Exception as exc:  # noqa: BLE001
        logger.info("Artefact download failed (%s)", type(exc).__name__)
        return None
    if not response.ok or not response.content:
        return None
    if len(response.content) > _MAX_BYTES:
        return None
    if response.content[:1] == b"<":
        return None
    return response.content


def _thumbnail_url(candidate: Candidate, client: HttpClient) -> str:
    """A 1600px JPEG when the original is rate-limited or too large."""
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
                "iiurlwidth": "1600",
            },
        )
    except Exception:  # noqa: BLE001
        return candidate.thumbnail_url or ""
    if not response.ok:
        return candidate.thumbnail_url or ""
    rows = parse_wikimedia(response.json())
    if rows and rows[0].thumbnail_url:
        return rows[0].thumbnail_url
    return candidate.thumbnail_url or ""


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
