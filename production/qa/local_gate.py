# -*- coding: utf-8 -*-
"""Pure-Python still checks that run before any vision LLM.

A still is accepted without an LLM when it is large enough, sharp enough,
not a near-duplicate of a still already used in this reel, and (when a
reference embedding is available) close to the narration. Only a borderline
score reaches the cheap vision judge. Nothing here needs a GPU or an API.

Embeddings are optional. ``REEL_LOCAL_EMBED`` names a model (default
``clip:ViT-B-32``). When the model is not installed the similarity check is
skipped and the other checks still run, so a box without the weights is
never blocked.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_EDGE_PX: int = 720
# Only a blank or broken frame lands below this. A calm sky or sea photo
# scores well above it, so the Flux landscape category is never rejected.
MIN_SHARPNESS: float = 8.0
PHASH_DISTANCE: int = 6
ACCEPT_SIMILARITY: float = 0.30
REJECT_SIMILARITY: float = 0.18

_LOCK = threading.Lock()
_USED: list[tuple[str, int]] = []
_MODEL = None
_MODEL_TRIED = False


def reset_gate() -> None:
    """A new reel starts with no seen stills."""
    with _LOCK:
        _USED.clear()


@dataclass
class GateResult:
    path: str
    ok: bool
    needs_llm: bool
    reasons: list[str] = field(default_factory=list)
    width: int = 0
    height: int = 0
    sharpness: float = 0.0
    phash: str = ""
    duplicate_of: str = ""
    similarity: float | None = None

    def as_dict(self) -> dict:
        return {
            "local_gate": "pass" if self.ok else "fail",
            "needs_llm": self.needs_llm,
            "reasons": list(self.reasons),
            "width": self.width,
            "height": self.height,
            "sharpness": round(self.sharpness, 1),
            "phash": self.phash,
            "duplicate_of": self.duplicate_of,
            "similarity": None if self.similarity is None else round(self.similarity, 4),
        }


def _int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _phash(image) -> int:
    """64-bit average hash. No dependency beyond Pillow."""
    small = image.convert("L").resize((8, 8))
    pixels = list(small.getdata())
    mean = sum(pixels) / len(pixels)
    bits = 0
    for i, value in enumerate(pixels):
        if value >= mean:
            bits |= 1 << i
    return bits


def _distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _sharpness(image) -> float:
    """Variance of a 3x3 Laplacian. A flat frame lands near 0."""
    grey = image.convert("L")
    if grey.width > 512:
        grey = grey.resize((512, max(1, int(grey.height * 512 / grey.width))))
    px = grey.load()
    width, height = grey.size
    total = 0.0
    count = 0
    for y in range(1, height - 1, 2):
        for x in range(1, width - 1, 2):
            lap = (
                -4 * px[x, y]
                + px[x - 1, y]
                + px[x + 1, y]
                + px[x, y - 1]
                + px[x, y + 1]
            )
            total += lap * lap
            count += 1
    return total / count if count else 0.0


def _embedder():
    """The optional CLIP/SigLIP model, loaded once. None when unavailable."""
    global _MODEL, _MODEL_TRIED
    if _MODEL_TRIED:
        return _MODEL
    with _LOCK:
        if _MODEL_TRIED:
            return _MODEL
        _MODEL_TRIED = True
        name = (os.getenv("REEL_LOCAL_EMBED") or "clip:ViT-B-32").strip()
        if not name or name.lower() in {"0", "off", "none"}:
            return None
        try:
            import open_clip

            family, _, variant = name.partition(":")
            weights = "openai" if family == "clip" else "webli"
            model, _, preprocess = open_clip.create_model_and_transforms(
                variant or "ViT-B-32", pretrained=weights,
            )
            model.eval()
            tokenizer = open_clip.get_tokenizer(variant or "ViT-B-32")
            _MODEL = (model, preprocess, tokenizer)
            logger.info("local embedder loaded | %s", name)
        except Exception as exc:  # noqa: BLE001 — weights or torch are optional
            logger.info(
                "local embedder unavailable (%s) — similarity check skipped",
                type(exc).__name__,
            )
            _MODEL = None
    return _MODEL


def _similarity(image, text: str) -> float | None:
    bundle = _embedder()
    if bundle is None or not (text or "").strip():
        return None
    model, preprocess, tokenizer = bundle
    try:
        import torch

        with torch.no_grad():
            image_vec = model.encode_image(preprocess(image).unsqueeze(0))
            text_vec = model.encode_text(tokenizer([text[:300]]))
            image_vec = image_vec / image_vec.norm(dim=-1, keepdim=True)
            text_vec = text_vec / text_vec.norm(dim=-1, keepdim=True)
            return float((image_vec @ text_vec.T).item())
    except Exception as exc:  # noqa: BLE001
        logger.info("local similarity failed (%s)", type(exc).__name__)
        return None


def check_still(
    path: Path | str,
    *,
    narration: str = "",
    vertical: bool = True,
    remember: bool = True,
) -> GateResult:
    """Score one still. ``remember`` records a passing hash for later dedupe."""
    target = Path(path)
    result = GateResult(path=str(target), ok=False, needs_llm=False)
    try:
        from PIL import Image

        with Image.open(target) as opened:
            image = opened.convert("RGB")
    except Exception:  # noqa: BLE001
        result.reasons.append("unreadable")
        return result
    result.width, result.height = image.size
    min_edge = _int("REEL_LOCAL_MIN_EDGE_PX", MIN_EDGE_PX)
    notes: list[str] = []
    if min(image.size) < min_edge:
        notes.append(f"small:{min(image.size)}px")
    if vertical and image.height < image.width:
        notes.append("landscape")
    result.sharpness = _sharpness(image)
    floor = _float("REEL_LOCAL_MIN_SHARPNESS", MIN_SHARPNESS)
    if result.sharpness < floor:
        result.reasons.append(f"blur:{result.sharpness:.0f}")
    digest = _phash(image)
    result.phash = f"{digest:016x}"
    limit = _int("REEL_LOCAL_PHASH_DISTANCE", PHASH_DISTANCE)
    with _LOCK:
        for seen_path, seen in _USED:
            if _distance(digest, seen) <= limit:
                result.duplicate_of = seen_path
                result.reasons.append("duplicate")
                break
    result.similarity = _similarity(image, narration)
    accept = _float("REEL_LOCAL_ACCEPT_SIMILARITY", ACCEPT_SIMILARITY)
    reject = _float("REEL_LOCAL_REJECT_SIMILARITY", REJECT_SIMILARITY)
    if result.similarity is not None:
        if result.similarity < reject:
            result.reasons.append(f"off-topic:{result.similarity:.2f}")
        elif result.similarity < accept:
            result.needs_llm = True
    hard = [r for r in result.reasons if not r.startswith("off-topic")]
    # Size and orientation are notes: the crop step upsizes and reframes, and
    # a judge should not be skipped because the source was landscape.
    result.reasons.extend(notes)
    if hard:
        result.ok = False
        result.needs_llm = False
        return result
    if result.reasons and any(r.startswith("off-topic") for r in result.reasons):
        result.ok = False
        result.needs_llm = False
        return result
    if result.needs_llm:
        return result
    result.ok = True
    if remember:
        with _LOCK:
            _USED.append((str(target), digest))
    return result
