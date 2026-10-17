# -*- coding: utf-8 -*-
"""ElevenLabs Flows video.

Verified against the official create/get docs and the installed
``elevenlabs`` SDK 2.73.0 (``VideoGenerationRequest_Veo31FastGenerate001``,
``flows.video.create/get``, ``user.subscription.get``). The HTTP body matches
that SDK's ``model_dump(by_alias=True)``. Calls do not go through the SDK:
its User-Agent is ``elevenlabs/2.73.0``, and this factory sends a browser
User-Agent on HTTP.

Wire shape (Veo), unknown keys omitted because the API rejects them::

    POST /v1/flows/video
    {
      "model_id": "veo-3.1-fast-generate-001",
      "prompt": "...",
      "duration_secs": 4,
      "aspect_ratio": "9:16",
      "resolution": "720p",
      "generate_audio": false
    }

    GET /v1/flows/video/{generation_id}
    GET /v1/user/subscription

Veo durations are 4, 6, or 8 seconds. The SDK type uses ``Literal | Any``,
so it accepts 5 and ``4k``; the API enum does not. This module follows the
API enum (``4K`` for Veo, ``4k`` for Seedance).
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from modules.video_providers.base import VideoProvider
from modules.video_providers.errors import (
    VideoProviderError,
    VideoRequestError,
    failure_is_retryable,
)
from modules.video_providers.http import (
    HttpResult,
    Transport,
    dumps_json,
    exchange,
    http_status_retryable,
    open_stream,
    read_chunks,
)
from modules.video_providers.types import (
    CostEstimate,
    ProviderCapabilities,
    UsageSnapshot,
    VideoJob,
    VideoRequest,
)

API_ROOT = "https://api.elevenlabs.io"
DEFAULT_MODEL = "veo-3.1-fast-generate-001"
QUALITY_MODEL = "veo-3.1-generate-001"
VEO_MODELS = (DEFAULT_MODEL, QUALITY_MODEL)

# Seedance is on the API but returns model_access_denied until ElevenLabs
# approves the workspace. It is reachable only when config opts in.
SEEDANCE_MODELS: dict[str, dict[str, Any]] = {
    "bytedance-seedance-v2": {
        "resolutions": ("480p", "720p", "1080p", "4k"),
        "max_images": 9,
        "seed": True,
    },
    "bytedance-seedance-v2-fast": {
        "resolutions": ("480p", "720p"),
        "max_images": 9,
        "seed": True,
    },
    "bytedance-seedance-v2-mini": {
        "resolutions": ("480p", "720p"),
        "max_images": 9,
        "seed": True,
    },
    "bytedance-seedance-v2.5": {
        "resolutions": ("480p", "720p", "1080p"),
        "max_images": 30,
        "seed": False,
    },
}
SEEDANCE_ASPECTS = ("auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
VEO_ASPECTS = ("16:9", "9:16")
VEO_DURATIONS = (4, 6, 8)
VEO_RESOLUTIONS = {"720p": "720p", "1080p": "1080p", "4k": "4K", "4K": "4K"}
INLINE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}
MAX_INLINE_BYTES = 25 * 1024 * 1024
MIN_POLL_S = 10.0

# Body codes that will not succeed on a blind retry.
_PERMANENT_API_CODES = frozenset(
    {
        "paid_plan_required",
        "model_access_denied",
        "invalid_api_key",
        "quota_exceeded",
        "missing_permissions",
        "unauthorized",
        "forbidden",
    }
)


def _api_code(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    detail = payload.get("detail")
    if isinstance(detail, dict):
        for key in ("status", "code", "type"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        message = detail.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    for key in ("status", "code", "type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in {"error"}:
            return value.strip()
    return ""


def _error_text(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict):
            message = detail.get("message") or detail.get("msg")
            if message:
                return str(message)
        if isinstance(detail, list) and detail:
            first = detail[0]
            if isinstance(first, dict) and first.get("msg"):
                return str(first["msg"])
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        message = payload.get("message")
        if message:
            return str(message)
    if isinstance(payload, str) and payload.strip():
        return payload.strip()[:500]
    return fallback


class ElevenLabsVideoProvider(VideoProvider):
    """Flows video on the Pro plan. Polling only — no webhooks."""

    name = "elevenlabs"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        allow_seedance: bool = False,
        base_url: str | None = None,
        transport: Transport | None = None,
        timeout: float = 120.0,
        inline_limit: int = MAX_INLINE_BYTES,
    ) -> None:
        self.model = (model or DEFAULT_MODEL).strip()
        self._api_key = api_key
        self.allow_seedance = bool(allow_seedance)
        root = (base_url or os.getenv("ELEVENLABS_API_BASE") or API_ROOT).strip()
        self.base_url = root.rstrip("/")
        self._transport = transport
        self._timeout = timeout
        self._inline_limit = inline_limit

    def capabilities(self, request: VideoRequest | None = None) -> ProviderCapabilities:
        caps = self._describe()
        if request is not None:
            self._validate(request)
        return caps

    def _describe(self) -> ProviderCapabilities:
        seedance = SEEDANCE_MODELS.get(self.model)
        if seedance is not None:
            return ProviderCapabilities(
                provider=self.name,
                active=True,
                model=self.model,
                models=tuple(SEEDANCE_MODELS),
                default_model=DEFAULT_MODEL,
                quality_model=QUALITY_MODEL,
                durations_secs=(),
                aspects=SEEDANCE_ASPECTS,
                resolutions=tuple(seedance["resolutions"]),
                max_reference_images=int(seedance["max_images"]),
                supports_negative_prompt=False,
                supports_seed=bool(seedance["seed"]),
                supports_generate_audio=True,
                supports_start_end_frame=True,
                reference_requires_duration=None,
                min_poll_interval_s=MIN_POLL_S,
                poll_backoff=1.5,
                poll_max_interval_s=60.0,
                notes=(
                    "Seedance needs ElevenLabs model access. "
                    "Duration limits are not published; the API may still reject a value."
                ),
            )
        return ProviderCapabilities(
            provider=self.name,
            active=True,
            model=self.model,
            models=VEO_MODELS,
            default_model=DEFAULT_MODEL,
            quality_model=QUALITY_MODEL,
            durations_secs=VEO_DURATIONS,
            aspects=VEO_ASPECTS,
            resolutions=("720p", "1080p", "4K"),
            max_reference_images=3,
            supports_negative_prompt=True,
            supports_seed=True,
            supports_generate_audio=True,
            supports_start_end_frame=True,
            reference_requires_duration=8,
            min_poll_interval_s=MIN_POLL_S,
            poll_backoff=1.5,
            poll_max_interval_s=60.0,
            notes=(
                "Veo 3.1 via ElevenLabs Flows. "
                f"Default {DEFAULT_MODEL}; quality option {QUALITY_MODEL}."
            ),
        )

    def estimate_cost(self, request: VideoRequest) -> CostEstimate:
        self.capabilities(request)
        return CostEstimate(
            provider=self.name,
            model=self.model,
            credits=None,
            usd=None,
            unit="character_count",
            note=(
                "ElevenLabs does not publish a per-second video price. "
                "Measure the character_count delta on GET /v1/user/subscription."
            ),
        )

    def public_params(self, request: VideoRequest) -> dict[str, object]:
        self._validate(request)
        body = self._body(request, encode_files=False)
        return body

    def submit(self, request: VideoRequest) -> VideoJob:
        self._validate(request)
        key = self._require_key()
        body = self._body(request, encode_files=True)
        result = self._json(
            "POST",
            f"{self.base_url}/v1/flows/video",
            key=key,
            payload=body,
        )
        data = _json_dict(result)
        job_id = str(data.get("id") or "").strip()
        status = str(data.get("status") or "").strip().lower()
        if not job_id:
            raise VideoProviderError(
                "ElevenLabs create response had no generation id.",
                retryable=False,
            )
        return VideoJob(id=job_id, status=status or "pending", provider=self.name, model=self.model)

    def poll(self, job_id: str) -> VideoJob:
        key = self._require_key()
        ident = (job_id or "").strip()
        if not ident:
            raise VideoRequestError("poll requires a generation id.")
        result = self._json(
            "GET",
            f"{self.base_url}/v1/flows/video/{ident}",
            key=key,
        )
        return self._job_from_body(_json_dict(result), fallback_id=ident)

    def usage_snapshot(self) -> UsageSnapshot | None:
        key = self._require_key()
        result = self._json("GET", f"{self.base_url}/v1/user/subscription", key=key)
        return _usage_from_body(_json_dict(result))

    def download(self, job: VideoJob, dest: Path) -> Path:
        target = Path(dest)
        if target.exists():
            raise FileExistsError(f"Refusing to overwrite {target}")
        part = target.with_name(target.name + ".part")
        if part.exists():
            raise FileExistsError(f"Refusing to overwrite in-progress download {part}")
        url = (job.content_url or "").strip()
        if not url:
            raise VideoProviderError(
                "Completed generation has no content_url.",
                retryable=True,
                job_id=job.id,
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._stream_to(url, part)
        except Exception as exc:
            if part.exists():
                part.unlink()
            if (
                isinstance(exc, VideoProviderError)
                and exc.retryable
                and _download_needs_fresh_url(exc)
                and job.id
            ):
                fresh = self.poll(job.id)
                fresh_url = (fresh.content_url or "").strip()
                if not fresh_url:
                    raise
                if target.exists() or part.exists():
                    raise FileExistsError(f"Refusing to overwrite {target}") from exc
                try:
                    self._stream_to(fresh_url, part)
                except Exception:
                    if part.exists():
                        part.unlink()
                    raise
            else:
                raise
        if target.exists():
            part.unlink(missing_ok=True)
            raise FileExistsError(f"Refusing to overwrite {target}")
        os.replace(os.fspath(part), os.fspath(target))
        return target

    def _stream_to(self, url: str, part: Path) -> None:
        # Signed content URLs must not receive the API key.
        status, headers, resp = open_stream(
            "GET",
            url,
            headers={"Accept": "*/*"},
            timeout=max(self._timeout, 300.0),
            transport=self._transport,
        )
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd: int | None = None
        try:
            if status < 200 or status >= 300:
                detail = ""
                try:
                    raw = resp.read()
                    if isinstance(raw, bytes):
                        detail = raw.decode("utf-8", errors="replace")[:500]
                    else:
                        detail = str(raw)
                except Exception:  # noqa: BLE001
                    detail = ""
                raise VideoProviderError(
                    f"Download failed HTTP {status}: {detail or url}",
                    retryable=http_status_retryable(status) or status in {401, 403, 410},
                )
            content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type.startswith("text/"):
                raise VideoProviderError(
                    f"Download returned {content_type}, not video bytes.",
                    retryable=True,
                )
            fd = os.open(os.fspath(part), flags, 0o644)
            for chunk in read_chunks(resp):
                os.write(fd, chunk)
            os.fsync(fd)
        finally:
            if fd is not None:
                os.close(fd)
            close = getattr(resp, "close", None)
            if callable(close):
                close()

    def _validate(self, request: VideoRequest) -> None:
        prompt = (request.prompt or "").strip()
        if not prompt:
            raise VideoRequestError("prompt is required.")
        if request.seed is not None and isinstance(request.seed, bool):
            raise VideoRequestError("seed must be an integer.")
        if request.audio is not None and not isinstance(request.audio, bool):
            raise VideoRequestError("audio must be a bool (generate audio) or omitted.")
        seedance = SEEDANCE_MODELS.get(self.model)
        if seedance is not None:
            if not self.allow_seedance:
                raise VideoRequestError(
                    f"{self.model} needs ElevenLabs access approval. "
                    "Set VIDEO_ALLOW_SEEDANCE (or the channel prefix, "
                    "for example ESP_VIDEO_ALLOW_SEEDANCE) "
                    "only after that access is granted."
                )
            self._validate_seedance(request, seedance)
            return
        if self.model not in VEO_MODELS:
            raise VideoRequestError(
                f"Unsupported ElevenLabs video model {self.model!r}. "
                f"Use {DEFAULT_MODEL} or {QUALITY_MODEL}."
            )
        self._validate_veo(request)

    def _validate_veo(self, request: VideoRequest) -> None:
        duration = _optional_duration(request.duration, allowed=VEO_DURATIONS)
        if request.aspect is not None and request.aspect.strip() not in VEO_ASPECTS:
            raise VideoRequestError(
                f"aspect must be one of {', '.join(VEO_ASPECTS)}."
            )
        if request.resolution is not None and request.resolution.strip() not in VEO_RESOLUTIONS:
            raise VideoRequestError("resolution must be 720p, 1080p, or 4K.")
        if request.seed is not None and not isinstance(request.seed, int):
            raise VideoRequestError("seed must be an integer.")
        images = request.reference_images
        if len(images) > 3:
            raise VideoRequestError("Veo accepts at most 3 reference images.")
        for image in images:
            if image.role not in {"subject", "style"}:
                raise VideoRequestError("reference image role must be subject or style.")
        framed = bool(request.start_frame or request.end_frame)
        if images and framed:
            raise VideoRequestError(
                "Veo reference images cannot be combined with a start or end frame."
            )
        if images and duration not in (None, 8):
            raise VideoRequestError("Veo reference images require an 8-second duration.")
        for image in images:
            _check_image_source(image.source, limit=self._inline_limit)
        if request.start_frame:
            _check_image_source(request.start_frame, limit=self._inline_limit)
        if request.end_frame:
            _check_image_source(request.end_frame, limit=self._inline_limit)

    def _validate_seedance(self, request: VideoRequest, spec: dict[str, Any]) -> None:
        if request.negative_prompt:
            raise VideoRequestError(f"{self.model} does not accept negative_prompt.")
        if request.duration is not None:
            _positive_int_duration(request.duration)
        if request.aspect is not None and request.aspect.strip() not in SEEDANCE_ASPECTS:
            raise VideoRequestError(
                f"aspect must be one of {', '.join(SEEDANCE_ASPECTS)}."
            )
        allowed_res = {item.lower(): item for item in spec["resolutions"]}
        if request.resolution is not None and request.resolution.strip().lower() not in allowed_res:
            raise VideoRequestError(
                f"resolution for {self.model} must be one of {', '.join(spec['resolutions'])}."
            )
        if request.seed is not None and not spec["seed"]:
            raise VideoRequestError(f"{self.model} does not accept seed.")
        if request.seed is not None and not isinstance(request.seed, int):
            raise VideoRequestError("seed must be an integer.")
        images = request.reference_images
        if len(images) > int(spec["max_images"]):
            raise VideoRequestError(
                f"{self.model} accepts at most {spec['max_images']} reference images."
            )
        for image in images:
            if image.role != "subject":
                raise VideoRequestError(
                    f"{self.model} reference images have no style role."
                )
        if images and (request.start_frame or request.end_frame):
            raise VideoRequestError(
                "Seedance reference images cannot be combined with a start or end frame."
            )
        for image in images:
            _check_image_source(image.source, limit=self._inline_limit)
        if request.start_frame:
            _check_image_source(request.start_frame, limit=self._inline_limit)
        if request.end_frame:
            _check_image_source(request.end_frame, limit=self._inline_limit)

    def _body(self, request: VideoRequest, *, encode_files: bool) -> dict[str, Any]:
        if self.model in SEEDANCE_MODELS:
            return self._seedance_body(request, encode_files=encode_files)
        return self._veo_body(request, encode_files=encode_files)

    def _veo_body(self, request: VideoRequest, *, encode_files: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model_id": self.model,
            "prompt": request.prompt.strip(),
        }
        if request.negative_prompt and request.negative_prompt.strip():
            body["negative_prompt"] = request.negative_prompt.strip()
        if request.seed is not None:
            body["seed"] = int(request.seed)
        duration = _optional_duration(request.duration, allowed=VEO_DURATIONS)
        if duration is not None:
            body["duration_secs"] = duration
        if request.aspect:
            body["aspect_ratio"] = request.aspect.strip()
        if request.resolution:
            body["resolution"] = VEO_RESOLUTIONS[request.resolution.strip()]
        if request.audio is not None:
            body["generate_audio"] = bool(request.audio)
        if request.start_frame:
            body["start_frame"] = _image_reference(
                request.start_frame, encode=encode_files, limit=self._inline_limit
            )
        if request.end_frame:
            body["end_frame"] = _image_reference(
                request.end_frame, encode=encode_files, limit=self._inline_limit
            )
        if request.reference_images:
            body["images"] = [
                {
                    "image": _image_reference(
                        image.source, encode=encode_files, limit=self._inline_limit
                    ),
                    "role": image.role,
                }
                for image in request.reference_images
            ]
        return body

    def _seedance_body(self, request: VideoRequest, *, encode_files: bool) -> dict[str, Any]:
        spec = SEEDANCE_MODELS[self.model]
        body: dict[str, Any] = {
            "model_id": self.model,
            "prompt": request.prompt.strip(),
        }
        if request.aspect:
            body["aspect_ratio"] = request.aspect.strip()
        if request.duration is not None:
            body["duration_secs"] = _positive_int_duration(request.duration)
        if request.resolution:
            allowed = {item.lower(): item for item in spec["resolutions"]}
            body["resolution"] = allowed[request.resolution.strip().lower()]
        if request.seed is not None:
            body["seed"] = int(request.seed)
        if request.audio is not None:
            body["generate_audio"] = bool(request.audio)
        if request.start_frame:
            body["start_frame"] = _image_reference(
                request.start_frame, encode=encode_files, limit=self._inline_limit
            )
        if request.end_frame:
            body["end_frame"] = _image_reference(
                request.end_frame, encode=encode_files, limit=self._inline_limit
            )
        if request.reference_images:
            body["images"] = [
                _image_reference(image.source, encode=encode_files, limit=self._inline_limit)
                for image in request.reference_images
            ]
        return body

    def _require_key(self) -> str:
        raw = self._api_key
        if raw is None:
            raw = os.getenv("ELEVENLABS_API_KEY") or ""
        key = str(raw).strip()
        if not key:
            raise VideoRequestError(
                "ELEVENLABS_API_KEY is empty. Add the secret key (it starts with 'sk_')."
            )
        if not key.startswith("sk_"):
            raise VideoRequestError(
                "ELEVENLABS_API_KEY is not a secret key (must start with 'sk_')."
            )
        return key

    def _json(
        self,
        method: str,
        url: str,
        *,
        key: str,
        payload: dict[str, Any] | None = None,
    ) -> HttpResult:
        headers = {
            "xi-api-key": key,
            "Accept": "application/json",
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = dumps_json(payload)
        result = exchange(
            method,
            url,
            headers=headers,
            body=body,
            timeout=self._timeout,
            transport=self._transport,
        )
        if 200 <= result.status < 300:
            return result
        self._raise_http(method, url, result)
        raise AssertionError("unreachable")

    def _raise_http(self, method: str, url: str, result: HttpResult) -> None:
        payload: Any
        try:
            payload = result.json()
        except (ValueError, UnicodeError):
            payload = result.body.decode("utf-8", errors="replace")[:500]
        code = _api_code(payload).lower()
        message = _error_text(payload, f"HTTP {result.status}")
        permanent = (
            code in _PERMANENT_API_CODES
            or result.status in {400, 401, 402, 403, 404, 422}
        )
        retryable = http_status_retryable(result.status) and not permanent
        if code in _PERMANENT_API_CODES:
            retryable = False
        raise VideoProviderError(
            f"ElevenLabs {method} {url} failed ({result.status} {code or 'error'}): {message}",
            retryable=retryable,
            failure_reason=code or None,
        )

    def _job_from_body(self, data: dict[str, Any], *, fallback_id: str) -> VideoJob:
        status = str(data.get("status") or "").strip().lower()
        job_id = str(data.get("id") or fallback_id).strip()
        reason = data.get("failure_reason")
        reason_s = str(reason).strip() if reason else None
        retryable: bool | None = None
        if status == "failed":
            retryable = failure_is_retryable(reason_s)
        return VideoJob(
            id=job_id,
            status=status,
            provider=self.name,
            model=self.model,
            content_url=(str(data["content_url"]) if data.get("content_url") else None),
            content_mime_type=(
                str(data["content_mime_type"]) if data.get("content_mime_type") else None
            ),
            failure_reason=reason_s,
            error_message=(str(data["error_message"]) if data.get("error_message") else None),
            retryable=retryable,
        )


def _json_dict(result: HttpResult) -> dict[str, Any]:
    try:
        data = result.json()
    except (ValueError, UnicodeError) as exc:
        raise VideoProviderError(
            "ElevenLabs returned a body that was not JSON.",
            retryable=True,
        ) from exc
    if not isinstance(data, dict):
        raise VideoProviderError(
            "ElevenLabs returned a JSON value that was not an object.",
            retryable=False,
        )
    return data


def _usage_from_body(data: dict[str, Any]) -> UsageSnapshot:
    raw_count = data.get("character_count")
    credits: int | None
    if isinstance(raw_count, bool) or raw_count is None:
        credits = None
    else:
        try:
            credits = int(raw_count)
        except (TypeError, ValueError):
            credits = None
    fields: dict[str, object] = {}
    for key in (
        "character_count",
        "character_limit",
        "tier",
        "status",
        "max_credit_limit_extension",
        "can_extend_character_limit",
    ):
        if key in data and _scalar(data[key]):
            fields[key] = data[key]
    for key, value in data.items():
        if "credit" in key.lower() and key not in fields and _scalar(value):
            fields[key] = value
    tier = data.get("tier")
    return UsageSnapshot(
        credits=credits,
        unit="character_count",
        tier=str(tier) if isinstance(tier, str) else None,
        fields=fields,
    )


def _scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _optional_duration(value: float | None, *, allowed: tuple[int, ...]) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, str):
        raise VideoRequestError(f"duration must be one of {', '.join(str(n) for n in allowed)}.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise VideoRequestError(
            f"duration must be one of {', '.join(str(n) for n in allowed)}."
        ) from exc
    if number != int(number):
        raise VideoRequestError(f"duration must be one of {', '.join(str(n) for n in allowed)}.")
    whole = int(number)
    if whole not in allowed:
        raise VideoRequestError(
            f"duration must be one of {', '.join(str(n) for n in allowed)} seconds."
        )
    return whole


def _positive_int_duration(value: float) -> int:
    if isinstance(value, bool):
        raise VideoRequestError("duration must be a positive whole number of seconds.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise VideoRequestError("duration must be a positive whole number of seconds.") from exc
    if number != int(number) or int(number) < 1 or int(number) > 60:
        raise VideoRequestError("duration must be a whole number of seconds from 1 to 60.")
    return int(number)


def _check_image_source(source: str, *, limit: int) -> None:
    _image_reference(source, encode=False, limit=limit)


def _image_reference(source: str, *, encode: bool, limit: int) -> dict[str, Any]:
    raw = (source or "").strip()
    if raw.startswith("asset:"):
        asset_id = raw.split(":", 1)[1].strip()
        if not asset_id:
            raise VideoRequestError("asset reference is missing an asset_id.")
        return {"type": "asset", "asset_id": asset_id}
    if raw.startswith("generation:"):
        generation_id = raw.split(":", 1)[1].strip()
        if not generation_id:
            raise VideoRequestError("generation reference is missing a generation_id.")
        return {"type": "generation", "generation_id": generation_id}
    path = Path(raw)
    if not path.is_file():
        raise VideoRequestError(f"image not found: {raw}")
    mime = INLINE_MIME.get(path.suffix.lower())
    if mime is None:
        raise VideoRequestError(
            "image must be jpeg, png, webp, heic, or heif "
            "(or asset:<id> / generation:<id>)."
        )
    size = path.stat().st_size
    if size > limit:
        raise VideoRequestError(
            f"inline image exceeds {limit} bytes ({size} bytes). "
            "Upload it with POST /v1/assets and pass asset:<asset_id>."
        )
    if not encode:
        return {"type": "inline_base64", "mime_type": mime, "bytes": size}
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "inline_base64", "content_base64": encoded, "mime_type": mime}


def _download_needs_fresh_url(exc: VideoProviderError) -> bool:
    text = str(exc)
    return any(token in text for token in ("HTTP 401", "HTTP 403", "HTTP 410"))
