# -*- coding: utf-8 -*-
"""Search, filter, rank, and download one free-license image."""

from __future__ import annotations

import io
import logging
import os
import threading
from pathlib import Path

from PIL import Image

from production.providers.image.free.attribution import credit_line, write_sidecar
from production.providers.image.free.cache import QueryCache, cache_key
from production.providers.image.free.http import HttpClient, RequestsClient
from production.providers.image.free.licenses import license_allowed
from production.providers.image.free.models import Attribution, Candidate, FreeImageHit, SourceDecision
from production.providers.image.free.policy import resolve_policy
from production.providers.image.free.providers import default_providers
from production.providers.image.free.quality import (
    image_has_watermark,
    metadata_has_watermark,
    optional_clip_scorer,
    prepare_portrait,
    rank_candidates,
)
from production.providers.image.free.query import build_query

logger = logging.getLogger(__name__)

_DOWNLOAD_LOCK = threading.Lock()
_MAX_BYTES = 30_000_000


def resolve_image_source(
    *,
    channel: str,
    dest: Path,
    topic: str = "",
    entity: str = "",
    sentence: str = "",
    policy: str | None = None,
    page_cfg: dict | None = None,
    providers: list | None = None,
    client: HttpClient | None = None,
    cache_dir: Path | None = None,
    min_short_side: int | None = None,
    allow_smart_crop: bool = True,
    vision_check=None,
    allowlist: frozenset[str] | set[str] | None = None,
    producer: str = "",
) -> SourceDecision:
    """Find one usable photograph. Does not call Flux.

    Downloads are serialized and limited to the file about to be vetted.
    Search metadata is cached per query.
    """
    chosen_policy = policy or resolve_policy(channel, page_cfg)
    query = build_query(topic=topic, entity=entity, sentence=sentence)
    if chosen_policy == "ai_only":
        return SourceDecision(policy=chosen_policy, query=query, reason="ai_only")
    if not query:
        return SourceDecision(policy=chosen_policy, query="", reason="empty_query")

    with _DOWNLOAD_LOCK:
        return _resolve_locked(
            channel=channel,
            dest=Path(dest),
            query=query,
            policy=chosen_policy,
            providers=providers,
            client=client,
            cache_dir=cache_dir,
            min_short_side=min_short_side,
            allow_smart_crop=allow_smart_crop,
            vision_check=vision_check,
            allowlist=allowlist,
            producer=producer,
        )


def _resolve_locked(
    *,
    channel: str,
    dest: Path,
    query: str,
    policy: str,
    providers: list | None,
    client: HttpClient | None,
    cache_dir: Path | None,
    min_short_side: int | None,
    allow_smart_crop: bool,
    vision_check,
    allowlist,
    producer: str,
) -> SourceDecision:
    short_side = min_short_side if min_short_side is not None else _min_short_side()
    active = list(providers) if providers is not None else default_providers()
    http = client or RequestsClient()
    names = [getattr(provider, "name", "provider") for provider in active]
    cache = QueryCache(_cache_dir(channel, cache_dir))
    key = cache_key(query, names)
    cached = cache.read(key)
    if cached is None:
        collected: list[Candidate] = []
        any_success = False
        for provider in active:
            try:
                rows = provider.search(query, http, limit=8)
                any_success = True
                collected.extend(rows or [])
            except Exception as exc:  # noqa: BLE001 — one provider must not blank the rest
                logger.info(
                    "Free image provider %s failed (%s)",
                    getattr(provider, "name", "?"),
                    type(exc).__name__,
                )
        if any_success:
            cache.write(key, query, collected)
        candidates = collected
    else:
        candidates = cached

    accepted: list[Candidate] = []
    for candidate in candidates:
        if metadata_has_watermark(candidate):
            continue
        if license_allowed(candidate.license_raw, allowlist) is None:
            continue
        if _known_too_small(candidate, short_side, allow_smart_crop):
            continue
        accepted.append(candidate)
    ranked = rank_candidates(accepted, query)
    clip = optional_clip_scorer()
    if clip is not None and ranked:
        ranked = _rerank_with_clip(ranked[:5], query, http, clip) + ranked[5:]

    for candidate in ranked:
        hit = _download_one(
            candidate,
            providers_by_name={getattr(item, "name", ""): item for item in active},
            client=http,
            dest=dest,
            query=query,
            channel=channel,
            min_short_side=short_side,
            allow_smart_crop=allow_smart_crop,
            vision_check=vision_check,
            allowlist=allowlist,
            producer=producer,
            score=_score_of(candidate, query),
        )
        if hit is not None:
            logger.info(
                "Free image selected provider=%s channel=%s query=%s",
                hit.provider,
                channel,
                query,
            )
            return SourceDecision(
                policy=policy,
                query=query,
                reason="free_image",
                hit=hit,
            )
    return SourceDecision(policy=policy, query=query, reason="none_passed")


def _download_one(
    candidate: Candidate,
    *,
    providers_by_name: dict,
    client: HttpClient,
    dest: Path,
    query: str,
    channel: str,
    min_short_side: int,
    allow_smart_crop: bool,
    vision_check,
    allowlist,
    producer: str,
    score: float,
) -> FreeImageHit | None:
    provider = providers_by_name.get(candidate.provider)
    if provider is not None and hasattr(provider, "resolve_download"):
        try:
            candidate = provider.resolve_download(candidate, client)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "Could not resolve %s download (%s)",
                candidate.provider,
                type(exc).__name__,
            )
            return None
    if not candidate.download_url:
        return None
    try:
        response = client.get(candidate.download_url)
    except Exception as exc:  # noqa: BLE001
        logger.info("Image download failed (%s)", type(exc).__name__)
        return None
    if not response.ok or not response.content or len(response.content) > _MAX_BYTES:
        return None
    try:
        with Image.open(io.BytesIO(response.content)) as raw:
            raw.load()
            image = raw.convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    if image_has_watermark(image):
        return None
    if vision_check is not None:
        try:
            if vision_check(image):
                return None
        except Exception:  # noqa: BLE001
            return None
    prepared = prepare_portrait(
        image,
        min_short_side=min_short_side,
        allow_smart_crop=allow_smart_crop,
    )
    if prepared is None:
        return None
    portrait, cropped = prepared
    canonical = license_allowed(candidate.license_raw, allowlist)
    if canonical is None:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    portrait.save(dest, format="JPEG", quality=92, optimize=True)
    attribution = Attribution(
        title=candidate.title,
        author=candidate.author,
        license=canonical,
        source_url=candidate.source_url,
        license_url=candidate.license_url,
        provider=candidate.provider,
    )
    credit = credit_line(
        title=candidate.title,
        author=candidate.author,
        license_name=canonical,
        source_url=candidate.source_url,
    )
    sidecar = write_sidecar(
        dest,
        attribution,
        credit=credit,
        extra={
            "query": query,
            "channel": channel,
            "producer": producer,
            "width": portrait.size[0],
            "height": portrait.size[1],
            "smart_cropped": cropped,
            "score": round(score, 4),
            "download_url": candidate.download_url,
        },
    )
    width, height = portrait.size
    return FreeImageHit(
        path=dest,
        sidecar_path=sidecar,
        attribution=attribution,
        credit_line=credit,
        width=width,
        height=height,
        score=score,
        provider=candidate.provider,
        smart_cropped=cropped,
    )


def _known_too_small(candidate: Candidate, min_short_side: int, allow_smart_crop: bool) -> bool:
    if not candidate.width or not candidate.height:
        return False
    short_side = min(candidate.width, candidate.height)
    already_portrait = (
        candidate.height > candidate.width and short_side >= min_short_side
    )
    if already_portrait:
        return False
    if not allow_smart_crop:
        return True
    return candidate.width < min_short_side or candidate.height <= min_short_side


def _score_of(candidate: Candidate, query: str) -> float:
    from production.providers.image.free.quality import text_resolution_score
    from production.providers.image.free.query import tokenize

    return text_resolution_score(candidate, tokenize(query))


def _rerank_with_clip(candidates, query, client, scorer) -> list[Candidate]:
    scored: list[tuple[float, Candidate]] = []
    for candidate in candidates:
        url = candidate.thumbnail_url or candidate.download_url
        if not url:
            scored.append((0.0, candidate))
            continue
        try:
            response = client.get(url)
            with Image.open(io.BytesIO(response.content)) as raw:
                bonus = float(scorer(query, raw.convert("RGB")))
        except Exception:  # noqa: BLE001
            bonus = 0.0
        scored.append((bonus, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [candidate for _, candidate in scored]


def _min_short_side() -> int:
    raw = (os.getenv("FREE_IMAGE_MIN_SHORT_SIDE") or "").strip()
    try:
        return max(1, int(raw)) if raw else 1000
    except ValueError:
        return 1000


def _cache_dir(channel: str, explicit: Path | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    from utils.pipeline_paths import temp_path

    return temp_path(channel or "shared", "free_images", "cache")
