"""Image model selection and per-model credit counter (structure only; no billing is charged here).

The default provider is Together Qwen-Image (best quality per dollar in the
real-key benchmark). Gemini Flash image remains available for the Aiwake rig
path. FLUX.1-schnell is not called: Together returns 400 unless a dedicated
endpoint is used. Keys are read from env by name only, never stored or logged.

Config (env):
  ANIMIND_IMAGE_MODEL      default model id (``together-qwen-image``)
  ANIMIND_IMAGE_MODELS     optional JSON overriding/adding entries: {"flux-pro": {"usdPerImage": 0.05, ...}}
  ANIMIND_FREE_CREDITS     free generations per user per month (credits; default 0 = off)
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx


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
    # Together images API. Qwen-Image is the default; FLUX.1-kontext-pro is the edit model.
    # FLUX.1-schnell is omitted: the shared images endpoint returns 400 for it.
    "together-qwen-image": ImageModel(
        "together-qwen-image", "together", "Qwen/Qwen-Image", 0.01, 1,
        "TOGETHER_API_KEY", True, "together", "image",
        "Together Qwen-Image list price",
    ),
    "together-flux-kontext": ImageModel(
        "together-flux-kontext", "together", "black-forest-labs/FLUX.1-kontext-pro", 0.04, 1,
        "TOGETHER_API_KEY", True, "together", "image",
        "Together FLUX.1-kontext-pro list price",
    ),
}

DEFAULT_IMAGE_MODEL = "together-qwen-image"


class PaidImageGenerationDisabled(RuntimeError):
    """Raised when a config-only provider is asked to spend money."""


class BudgetExceeded(RuntimeError):
    """Raised before a call that would pass the per-run USD cap."""


@dataclass
class ImageResult:
    path: Path
    provider: str
    model: str
    cost_usd: float
    cached: bool = False


@dataclass
class RunBudget:
    """Hard cap for one avatar build or one benchmark invocation."""

    cap_usd: float
    spent_usd: float = 0.0

    def remaining(self) -> float:
        return round(max(0.0, self.cap_usd - self.spent_usd), 8)

    def allow(self, estimate: float) -> bool:
        return float(estimate) <= self.remaining() + 1e-9

    def charge(self, amount: float) -> None:
        """Account for a call that already happened. The next call is refused by :meth:`allow`."""
        self.spent_usd = round(self.spent_usd + max(0.0, float(amount)), 8)


class ImageProvider(ABC):
    """Provider boundary. Gemini calls Aiwake; Together and OpenRouter are thin HTTP adapters."""

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


class GeminiImageProvider(ImageProvider):
    """Aiwake's fast Gemini Flash image call (``create_puppet.generate_character_image``)."""

    def generate(self, prompt: str, **options: Any) -> ImageResult:
        return _gemini_image(self.spec, prompt, **options)


class TogetherImageProvider(ImageProvider):
    """Together images. Qwen uses ``TOGETHER_QWEN_IMAGE_MODEL`` only."""

    def generate(self, prompt: str, **options: Any) -> ImageResult:
        return _together_image(self.spec, prompt, **options)


class OpenRouterImageProvider(ImageProvider):
    """OpenRouter image chat for Qwen-Image or FLUX Kontext."""

    def generate(self, prompt: str, **options: Any) -> ImageResult:
        return _openrouter_image(self.spec, prompt, **options)


class QwenImageProvider(ConfigOnlyImageProvider):
    """DashScope Qwen Image adapter boundary; the live Qwen path is OpenRouter."""


class FluxImageProvider(ConfigOnlyImageProvider):
    """fal.ai Flux adapter boundary; the live Flux path is Together or OpenRouter."""


def models() -> dict[str, ImageModel]:
    out = dict(DEFAULTS)
    raw = os.environ.get("ANIMIND_IMAGE_MODELS")
    if raw:
        for mid, cfg in json.loads(raw).items():
            base = asdict(out[mid]) if mid in out else {"id": mid, "provider": "custom", "model": mid, "usdPerImage": 0.0}
            out[mid] = ImageModel(**(base | cfg | {"id": mid}))
    return out


def _with_env_model(spec: ImageModel) -> ImageModel:
    """Model ids named by env, read at call time. Empty env keeps the registry default."""
    if spec.id == "together-qwen-image":
        # TOGETHER_IMAGE_MODEL is the old FLUX.1-schnell switch. It must not
        # replace Qwen: that id 400s on the shared images endpoint.
        override = (os.getenv("TOGETHER_QWEN_IMAGE_MODEL") or "").strip()
    elif spec.id == "together-flux-kontext":
        override = (os.getenv("TOGETHER_FLUX_KONTEXT_MODEL") or "").strip()
    else:
        override = ""
    if override and override != spec.model:
        return ImageModel(**(asdict(spec) | {"model": override}))
    return spec


def selected(model_id: str | None = None) -> ImageModel:
    mid = model_id or os.environ.get("ANIMIND_IMAGE_MODEL", DEFAULT_IMAGE_MODEL)
    table = models()
    if mid not in table:
        raise ValueError(f"unknown image model {mid!r}; known: {sorted(table)}")
    return _with_env_model(table[mid])


def provider(model_id: str | None = None) -> ImageProvider:
    spec = selected(model_id)
    adapter = {
        "gemini": GeminiImageProvider,
        "together": TogetherImageProvider,
        "openrouter": OpenRouterImageProvider,
        "qwen-image": QwenImageProvider,
        "flux": FluxImageProvider,
    }.get(spec.provider, ConfigOnlyImageProvider)
    return adapter(spec)


def run_budget(cap_usd: float | None = None) -> RunBudget:
    from . import settings  # noqa: PLC0415

    return RunBudget(settings.run_budget_usd() if cap_usd is None else max(0.0, float(cap_usd)))


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


def _destination(options: dict[str, Any]) -> Path:
    raw = options.get("destination")
    if raw is None:
        raise ValueError("destination is required")
    path = Path(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _budget_of(spec: ImageModel, options: dict[str, Any]) -> RunBudget:
    got = options.get("budget")
    if isinstance(got, RunBudget):
        return got
    return run_budget()


def _preflight(spec: ImageModel, budget: RunBudget) -> None:
    if not budget.allow(spec.usdPerImage):
        raise BudgetExceeded(
            f"{spec.id} estimate ${spec.usdPerImage:.4f} exceeds the remaining "
            f"${budget.remaining():.4f} of the ${budget.cap_usd:.2f} run cap"
        )


def _record_image(spec: ImageModel, *, cost: float, status: str, path: Path | None, latency_ms: float) -> None:
    from . import telemetry  # noqa: PLC0415

    telemetry.record(
        "image-call", provider=spec.provider, model=spec.model, images=1 if status == "ok" else 0,
        cost_usd=cost, latency_ms=latency_ms, status=status, artifact=path,
        metadata={"modelId": spec.id},
    )


def _decode_image(payload: str) -> bytes:
    text = payload.strip()
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    return base64.b64decode(text)


def _gemini_image(spec: ImageModel, prompt: str, **options: Any) -> ImageResult:
    key = (os.getenv(spec.keyEnv) or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    budget = _budget_of(spec, options)
    _preflight(spec, budget)
    destination = _destination(options)
    started = time.perf_counter()
    from production.cost.google_guardrail import get_google_cost_tracker  # noqa: PLC0415
    from products.aiwake.animator.factory.create_puppet import generate_character_image  # noqa: PLC0415

    previous = {
        name: os.environ.get(name)
        for name in ("MAX_GOOGLE_COST_PER_RUN_USD", "GOOGLE_COST_ALLOW_ABOVE_CEILING")
    }
    os.environ["GOOGLE_COST_ALLOW_ABOVE_CEILING"] = "1"
    os.environ["MAX_GOOGLE_COST_PER_RUN_USD"] = f"{max(budget.remaining(), spec.usdPerImage):.4f}"
    before = float(get_google_cost_tracker().total_usd())
    try:
        model = generate_character_image(
            prompt, destination,
            aspect_ratio=str(options.get("aspect_ratio") or "4:5"),
            reference_path=options.get("reference_path"),
        )
    except Exception:
        _record_image(spec, cost=0.0, status="failed", path=None, latency_ms=(time.perf_counter() - started) * 1000)
        raise
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    spent = max(0.0, float(get_google_cost_tracker().total_usd()) - before)
    cost = spent if spent > 0 else spec.usdPerImage
    budget.charge(cost)
    _record_image(spec, cost=cost, status="ok", path=destination, latency_ms=(time.perf_counter() - started) * 1000)
    return ImageResult(destination, spec.provider, model or spec.model, cost)


def _together_image(spec: ImageModel, prompt: str, **options: Any) -> ImageResult:
    key = (os.getenv("TOGETHER_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("TOGETHER_API_KEY is not configured")
    budget = _budget_of(spec, options)
    _preflight(spec, budget)
    destination = _destination(options)
    width, height = _pixel_size(str(options.get("aspect_ratio") or "4:5"))
    model = spec.model
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "width": width,
        "height": height,
        "n": 1,
        "response_format": "b64_json",
    }
    reference = options.get("reference_path")
    if reference:
        raw = Path(reference).read_bytes()
        body["image_url"] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        if "qwen" in model.lower() and "edit" not in model.lower():
            model = (os.getenv("TOGETHER_QWEN_EDIT_MODEL") or "Qwen/Qwen-Image-Edit").strip()
            body["model"] = model
    started = time.perf_counter()
    response = httpx.post(
        "https://api.together.xyz/v1/images/generations",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=120.0,
    )
    try:
        response.raise_for_status()
        payload = response.json()
        encoded = (payload.get("data") or [{}])[0].get("b64_json") or ""
        if not encoded:
            raise RuntimeError("Together response contained no image")
        destination.write_bytes(_decode_image(encoded))
    except Exception:
        _record_image(spec, cost=0.0, status="failed", path=None, latency_ms=(time.perf_counter() - started) * 1000)
        raise
    cost = spec.usdPerImage
    budget.charge(cost)
    _record_image(spec, cost=cost, status="ok", path=destination, latency_ms=(time.perf_counter() - started) * 1000)
    return ImageResult(destination, spec.provider, model, cost)


def _openrouter_image(spec: ImageModel, prompt: str, **options: Any) -> ImageResult:
    key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    budget = _budget_of(spec, options)
    _preflight(spec, budget)
    destination = _destination(options)
    started = time.perf_counter()
    response = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": spec.model,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
        },
        timeout=120.0,
    )
    try:
        response.raise_for_status()
        payload = response.json()
        encoded = _openrouter_image_payload(payload)
        destination.write_bytes(_decode_image(encoded))
    except Exception:
        _record_image(spec, cost=0.0, status="failed", path=None, latency_ms=(time.perf_counter() - started) * 1000)
        raise
    usage = payload.get("usage") or {}
    cost = float(usage.get("cost") or spec.usdPerImage)
    budget.charge(cost)
    _record_image(spec, cost=cost, status="ok", path=destination, latency_ms=(time.perf_counter() - started) * 1000)
    return ImageResult(destination, spec.provider, spec.model, cost)


def _openrouter_image_payload(payload: dict[str, Any]) -> str:
    message = ((payload.get("choices") or [{}])[0].get("message") or {})
    images = message.get("images") or []
    if images:
        image = images[0]
        if isinstance(image, str):
            return image
        url = (image.get("image_url") or {}).get("url") or image.get("url") or ""
        if url:
            return url
    for item in payload.get("data") or []:
        if item.get("b64_json"):
            return str(item["b64_json"])
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            url = (part.get("image_url") or {}).get("url") or ""
            if url:
                return url
    raise RuntimeError("OpenRouter response contained no image")


def _pixel_size(aspect_ratio: str) -> tuple[int, int]:
    return {"9:16": (768, 1344), "1:1": (1024, 1024), "16:9": (1344, 768)}.get(aspect_ratio, (768, 960))


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
