"""Portrait cutout contract. No paid call, no invented size, no invented matte.

What this tree actually contains:

* Gemini image sizes the reel adapter will send are ``1K`` and ``2K``
  (``production/providers/settings.py``). Anything else there is collapsed
  to ``1K``. This contract refuses that collapse.
* ``4K`` is not in that allowlist, and there is no 4K list price beside the
  coded 1K/2K constants in ``production/cost/pricing.py``.
* ``create_puppet.generate_character_image`` sends ``aspect_ratio`` only.
  It does not send ``image_size``, so a 1K request is not a verified pixel
  preset on the Animind rig path. Exact 1K and 4K pixel sizes are not in
  this code and are not guessed.
* Factory prompts ask for a solid black stage. ``navy-blue`` in those
  prompts is armor paint, not a backdrop RGB. No navy backdrop tuple and
  no post-cutout softness value were found, so those stages stay
  unconfigured. The cutout is the existing chroma key (the offline path of
  ``create_puppet.remove_background``). Its in-key feather is left untouched.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from animation.parametric.factory.rigger import chroma_key_cutout
from production.cost.pricing import (
    GEMINI_FLASH_IMAGE_1K_USD,
    GEMINI_PRO_IMAGE_1K_USD,
    GEMINI_PRO_IMAGE_2K_USD,
)

# Sizes the existing Gemini image adapter agrees to send. Not pixel dimensions.
ADAPTER_IMAGE_SIZES = ("1K", "2K")
FACTORY_CALL = "products.aiwake.animator.factory.create_puppet.generate_character_image"
CUTOUT_TOOL = "animation.parametric.factory.rigger.chroma_key_cutout"
PRODUCTION_CUTOUT = "products.aiwake.animator.factory.create_puppet.remove_background"
MIN_NATIVE_WIDTH = 1024
THUMB_EDGE = 256

# Coded list prices already in the repo. Not a budget, and not a 4K price.
KNOWN_PRICES = {
    "1K": {
        "usdPerImage": GEMINI_FLASH_IMAGE_1K_USD,
        "model": "gemini-2.5-flash-image",
        "where": "production/cost/pricing.py GEMINI_FLASH_IMAGE_1K_USD",
        "alsoProUsd": GEMINI_PRO_IMAGE_1K_USD,
    },
    "2K": {
        "usdPerImage": GEMINI_PRO_IMAGE_2K_USD,
        "model": "gemini-pro-image",
        "where": "production/cost/pricing.py GEMINI_PRO_IMAGE_2K_USD",
    },
    "4K": None,
}


class PortraitRefused(Exception):
    """The request asked for a resolution this process will not generate."""

    def __init__(self, decision: dict[str, Any]) -> None:
        super().__init__("resolution")
        self.decision = decision


def plan(body: dict[str, Any] | None = None, *, user: str | None = None) -> dict[str, Any]:
    """Resolution decision. Never calls a provider and never upscales."""
    raw = _requested(body or {})
    product = _product_preset(raw)
    mapped = {"1k": "1K", "4k": None}.get(product)
    entitled = server_premium(user)
    reasons: list[str] = []
    if product == "4k":
        reasons.append("adapter_has_no_4k_preset")
        reasons.append("no_4k_list_price")
    if product == "1k":
        reasons.append("factory_call_omits_image_size")
    if product == "unsupported":
        reasons.append("unsupported_preset")
    if not entitled and product == "4k":
        reasons.append("premium_not_granted_by_server")
    reasons.append("generation_not_authorized")
    return {
        "requestedResolution": raw,
        "productPreset": product,
        "adapterPresets": list(ADAPTER_IMAGE_SIZES),
        "mappedPreset": mapped,
        "actualPixels": None,
        "provenance": "unverified",
        "native": False,
        "consume": False,
        "charged": False,
        "upscale": "refused",
        "silentFallback": "refused",
        "clientPremiumIgnored": True,
        "entitlement": "server" if entitled else "absent",
        "reasons": reasons,
        "outputContract": {
            "minWidth": MIN_NATIVE_WIDTH,
            "heightGreaterThanWidth": True,
            "exactRatio": None,
        },
        "stages": stages(),
    }


def server_premium(user: str | None) -> bool:
    """Server allowlist only. A client flag is not read."""
    import os

    raw = os.getenv("ANIMIND_PREMIUM_USERS") or ""
    allowed = {part.strip() for part in raw.split(",") if part.strip()}
    return bool(user) and user in allowed


def stages() -> list[dict[str, Any]]:
    """Navy, then the existing cutout, then softness. Missing knobs stay off."""
    return [
        {
            "id": "backdrop",
            "role": "temporary extraction stage",
            "found": "factory prompts request a solid black background",
            "navyRgb": None,
            "status": "unconfigured",
            "note": "navy-blue in the factory prompts is armor, not a backdrop color",
        },
        {
            "id": "cutout",
            "tool": CUTOUT_TOOL,
            "production": PRODUCTION_CUTOUT,
            "status": "ready",
        },
        {
            "id": "softness",
            "applied": False,
            "parameter": None,
            "status": "parameter_absent",
            "note": "the chroma key's own feather is not a later softness pass and is not retuned",
        },
    ]


def measure(image: Image.Image, *, provenance: str = "source") -> dict[str, Any]:
    """Pixel facts. An upscaled raster does not pass the native portrait gate."""
    width, height = image.size
    native = provenance != "upscaled" and width >= MIN_NATIVE_WIDTH and height > width
    return {
        "width": width,
        "height": height,
        "portrait": height > width,
        "provenance": provenance,
        "meetsNativePortrait1k": native,
        "upscaled": provenance == "upscaled",
    }


def describe_legacy(image: Image.Image) -> dict[str, Any]:
    """Smaller plates stay as they are. The upgrade is a new generation, not a resize."""
    facts = measure(image, provenance="legacy")
    if facts["meetsNativePortrait1k"]:
        return facts | {"limitation": None, "upgrade": None, "rewritten": False}
    return facts | {
        "limitation": "below_native_portrait_1k",
        "upgrade": "regenerate",
        "rewritten": False,
    }


def extract(
    source: Image.Image,
    dest: Path,
    *,
    probes: dict[str, tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Keep the source, cut with the existing key, and do not soften afterwards.

    The scene is not baked in. A thumb is a smaller copy for UI only.
    """
    from .spend.guard import admit_unpriced  # noqa: PLC0415

    dest.mkdir(parents=True, exist_ok=True)
    original = dest / "source.png"
    source.convert("RGBA").save(original, format="PNG")
    source_bytes = original.read_bytes()
    with admit_unpriced(provider="local", model="chroma_key_cutout", operation="cutout"):
        cut = chroma_key_cutout(source)
    # Softness parameter was not found. Leave the cutout alpha alone.
    alpha = np.asarray(cut.getchannel("A"))
    cutout_path = dest / "cutout.png"
    cut.save(cutout_path, format="PNG")
    if original.read_bytes() != source_bytes:
        raise RuntimeError("source was rewritten")
    if not np.array_equal(alpha, np.asarray(Image.open(cutout_path).getchannel("A"))):
        raise RuntimeError("softness changed the cutout")
    thumb = cut.copy()
    thumb.thumbnail((THUMB_EDGE, THUMB_EDGE))
    thumb.save(dest / "thumb.png", format="PNG")
    qa = _qa(source, cut, probes or {})
    native = measure(source)
    ready = bool(qa["pass"] and native["meetsNativePortrait1k"])
    return {
        "source": native,
        "cutout": measure(cut, provenance="cutout"),
        "useful": _useful(cut),
        "thumb": {"width": thumb.size[0], "height": thumb.size[1], "role": "ui"},
        "scenario": "separate",
        "stages": stages(),
        "qa": qa,
        "ready": ready,
        "status": "ready" if ready else "rejected",
        "sourceSha256": hashlib.sha256(source_bytes).hexdigest(),
        "factoryCall": FACTORY_CALL,
    }


def _requested(body: dict[str, Any]) -> str:
    raw = body.get("requestedResolution", body.get("resolution", "1k"))
    return str(raw or "1k").strip()


def _product_preset(raw: str) -> str:
    token = raw.strip().lower()
    if token in {"1k", "1"}:
        return "1k"
    if token in {"4k", "4"}:
        return "4k"
    return "unsupported"


def _useful(image: Image.Image) -> dict[str, int]:
    alpha = np.asarray(image.convert("RGBA"))[..., 3]
    ys, xs = np.nonzero(alpha > 16)
    if len(xs) == 0:
        return {"width": 0, "height": 0}
    return {"width": int(xs.max() - xs.min() + 1), "height": int(ys.max() - ys.min() + 1)}


def _qa(source: Image.Image, cut: Image.Image, probes: dict[str, tuple[int, int]]) -> dict[str, Any]:
    """Backdrop left on the matte fails. Probe pixels (hair, moustache) must survive."""
    src = np.asarray(source.convert("RGBA"))
    out = np.asarray(cut.convert("RGBA"))
    corner = src[0, 0, :3].astype(np.int16)
    dist = np.abs(src[:, :, :3].astype(np.int16) - corner).sum(axis=2)
    backdrop = dist <= 12
    residue = int(np.count_nonzero(backdrop & (out[:, :, 3] > 32)))
    backdrop_n = int(np.count_nonzero(backdrop))
    residue_frac = residue / backdrop_n if backdrop_n else 1.0
    kept = {}
    for name, (x, y) in probes.items():
        kept[name] = int(out[y, x, 3])
    probes_ok = all(value > 200 for value in kept.values()) if kept else True
    passed = residue_frac < 0.02 and probes_ok
    return {
        "pass": passed,
        "residueFraction": round(residue_frac, 4),
        "probes": kept,
        "nativeSize": True,
    }
