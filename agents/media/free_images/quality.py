# -*- coding: utf-8 -*-
"""Relevance ranking, size, orientation, and watermark checks."""

from __future__ import annotations

import logging
import os
from typing import Callable

from PIL import Image

from agents.media.free_images.models import Candidate
from agents.media.free_images.query import tokenize

logger = logging.getLogger(__name__)

_STOCK_HINTS = (
    "shutterstock",
    "gettyimages",
    "getty images",
    "istockphoto",
    "istock",
    "alamy",
    "dreamstime",
    "123rf",
    "adobestock",
    "adobe stock",
    "depositphotos",
    "bigstock",
    "watermark",
    "pond5",
)

ImageScorer = Callable[[str, Image.Image], float]


def metadata_has_watermark(candidate: Candidate) -> bool:
    blob = " ".join(
        (
            candidate.title,
            candidate.description,
            candidate.download_url,
            candidate.thumbnail_url,
            candidate.source_url,
            candidate.author,
        )
    ).lower()
    return any(hint in blob for hint in _STOCK_HINTS)


def image_has_watermark(img: Image.Image) -> bool:
    """Corner and bottom-strip heuristic for burned-in logos and stock text.

    A region counts when a small share of its pixels are near-white or
    near-black ink on an otherwise flat field. Busy photographic corners
    stay under the threshold because too much of the patch disagrees with
    the local mean.
    """
    rgb = img.convert("RGB")
    width, height = rgb.size
    if width < 32 or height < 32:
        return False
    corner_w = max(16, int(width * 0.22))
    corner_h = max(16, int(height * 0.10))
    regions = [
        rgb.crop((0, 0, corner_w, corner_h)),
        rgb.crop((width - corner_w, 0, width, corner_h)),
        rgb.crop((0, height - corner_h, corner_w, height)),
        rgb.crop((width - corner_w, height - corner_h, width, height)),
    ]
    strip_top = height - max(12, int(height * 0.06))
    regions.append(
        rgb.crop((int(width * 0.25), strip_top, int(width * 0.75), height))
    )
    return any(_region_looks_like_text(region) for region in regions)


def _region_looks_like_text(region: Image.Image) -> bool:
    gray = region.convert("L")
    try:
        pixels = list(gray.get_flattened_data())
    except AttributeError:  # Pillow < 13
        pixels = list(gray.getdata())
    if not pixels:
        return False
    mean = sum(pixels) / len(pixels)
    ink = [pixel for pixel in pixels if abs(pixel - mean) > 70]
    fraction = len(ink) / len(pixels)
    if not 0.008 <= fraction <= 0.22:
        return False
    extreme = sum(1 for pixel in ink if pixel < 35 or pixel > 220)
    if extreme / max(1, len(ink)) < 0.55:
        return False
    variance = sum((pixel - mean) ** 2 for pixel in pixels) / len(pixels)
    return (variance ** 0.5) >= 16


def text_resolution_score(candidate: Candidate, query_tokens: list[str]) -> float:
    title_tokens = set(tokenize(candidate.title))
    description_tokens = set(tokenize(candidate.description))
    if query_tokens:
        title_hits = sum(1 for token in query_tokens if token in title_tokens)
        description_hits = sum(
            1
            for token in query_tokens
            if token in description_tokens and token not in title_tokens
        )
        token_score = (2.0 * title_hits + description_hits) / len(query_tokens)
    else:
        token_score = 0.0
    short_side = 0
    if candidate.width and candidate.height:
        short_side = min(candidate.width, candidate.height)
    resolution = min(short_side / 2000.0, 1.0) if short_side else 0.2
    portrait_bonus = 0.2 if candidate.height > candidate.width else 0.0
    return token_score + 0.35 * resolution + portrait_bonus


def rank_candidates(
    candidates: list[Candidate],
    query: str,
    *,
    scorer: Callable[[str, Candidate], float | None] | None = None,
) -> list[Candidate]:
    tokens = tokenize(query)
    scored: list[tuple[float, int, Candidate]] = []
    for candidate in candidates:
        score = text_resolution_score(candidate, tokens)
        if scorer is not None:
            try:
                extra = scorer(query, candidate)
            except Exception:  # noqa: BLE001
                extra = None
            if isinstance(extra, (int, float)):
                score += float(extra)
        short_side = 0
        if candidate.width and candidate.height:
            short_side = min(candidate.width, candidate.height)
        scored.append((score, short_side, candidate))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [candidate for _, _, candidate in scored]


def prepare_portrait(
    img: Image.Image,
    *,
    min_short_side: int,
    allow_smart_crop: bool,
) -> tuple[Image.Image, bool] | None:
    """Keep a portrait frame whose short side is at least ``min_short_side``.

    Landscape and square frames are center-cropped to 2:3 when both sides
    are already large enough. That crop is still a template input.
    """
    image = img.convert("RGB")
    width, height = image.size
    if height > width and min(width, height) >= min_short_side:
        return image, False
    if not allow_smart_crop or width < min_short_side or height < min_short_side:
        return None
    if height <= min_short_side:
        return None
    target_width = min(width, max(min_short_side, int(round(height * 2 / 3))))
    if target_width >= height:
        target_width = height - 1
    if target_width < min_short_side:
        return None
    left = max(0, (width - target_width) // 2)
    cropped = image.crop((left, 0, left + target_width, height))
    cropped_w, cropped_h = cropped.size
    if cropped_h > cropped_w and min(cropped_w, cropped_h) >= min_short_side:
        return cropped, True
    return None


def optional_clip_scorer() -> ImageScorer | None:
    """Pixel CLIP score when ``FREE_IMAGE_CLIP=1`` and open_clip imports.

    Missing libraries return None. Ranking then uses title, description,
    and resolution only.
    """
    flag = (os.getenv("FREE_IMAGE_CLIP") or "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return None
    try:
        import open_clip
        import torch
    except ImportError:
        logger.info("FREE_IMAGE_CLIP is set but open_clip/torch is not installed.")
        return None
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="openai"
    )
    tokenizer = open_clip.get_tokenizer("ViT-B-32")
    model.eval()

    def score(query: str, image: Image.Image) -> float:
        image_tensor = preprocess(image.convert("RGB")).unsqueeze(0)
        text = tokenizer([query])
        with torch.no_grad():
            image_features = model.encode_image(image_tensor)
            text_features = model.encode_text(text)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)
            return float((image_features @ text_features.T)[0, 0])

    return score
