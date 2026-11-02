"""Image model selection and per-model credit counter (structure only; no billing is charged here).

The default provider is Gemini "nano-banana" (``gemini-2.5-flash-image``), the one the rig pipeline calls.
Other providers (Flux, ...) plug in through :data:`DEFAULTS`; they are listed but disabled until a key
env var named in the config is set. Keys are read from env by name only, never stored or logged.

Config (env):
  ANIMIND_IMAGE_MODEL      default model id (``nano-banana``)
  ANIMIND_IMAGE_MODELS     optional JSON overriding/adding entries: {"flux-pro": {"usdPerImage": 0.05, ...}}
  ANIMIND_FREE_CREDITS     free generations per user per month (credits; default 0 = off)
"""

from __future__ import annotations

import json
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ImageModel:
    id: str
    provider: str  # "gemini" | "flux" | ...
    model: str  # provider-side model name
    usdPerImage: float
    credits: int = 1  # credits charged per image
    keyEnv: str = ""  # env var holding the provider key (name only)
    implemented: bool = False
    gateway: str = ""
    billingUnit: str = "image"
    priceSource: str = ""
    priceDate: str = "2026-11-02"

    def available(self) -> bool:
        return self.implemented and bool(self.keyEnv and os.environ.get(self.keyEnv))


DEFAULTS: dict[str, ImageModel] = {
    "nano-banana": ImageModel(
        "nano-banana", "gemini", "gemini-2.5-flash-image", 0.039, 1,
        "GEMINI_API_KEY", True, "google", "image",
        "https://ai.google.dev/gemini-api/docs/pricing",
    ),
    "qwen-image": ImageModel(
        "qwen-image", "qwen-image", "qwen-image-3.0", 0.03, 1,
        "DASHSCOPE_API_KEY", False, "dashscope", "image",
        "https://www.alibabacloud.com/help/en/model-studio/model-pricing",
    ),
    "flux-pro": ImageModel(
        "flux-pro", "flux", "fal-ai/flux-pro/v1.1", 0.04, 1,
        "FAL_KEY", False, "fal.ai", "megapixel",
        "https://fal.ai/models/fal-ai/flux-pro/v1.1",
    ),
    "flux-kontext": ImageModel(
        "flux-kontext", "flux", "fal-ai/flux-2/edit", 0.012, 1,
        "FAL_KEY", False, "fal.ai", "processed megapixel",
        "https://fal.ai/pricing",
    ),
}


class PaidImageGenerationDisabled(RuntimeError):
    """Raised when a config-only provider is asked to spend money."""


class ImageProvider(ABC):
    """Provider boundary. Phase 4 exposes selection without making paid calls."""

    def __init__(self, spec: ImageModel) -> None:
        self.spec = spec

    def configured(self) -> bool:
        return bool(self.spec.keyEnv and os.environ.get(self.spec.keyEnv))

    @abstractmethod
    def generate(self, prompt: str, **options: Any) -> Any:
        raise NotImplementedError


class ConfigOnlyImageProvider(ImageProvider):
    def generate(self, prompt: str, **options: Any) -> Any:
        raise PaidImageGenerationDisabled(
            f"{self.spec.provider} is registered but paid generation is disabled in phase 4"
        )


class GeminiImageProvider(ConfigOnlyImageProvider):
    """Selection adapter for the existing Gemini rig pipeline."""


class QwenImageProvider(ConfigOnlyImageProvider):
    """DashScope Qwen Image adapter boundary; no paid request in phase 4."""


class FluxImageProvider(ConfigOnlyImageProvider):
    """fal.ai Flux adapter boundary; no paid request in phase 4."""


def models() -> dict[str, ImageModel]:
    out = dict(DEFAULTS)
    raw = os.environ.get("ANIMIND_IMAGE_MODELS")
    if raw:
        for mid, cfg in json.loads(raw).items():
            base = asdict(out[mid]) if mid in out else {"id": mid, "provider": "custom", "model": mid, "usdPerImage": 0.0}
            out[mid] = ImageModel(**(base | cfg | {"id": mid}))
    return out


def selected(model_id: str | None = None) -> ImageModel:
    mid = model_id or os.environ.get("ANIMIND_IMAGE_MODEL", "nano-banana")
    table = models()
    if mid not in table:
        raise ValueError(f"unknown image model {mid!r}; known: {sorted(table)}")
    return table[mid]


def provider(model_id: str | None = None) -> ImageProvider:
    spec = selected(model_id)
    adapter = {
        "gemini": GeminiImageProvider,
        "qwen-image": QwenImageProvider,
        "flux": FluxImageProvider,
    }.get(spec.provider, ConfigOnlyImageProvider)
    return adapter(spec)


def public_list() -> list[dict[str, Any]]:
    """For the UI picker: no key names, only whether the model can be used now."""
    return [
        {
            "id": m.id,
            "provider": m.provider,
            "gateway": m.gateway,
            "usdPerImage": m.usdPerImage,
            "billingUnit": m.billingUnit,
            "credits": m.credits,
            "available": m.available(),
        }
        for m in models().values()
    ]


@dataclass
class CreditLedger:
    """Counts images and credits per model per user per month in a JSON file (no charging)."""
    path: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def record(self, user: str, model_id: str, images: int = 1, month: str | None = None) -> dict[str, Any]:
        m = selected(model_id)
        month = month or time.strftime("%Y-%m")
        with self._lock:
            data = self._load()
            row = data.setdefault(month, {}).setdefault(user, {}).setdefault(m.id, {"images": 0, "credits": 0, "usd": 0.0})
            row["images"] += images
            row["credits"] += images * m.credits
            row["usd"] = round(row["usd"] + images * m.usdPerImage, 4)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=1))
            return dict(row)

    def used_credits(self, user: str, month: str | None = None) -> int:
        month = month or time.strftime("%Y-%m")
        return sum(r["credits"] for r in self._load().get(month, {}).get(user, {}).values())

    def free_left(self, user: str, month: str | None = None) -> int:
        free = int(os.environ.get("ANIMIND_FREE_CREDITS", "0"))
        return max(0, free - self.used_credits(user, month))
