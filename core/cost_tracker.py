# -*- coding: utf-8 -*-
"""
CostTracker — per-run financial tracking for the Unified Multi-Page Factory.

Records an estimated USD cost for every generation operation (image, text, audio)
and writes a structured telemetry JSON to outputs/{page_id}/library/.
The ``estimated_cost`` field is also merged into each durable post JSON via
``CostTracker.annotate_payload()``.

Pricing constants
-----------------
All costs are approximations in USD; update ``_PRICE`` when provider pricing changes.

Image generation uses Together AI ``FLUX.1-schnell`` at **$0.003 / image**
(Gemini image pricing is deprecated / aliased to the same rate).

Usage
-----
    from core.cost_tracker import CostTracker

    tracker = CostTracker(page_id="ancient_knowledge", cost_tier="nano")

    tracker.track_image("image_nano")
    tracker.track_text("text_gemini_flash", token_count=4000)
    tracker.track_audio(char_count=1200, sfx=False)
    tracker.track_sfx(calls=1)
    tracker.track_music(api=False)

    payload["estimated_cost"] = tracker.total_usd()
    tracker.write_telemetry(outputs_dir / "library", variant_index=1)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

_VALID_TIERS = frozenset({"nano", "economic", "premium"})


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce *value* to float; never raise. None / invalid → *default*."""
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN
        return default
    return out


def _safe_int(value: Any, default: int = 0) -> int:
    """Coerce *value* to int; never raise. None / invalid → *default*."""
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _normalize_tier(value: Any) -> Literal["nano", "economic", "premium"]:
    raw = str(value or "economic").lower().strip()
    if raw in _VALID_TIERS:
        return raw  # type: ignore[return-value]
    logger.warning("CostTracker | unknown cost_tier=%r — falling back to 'economic'", value)
    return "economic"

# Official per-unit formulas (USD)
FLUX_SCHNELL_USD_PER_IMAGE: float = 0.003
ELEVENLABS_USD_PER_1K_CHARS: float = 0.030          # → $0.00003 / char


def elevenlabs_on_plan() -> bool:
    """ElevenLabs runs on a flat subscription (Pro) unless told otherwise.

    Plan usage costs $0 at the margin, like ``spend_ledger.record_informational``:
    the list price is kept on the entry for scale but stays out of the total.
    ``ELEVENLABS_BILLING=metered`` restores list-price billing.
    """
    raw = (os.getenv("ELEVENLABS_BILLING") or "subscription").strip().lower()
    return raw not in {"metered", "payg", "pay_as_you_go", "usage", "list"}
GEMINI_FLASH_USD_PER_1M_TOKENS: float = 0.075       # input+output tokens
ELEVENLABS_SFX_USD_PER_CALL: float = 0.002          # Sound Effects API
ELEVENLABS_MUSIC_USD_PER_CALL: float = 0.030        # Music compose API
LOCAL_MUSIC_USD_PER_BED: float = 0.0                # on-disk / failsafe bed

# ---------------------------------------------------------------------------
# Pricing table (USD per call/unit)
# ---------------------------------------------------------------------------
_PRICE: dict[str, float] = {
    # Image generation — Together AI FLUX.1-schnell = $0.003 / image
    "image_flux_schnell":  FLUX_SCHNELL_USD_PER_IMAGE,
    "image_flux_dev":      0.025_0,
    "image_flux_pro":      0.050_0,
    "image_together_juggernaut": 0.001_7,
    "image_together_flux2dev":   0.015_4,
    "image_deepinfra_flux2dev":  0.008_8,
    "image_sdxl":          0.008_0,
    "image_nano":          FLUX_SCHNELL_USD_PER_IMAGE,
    # Gemini image output, standard paid tier, October 2026 docs.
    # Nano Banana (gemini-2.5-flash-image) is $0.039. The 2.1 successor
    # at 1K is $0.0336. Flux stays $0.003.
    "image_nanobanana":    0.039,
    "image_nanobanana_lite": 0.0336,
    # A real free-licence photograph. $0 is intentional, not a missing price.
    "image_commons":       0.0,
    # A copy of a still this reel already paid for. Not a Commons download.
    "image_reuse":         0.0,
    "image_economic":      FLUX_SCHNELL_USD_PER_IMAGE,
    "image_premium":       FLUX_SCHNELL_USD_PER_IMAGE,
    "image_gemini_flash":  FLUX_SCHNELL_USD_PER_IMAGE,
    "image_gemini_pro":    FLUX_SCHNELL_USD_PER_IMAGE,

    # Text / LLM — Gemini 2.5 Flash billed per token (not a flat per-call fee)
    "text_gemini_flash":   GEMINI_FLASH_USD_PER_1M_TOKENS,  # $ / 1M tokens
    "text_gemini_pro":     0.075_0,
    "text_deepseek":       0.000_2,
    "text_claude_haiku":   0.001_0,
    "text_claude_sonnet":  0.005_0,

    # Audio — ElevenLabs
    "tts_per_char":        ELEVENLABS_USD_PER_1K_CHARS / 1000.0,  # $0.00003
    "sfx_per_call":        ELEVENLABS_SFX_USD_PER_CALL,
    "music_api_per_call":  ELEVENLABS_MUSIC_USD_PER_CALL,
    "music_local_per_bed": LOCAL_MUSIC_USD_PER_BED,

    # Remote GPU — RunPod RTX 4090 (override via env — see track_gpu_seconds)
    # Pod on-demand (public pricing page): Community $0.34/hr, Secure $0.69/hr
    "gpu_pod_rtx4090_community_per_sec": 0.34 / 3600.0,   # ≈ $0.0000944/s
    "gpu_pod_rtx4090_secure_per_sec":    0.69 / 3600.0,   # ≈ $0.0001917/s
    # Serverless: official endpoint-config table lists "4090 PRO" at $0.00031/s
    # (docs.runpod.io/serverless/endpoints/endpoint-configurations). Older blog
    # posts cited $0.00044 flex / $0.00026 active — do NOT prefer those when they
    # disagree with the current docs table. Active workers: apply −40% when the
    # account still uses that discount; confirm in RunPod console when keyed.
    "gpu_serverless_rtx4090_flex_per_sec":   0.000_31,
    "gpu_serverless_rtx4090_active_per_sec": 0.000_186,  # 0.00031 * 0.60
}

# Map cost_tier → default image model key (all tiers → FLUX Schnell)
_TIER_IMAGE_KEY: dict[str, str] = {
    "nano":     "image_flux_schnell",
    "economic": "image_flux_schnell",
    "premium":  "image_flux_schnell",
}

# Map cost_tier → default text model key
_TIER_TEXT_KEY: dict[str, str] = {
    "nano":     "text_gemini_flash",   # Gemini primary even on nano pages
    "economic": "text_gemini_flash",
    "premium":  "text_claude_sonnet",
}



def _ledger_llm_summary() -> dict | None:
    """Real LLM and vision spend of this run, by provider, from spend_ledger."""
    try:
        from core.spend_ledger import run_records  # noqa: PLC0415
        records = [
            r for r in run_records()
            if str(r.kind or "") in {"text", "vision", "llm"}
        ]
    except Exception:  # noqa: BLE001
        return None
    if not records:
        return None
    by_provider: dict[str, float] = {}
    text_usd: dict[str, float] = {}
    for r in records:
        provider = str(r.provider or "unknown").strip().lower()
        by_provider[provider] = by_provider.get(provider, 0.0) + float(r.cost_usd or 0.0)
        if str(r.kind or "") != "vision":
            label = f"{provider}:{r.model or ''}"
            text_usd[label] = text_usd.get(label, 0.0) + float(r.cost_usd or 0.0)
    text_label = max(text_usd, key=text_usd.get) if text_usd else ""
    return {
        "usd": round(sum(by_provider.values()), 6),
        "calls": len(records),
        "by_provider": {k: round(v, 6) for k, v in sorted(by_provider.items())},
        "text_model_key": f"text_{text_label.split(':', 1)[0]}" if text_label else "",
        "top_text_model": text_label,
    }


@dataclass
class CostTracker:
    """
    Thread-safe accumulator of generation cost estimates.

    Parameters
    ----------
    page_id:
        The active page slug (e.g. ``ancient_knowledge``).
    cost_tier:
        ``"nano"`` | ``"economic"`` | ``"premium"``.
        Controls which default pricing keys are used when callers omit
        the explicit ``model_key`` argument.
    """

    page_id: str
    cost_tier: Literal["nano", "economic", "premium"] = "economic"

    _entries: list[dict] = field(default_factory=list, repr=False, init=False)
    _total_usd: float = field(default=0.0, repr=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, init=False)

    def __post_init__(self) -> None:
        self.page_id = str(self.page_id or "unknown").strip() or "unknown"
        object.__setattr__(self, "cost_tier", _normalize_tier(self.cost_tier))
        try:
            from core.spend_ledger import run_records  # noqa: PLC0415

            object.__setattr__(self, "_ledger_seen", len(run_records()))
        except Exception:  # noqa: BLE001
            object.__setattr__(self, "_ledger_seen", 0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add(
        self,
        operation: str,
        model_key: str,
        units: float,
        cost: float,
        *,
        plan_list_usd: float | None = None,
        detail: dict | None = None,
    ) -> float:
        """Add a line. ``plan_list_usd`` marks flat-plan usage: $0, informational."""
        units_f = max(0.0, _safe_float(units, 0.0))
        cost_f = max(0.0, _safe_float(cost, 0.0))
        entry = {
            "operation": str(operation or "unknown"),
            "model_key": str(model_key or "unknown"),
            "units": units_f,
            "cost_usd": round(cost_f, 8),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if plan_list_usd is not None:
            cost_f = 0.0
            entry["cost_usd"] = 0.0
            entry["billing"] = "subscription"
            entry["informational"] = True
            entry["list_usd"] = round(max(0.0, _safe_float(plan_list_usd, 0.0)), 8)
        if detail:
            entry.update(detail)
        with self._lock:
            self._entries.append(entry)
            self._total_usd = _safe_float(self._total_usd, 0.0) + cost_f
        logger.debug(
            "CostTracker | page=%s op=%s model=%s cost=$%.6f  total=$%.6f",
            self.page_id, operation, model_key, cost_f, self._total_usd,
        )
        return cost_f

    # ------------------------------------------------------------------
    # Public tracking methods
    # ------------------------------------------------------------------

    def track_image(
        self,
        model_key: str | None = None,
        count: int = 1,
    ) -> float:
        """
        Record the estimated cost for ``count`` image generation calls.

        Parameters
        ----------
        model_key:
            One of the keys in ``_PRICE`` (e.g. ``"image_nano"``).
            Defaults to the tier-appropriate key.
        count:
            Number of images generated (for multi-image sequence reels).
        """
        try:
            n = max(1, _safe_int(count, 1))
            key = model_key or _TIER_IMAGE_KEY.get(self.cost_tier, "image_flux_schnell")
            if key not in _PRICE:
                key = "image_flux_schnell"
            price_per = _safe_float(_PRICE[key], 0.0)
            total = price_per * n
            return self._add("image_generation", key, float(n), total)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CostTracker.track_image failed (%s) — recording $0 fallback", exc)
            return 0.0

    def _served_text_calls(self) -> list:
        """Text calls the spend ledger saw since this tracker last looked.

        The ledger knows which provider actually answered (DeepSeek, Gemini,
        ...) and what it cost, so the script line carries the real label.
        """
        try:
            from core.spend_ledger import run_records  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return []
        records = run_records()
        seen = int(getattr(self, "_ledger_seen", 0) or 0)
        if seen > len(records):
            seen = 0
        object.__setattr__(self, "_ledger_seen", len(records))
        return [r for r in records[seen:] if str(r.kind or "") == "text"]

    def track_text(
        self,
        model_key: str | None = None,
        char_count: int = 0,
        token_count: int | None = None,
    ) -> float:
        """
        Record LLM cost. Gemini 2.5 Flash = ``$0.075 / 1M tokens``.

        ``token_count`` wins when provided; otherwise tokens ≈ ``char_count / 4``.
        """
        try:
            chars = max(0, _safe_int(char_count, 0))
            tokens = _safe_int(token_count, 0) if token_count is not None else max(0, chars // 4)
            if tokens <= 0 and chars > 0:
                tokens = max(1, chars // 4)
            if model_key is None:
                served = self._served_text_calls()
                if served:
                    last = served[-1]
                    provider = str(last.provider or "unknown").strip().lower()
                    return self._add(
                        "text_generation",
                        f"text_{provider}",
                        float(sum(r.input_tokens + r.output_tokens for r in served) or tokens),
                        sum(float(r.cost_usd or 0.0) for r in served),
                        detail={
                            "provider": provider,
                            "model": str(last.model or ""),
                            "calls": len(served),
                            "source": "spend_ledger",
                        },
                    )
            key = model_key or _TIER_TEXT_KEY.get(self.cost_tier, "text_gemini_flash")
            if str(key).startswith("text_gemini"):
                price_per_m = _safe_float(
                    _PRICE.get(key, GEMINI_FLASH_USD_PER_1M_TOKENS),
                    GEMINI_FLASH_USD_PER_1M_TOKENS,
                )
                cost = tokens * price_per_m / 1_000_000.0
            else:
                # Non-Gemini keys remain small per-call estimates
                cost = _safe_float(_PRICE.get(key, 0.0), 0.0)
            return self._add("text_generation", key, float(tokens), cost)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CostTracker.track_text failed (%s) — recording $0 fallback", exc)
            return 0.0

    def track_audio(
        self,
        char_count: int = 0,
        sfx: bool = False,
        model_key: str = "tts_per_char",
    ) -> float:
        """Record ElevenLabs TTS at ``$0.030 / 1,000 characters``. Optional SFX call."""
        try:
            chars = max(0, _safe_int(char_count, 0))
            tts_cost = chars * (ELEVENLABS_USD_PER_1K_CHARS / 1000.0)
            total = tts_cost
            if sfx:
                total += self.track_sfx(calls=1)
            if chars > 0:
                if elevenlabs_on_plan():
                    self._add("audio_generation", "elevenlabs_tts", float(chars), 0.0,
                              plan_list_usd=tts_cost)
                    return total - tts_cost
                self._add("audio_generation", "elevenlabs_tts", float(chars), tts_cost)
            return total
        except Exception as exc:  # noqa: BLE001
            logger.warning("CostTracker.track_audio failed (%s) — recording $0 fallback", exc)
            return 0.0

    def track_sfx(self, calls: int = 1) -> float:
        """ElevenLabs Sound Effects API — one billed generation."""
        try:
            n = max(1, _safe_int(calls, 1))
            cost = ELEVENLABS_SFX_USD_PER_CALL * n
            if elevenlabs_on_plan():
                return self._add("sfx_generation", "elevenlabs_sfx", float(n), 0.0,
                                 plan_list_usd=cost)
            return self._add("sfx_generation", "elevenlabs_sfx", float(n), cost)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CostTracker.track_sfx failed (%s)", exc)
            return 0.0

    def track_music(self, *, api: bool = False, beds: int = 1) -> float:
        """Music bed: ElevenLabs Music compose, or $0.0000 for a local/cached file."""
        try:
            n = max(1, _safe_int(beds, 1))
            if api:
                # ElevenLabs Music compose: same account and plan as the voice.
                cost = ELEVENLABS_MUSIC_USD_PER_CALL * n
                key = "music_api_per_call"
                if elevenlabs_on_plan():
                    return self._add("music_generation", key, float(n), 0.0,
                                     plan_list_usd=cost)
            else:
                cost = LOCAL_MUSIC_USD_PER_BED * n
                key = "music_local_per_bed"
            return self._add("music_generation", key, float(n), cost)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CostTracker.track_music failed (%s)", exc)
            return 0.0

    def track_gpu_seconds(
        self,
        seconds: float,
        *,
        mode: str | None = None,
        jobs: int = 1,
        model_key: str | None = None,
    ) -> float:
        """
        Record estimated RunPod GPU cost for *seconds* of billed GPU time.

        Parameters
        ----------
        seconds:
            Wall-clock GPU time to bill. For **pod** mode this is typically
            dedicated uptime attributed to the run; for **serverless** it is
            the sum of per-job active GPU seconds (workers bill in parallel).
        mode:
            ``\"comfyui\"`` / ``\"pod\"`` → pod hourly rate.
            ``\"runpod\"`` / ``\"serverless\"`` → serverless per-second rate.
            Default: ``REMOTE_GPU_MODE`` from config/env.
        jobs:
            Informational unit count (e.g. number of image jobs).
        """
        import os

        secs = max(0.0, _safe_float(seconds, 0.0))
        if secs <= 0:
            return 0.0
        try:
            return self._track_gpu_seconds_inner(secs, mode=mode, jobs=jobs, model_key=model_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CostTracker.track_gpu_seconds failed (%s) — recording $0 fallback", exc)
            return 0.0

    def _track_gpu_seconds_inner(
        self,
        secs: float,
        *,
        mode: str | None,
        jobs: int,
        model_key: str | None,
    ) -> float:
        import os

        mode_raw = (
            mode
            or os.getenv("REMOTE_GPU_MODE")
            or "comfyui"
        ).strip().lower()
        is_serverless = mode_raw in ("runpod", "serverless")

        # Optional explicit $/unit overrides from env/config
        try:
            import config as app_config

            pod_override = float(getattr(app_config, "RUNPOD_POD_RTX4090_USD_PER_HOUR", 0) or 0)
            srv_override = float(
                getattr(app_config, "RUNPOD_SERVERLESS_RTX4090_USD_PER_SEC", 0) or 0
            )
            cloud = str(getattr(app_config, "RUNPOD_CLOUD_TYPE", "community") or "community").lower()
            worker_t = str(
                getattr(app_config, "RUNPOD_SERVERLESS_WORKER_TYPE", "flex") or "flex"
            ).lower()
        except Exception:
            pod_override = float(os.getenv("RUNPOD_POD_RTX4090_USD_PER_HOUR") or 0)
            srv_override = float(os.getenv("RUNPOD_SERVERLESS_RTX4090_USD_PER_SEC") or 0)
            cloud = (os.getenv("RUNPOD_CLOUD_TYPE") or "community").strip().lower()
            worker_t = (os.getenv("RUNPOD_SERVERLESS_WORKER_TYPE") or "flex").strip().lower()

        if model_key:
            key = model_key
            rate = _safe_float(_PRICE.get(key), 0.0)
            if rate <= 0:
                logger.warning(
                    "CostTracker | unknown GPU model_key=%r — using community pod rate",
                    key,
                )
                key = "gpu_pod_rtx4090_community_per_sec"
                rate = _safe_float(_PRICE.get(key), 0.0)
        elif is_serverless:
            if srv_override > 0:
                key = "gpu_serverless_rtx4090_custom_per_sec"
                rate = srv_override
            elif worker_t in ("active", "always_on", "min_workers"):
                key = "gpu_serverless_rtx4090_active_per_sec"
                rate = _PRICE[key]
            else:
                key = "gpu_serverless_rtx4090_flex_per_sec"
                rate = _PRICE[key]
        else:
            if pod_override > 0:
                key = "gpu_pod_rtx4090_custom_per_sec"
                rate = pod_override / 3600.0
            elif cloud in ("secure", "secure_cloud"):
                key = "gpu_pod_rtx4090_secure_per_sec"
                rate = _PRICE[key]
            else:
                key = "gpu_pod_rtx4090_community_per_sec"
                rate = _PRICE[key]

        rate = max(0.0, _safe_float(rate, 0.0))
        total = rate * secs
        return self._add(
            "gpu_compute",
            key,
            secs,
            total,
        )

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def total_usd(self) -> float:
        """Return the running total estimated cost in USD."""
        with self._lock:
            return self._total_usd

    def merge(self, other: "CostTracker") -> None:
        """Append another tracker's line items into this batch accumulator."""
        if other is None or other is self:
            return
        with other._lock:
            entries = list(other._entries)
        for e in entries:
            extra = {
                k: v for k, v in e.items()
                if k not in {"operation", "model_key", "units", "cost_usd", "ts",
                             "billing", "informational", "list_usd"}
            }
            self._add(
                str(e.get("operation") or "unknown"),
                str(e.get("model_key") or "unknown"),
                _safe_float(e.get("units"), 0.0),
                _safe_float(e.get("cost_usd"), 0.0),
                plan_list_usd=(
                    _safe_float(e.get("list_usd"), 0.0) if e.get("informational") else None
                ),
                detail=extra or None,
            )

    def category_totals(self, entries: list[dict] | None = None) -> dict[str, Any]:
        """Roll up line items into research / image / voice+sfx+music buckets."""
        src = entries
        if src is None:
            with self._lock:
                src = list(self._entries)
        research_cost = research_tokens = 0.0
        image_cost = image_count = 0.0
        flux_count = flux_cost = 0.0
        nano_count = nano_cost = 0.0
        real_count = 0.0
        reuse_count = 0.0
        tts_cost = tts_chars = 0.0
        sfx_cost = sfx_calls = 0.0
        music_cost = music_beds = 0.0
        music_api = False
        info_usd = 0.0
        info_audio = False
        text_label = ""
        for e in src or []:
            if not isinstance(e, dict):
                continue
            if e.get("informational"):
                info_usd += max(0.0, _safe_float(e.get("list_usd"), 0.0))
                if str(e.get("operation") or "") in {
                    "audio_generation", "sfx_generation", "music_generation",
                }:
                    info_audio = True
            if str(e.get("operation") or "") == "text_generation" and e.get("provider"):
                text_label = f"{e.get('provider')}:{e.get('model') or ''}".rstrip(":")
            op = str(e.get("operation") or "")
            cost = max(0.0, _safe_float(e.get("cost_usd"), 0.0))
            units = max(0.0, _safe_float(e.get("units"), 0.0))
            mk = str(e.get("model_key") or "")
            if op == "text_generation":
                research_cost += cost
                research_tokens += units
            elif op == "image_generation":
                image_cost += cost
                image_count += units
                if mk in {"image_nanobanana", "image_nanobanana_lite"}:
                    nano_count += units
                    nano_cost += cost
                elif mk == "image_commons":
                    real_count += units
                elif mk == "image_reuse":
                    reuse_count += units
                else:
                    flux_count += units
                    flux_cost += cost
            elif op == "audio_generation":
                tts_cost += cost
                tts_chars += units
            elif op == "sfx_generation":
                sfx_cost += cost
                sfx_calls += units
            elif op == "music_generation":
                music_cost += cost
                music_beds += units
                if "api" in mk:
                    music_api = True
        if flux_count > 0 and flux_cost <= 0 and nano_cost <= 0:
            invented = flux_count * FLUX_SCHNELL_USD_PER_IMAGE
            image_cost += invented
            flux_cost += invented
        if tts_chars > 0 and tts_cost <= 0 and not info_audio:
            tts_cost = tts_chars * (ELEVENLABS_USD_PER_1K_CHARS / 1000.0)
        if research_tokens > 0 and research_cost <= 0:
            research_cost = research_tokens * GEMINI_FLASH_USD_PER_1M_TOKENS / 1_000_000.0
        audio_cost = tts_cost + sfx_cost + music_cost
        total = research_cost + image_cost + audio_cost
        return {
            "research_cost": research_cost,
            "research_tokens": int(research_tokens),
            "image_cost": image_cost,
            "image_count": int(image_count),
            "image_flux_count": int(flux_count),
            "image_flux_cost": flux_cost,
            "image_nano_count": int(nano_count),
            "image_nano_cost": nano_cost,
            "image_real_count": int(real_count),
            "image_real_cost": 0.0,
            "image_reuse_count": int(reuse_count),
            "image_reuse_cost": 0.0,
            "tts_cost": tts_cost,
            "tts_chars": int(tts_chars),
            "sfx_cost": sfx_cost,
            "sfx_calls": int(sfx_calls),
            "music_cost": music_cost,
            "music_beds": int(music_beds),
            "music_api": music_api,
            "audio_cost": audio_cost,
            "audio_on_plan": info_audio,
            "informational_usd": info_usd,
            "text_label": text_label,
            "total": total,
        }

    def pipeline_usd(self) -> float:
        """Official pipeline total: Gemini + FLUX images + ElevenLabs VO/SFX/music (no GPU)."""
        return float(self.category_totals().get("total") or 0.0)

    def to_dict(self) -> dict:
        """
        Return a serialisable snapshot of the cost run.

        Compatible with ``write_atomic_json`` / ``merge_update_json``
        from ``agents.media.durable_library``.
        """
        with self._lock:
            entries = list(self._entries)
            ledger = _safe_float(self._total_usd, 0.0)
        cats = self.category_totals(entries)
        marginal = float(cats.get("total") or 0.0)
        llm = _ledger_llm_summary()
        if llm is not None:
            # The spend ledger saw every LLM and vision call with its real
            # price and provider (s23d: the per-script estimate said
            # text_gemini_flash $0.0003; DeepSeek and Gemma cost $0.0035).
            estimated_text = sum(
                _safe_float(e.get("cost_usd"), 0.0) for e in entries
                if e.get("operation") == "text_generation"
            )
            marginal = marginal - estimated_text + llm["usd"]
            entries = [e for e in entries if e.get("operation") != "text_generation"]
            entries.insert(0, {
                "operation": "text_generation",
                "model_key": llm["text_model_key"] or "text_unknown",
                "units": float(llm["calls"]),
                "cost_usd": llm["usd"],
                "detail": {
                    "source": "spend_ledger",
                    "by_provider": llm["by_provider"],
                    "top_text_model": llm["top_text_model"],
                    "replaces_estimate_usd": round(estimated_text, 6),
                },
            })
        return {
            "page_id": self.page_id,
            "cost_tier": self.cost_tier,
            "total_estimated_usd": round(marginal, 6),
            "ledger_usd": round(ledger, 6),
            "marginal_usd": round(marginal, 6),
            "llm_ledger": llm,
            "informational_usd": round(float(cats.get("informational_usd") or 0.0), 6),
            "informational_note": (
                "flat subscription usage at list price; $0 marginal; not in the reel cap"
            ),
            "breakdown": entries,
            "tracked_at": datetime.now(timezone.utc).isoformat(),
        }

    def annotate_payload(self, payload: dict) -> dict:
        """
        Inject ``estimated_cost`` and ``cost_tier`` into a durable post payload
        *in-place* and return the same dict.

        This is the canonical hook called just before ``write_atomic_json``
        so every persisted post JSON carries cost telemetry automatically.
        """
        if not isinstance(payload, dict):
            payload = {}
        payload["estimated_cost"] = round(_safe_float(self.pipeline_usd(), 0.0), 6)
        payload["cost_tier"] = self.cost_tier
        return payload

    def write_telemetry(
        self,
        library_dir: Path,
        variant_index: int = 1,
    ) -> Path:
        """
        Write a standalone cost telemetry JSON to ``library_dir``.

        File name: ``cost_{page_id}_{stamp}_v{variant:02d}.json``

        Returns the absolute path of the written file.
        """
        try:
            dest = Path(library_dir).expanduser().resolve()
        except Exception as exc:  # noqa: BLE001
            logger.warning("CostTracker | invalid library_dir=%r (%s)", library_dir, exc)
            from utils.pipeline_paths import outputs_root

            dest = outputs_root() / "cost_telemetry"
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("CostTracker | telemetry mkdir failed (%s)", exc)
            return dest / "cost_unwritten.json"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        vix = max(1, _safe_int(variant_index, 1))
        fname = f"cost_{self.page_id}_{stamp}_v{vix:02d}.json"
        out = dest / fname
        try:
            out.write_text(
                json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info(
                "CostTracker | telemetry written: %s  total=$%.6f",
                out.name, self.total_usd(),
            )
        except OSError as exc:
            logger.warning("CostTracker | failed to write telemetry: %s", exc)
        return out


_STATIC_IMAGE_POST_TYPES = frozenset({
    "LONG_CAPTION_IMAGE",
    "CTA_CAPTION_IMAGE",
    "SMART_BAIT",
    "STANDARD_QUOTE",
    "CAROUSEL",
})
_REEL_POST_TYPES = frozenset({
    "ECONOMIC_REEL",
    "ECONOMIC_REEL_LOFI",
    "WAN_REEL",
    "REFERENCE_BASED_REELS",
    "DYNAMIC_REEL",
})


def is_static_image_post(post_type: str | None) -> bool:
    return (post_type or "").upper().strip() in _STATIC_IMAGE_POST_TYPES


def slot_unit_label(post_type: str | None) -> tuple[str, str]:
    """Return ``(unit, suffix)`` — e.g. ``('Post', ' (Static Image)')``."""
    if is_static_image_post(post_type):
        return "Post", " (Static Image)"
    return "Reel", ""


def image_cost_lines(cats: dict | None) -> list[str]:
    """One line per image provider. Reuse is not labelled as Commons or Flux."""
    row = cats or {}
    nano_n = int(row.get("image_nano_count") or 0)
    flux_n = int(row.get("image_flux_count") or 0)
    real_n = int(row.get("image_real_count") or 0)
    reuse_n = int(row.get("image_reuse_count") or 0)
    if nano_n <= 0 and flux_n <= 0 and real_n <= 0 and reuse_n <= 0:
        return []
    lines: list[str] = []
    if nano_n:
        lines.append(
            "  - Image Gen (Nano Banana):".ljust(42)
            + f"${float(row.get('image_nano_cost') or 0):.4f}  "
            f"({nano_n} generation{'s' if nano_n != 1 else ''})"
        )
    if flux_n:
        lines.append(
            "  - Image Gen (FLUX Schnell):".ljust(42)
            + f"${float(row.get('image_flux_cost') or 0):.4f}  "
            f"({flux_n} attempt{'s' if flux_n != 1 else ''})"
        )
    if real_n:
        lines.append(
            "  - Image Gen (Commons/real):".ljust(42)
            + f"$0.0000  ({real_n} still{'s' if real_n != 1 else ''})"
        )
    if reuse_n:
        lines.append(
            "  - Image Gen (reuse):".ljust(42)
            + f"$0.0000  ({reuse_n} still{'s' if reuse_n != 1 else ''})"
        )
    return lines


def print_cost_summary(
    variant_index: int,
    total_variants: int,
    images_this_reel: int,
    total_batch_images: int,
    total_reel_cost: float,
    *,
    batch_cost: float | None = None,
    research_cost: float = 0.0,
    image_cost: float = 0.0,
    audio_cost: float = 0.0,
    post_type: str = "",
    tts_chars: int = 0,
    audio_label: str = "",
    categories: dict | None = None,
    reel_stills: int | None = None,
) -> None:
    """Print per-slot cost, then batch totals when more than one slot exists."""
    try:
        v = max(1, _safe_int(variant_index, 1))
        n = max(1, _safe_int(total_variants, 1))
        imgs = max(0, _safe_int(images_this_reel, 0))
        batch = max(0, _safe_int(total_batch_images, 0))
        cost = max(0.0, _safe_float(total_reel_cost, 0.0))
        img_usd = max(0.0, _safe_float(image_cost, 0.0))
        split_lines = image_cost_lines(categories)
        if img_usd <= 0 and imgs > 0 and not split_lines:
            img_usd = imgs * FLUX_SCHNELL_USD_PER_IMAGE
        pt = (post_type or "").upper().strip()
        unit, suffix = slot_unit_label(pt)
        static = is_static_image_post(pt)
        chars = max(0, _safe_int(tts_chars, 0))
        voice_usd = max(0.0, _safe_float(audio_cost, 0.0))
        research_usd = max(0.0, _safe_float(research_cost, 0.0))
        is_econ_reel = pt == "ECONOMIC_REEL"
        omit_audio = static or (chars <= 0 and not is_econ_reel)
        if omit_audio:
            voice_usd = 0.0
            cost = research_usd + img_usd
        print("=" * 62)
        print(f"| COST ANALYSIS SUMMARY - {unit} {v}/{n}{suffix}")
        print("=" * 62)
        if pt in ("LONG_CAPTION_IMAGE", "CTA_CAPTION_IMAGE"):
            asset_line = (
                f"  Visual Assets: {imgs} AI Image{'s' if imgs != 1 else ''} "
                f"({pt})"
            )
        elif is_econ_reel and reel_stills is not None:
            asset_line = f"  Stills in reel: {max(0, int(reel_stills))}"
        elif is_econ_reel:
            asset_line = (
                f"  Visual Assets: {imgs} AI Image{'s' if imgs != 1 else ''} "
                f"(ECONOMIC_REEL)"
            )
        elif static:
            asset_line = (
                f"  Visual Assets (This {unit}) : {imgs} AI Image"
                f"{'s' if imgs != 1 else ''} (Static Post)"
            )
        else:
            asset_line = (
                f"  Visual Assets (This Reel) : {imgs} FLUX Schnell API image"
                f"{'s' if imgs != 1 else ''} → compiled MP4"
            )
        print(asset_line)
        if is_econ_reel:
            audio_engine = str(audio_label or "F5TTS")
            print(f"  Audio Synthesis: {chars:,} chars ({audio_engine})")
        print(f"  - Research & Script (Gemini): ${research_usd:.4f}")
        if split_lines:
            for _image_line in split_lines:
                print(_image_line)
        else:
            print(
                f"  - Image Gen (FLUX Schnell):   ${img_usd:.4f}  "
                f"({imgs} generation{'s' if imgs != 1 else ''})"
            )
        if is_econ_reel:
            print(f"  - Voice & Audio:              ${voice_usd:.4f}")
            print("  - Video Render (MoviePy):     $0.0000")
        elif not omit_audio:
            print(f"  - Voice & Audio (ElevenLabs): ${voice_usd:.4f}")
        print(f"  Estimated Cost (This {unit}):   ${cost:.4f} USD")
        if n > 1:
            bcost = max(0.0, _safe_float(batch_cost, 0.0))
            print("-" * 62)
            print(
                f"  Batch so far: {batch} API images | "
                f"BATCH TOTAL: ${bcost:.4f} USD ({v}/{n} {unit.lower()}s)"
            )
        print("=" * 62)
    except Exception as exc:  # noqa: BLE001
        logger.warning("print_cost_summary failed (%s) — skipping block", exc)
        print("| COST ANALYSIS SUMMARY | (unavailable)")
