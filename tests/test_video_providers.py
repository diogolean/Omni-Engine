# -*- coding: utf-8 -*-
"""Video providers. HTTP is mocked. No test may open a socket."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.request import Request

import pytest
from models_avatar.http_client import BROWSER_USER_AGENT
from modules.video_providers.cli import build_parser, main, request_from_args
from modules.video_providers.config import load_channel_settings
from modules.video_providers.elevenlabs import (
    DEFAULT_MODEL,
    QUALITY_MODEL,
    ElevenLabsVideoProvider,
)
from modules.video_providers.errors import (
    ProviderInactiveError,
    VideoDisabledError,
    VideoProviderError,
    VideoRequestError,
    failure_is_retryable,
)
from modules.video_providers.registry import get_provider
from modules.video_providers.service import generate_clip, resolve_clip_path, wait_for_terminal
from modules.video_providers.stubs import GeminiVeoProvider, KlingFalProvider
from modules.video_providers.types import ClipResult, ReferenceImage, VideoRequest


class _Resp:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body
        self._off = 0

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk = self._body[self._off :]
            self._off = len(self._body)
            return chunk
        chunk = self._body[self._off : self._off + n]
        self._off += len(chunk)
        return chunk

    def close(self) -> None:
        return None


class Script:
    def __init__(self) -> None:
        self.calls: list[Request] = []
        self._steps: list[_Resp] = []

    def push(self, status: int, body: dict | bytes, headers: dict[str, str] | None = None) -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self._steps.append(_Resp(status, raw, headers))

    def __call__(self, request: Request, timeout: float) -> _Resp:
        self.calls.append(request)
        if not self._steps:
            raise AssertionError(f"unexpected {request.get_method()} {request.full_url}")
        return self._steps.pop(0)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.t += delay


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("video provider tests must not open a socket")

    monkeypatch.setattr("modules.video_providers.http.urlopen", boom)
    for name in (
        "VIDEO_ENABLED",
        "VIDEO_PROVIDER",
        "VIDEO_MODEL",
        "VIDEO_ALLOW_SEEDANCE",
        "VIDEO_POLL_TIMEOUT_S",
        "VIDEO_POLL_MIN_INTERVAL_S",
        "ESP_VIDEO_ENABLED",
        "ESP_VIDEO_PROVIDER",
        "ESP_VIDEO_MODEL",
        "ESP_VIDEO_ALLOW_SEEDANCE",
        "ESP_VIDEO_POLL_TIMEOUT_S",
        "ESP_VIDEO_POLL_MIN_INTERVAL_S",
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_API_BASE",
    ):
        monkeypatch.delenv(name, raising=False)


def _request(**kwargs: object) -> VideoRequest:
    base: dict[str, object] = {"prompt": "a gelatin waterpark slide at noon"}
    base.update(kwargs)
    return VideoRequest(**base)  # type: ignore[arg-type]


def _provider(script: Script, **kwargs: object) -> ElevenLabsVideoProvider:
    options = {"api_key": "sk_test", "transport": script}
    options.update(kwargs)
    return ElevenLabsVideoProvider(**options)  # type: ignore[arg-type]


def _sub(count: int, **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "tier": "pro",
        "character_count": count,
        "character_limit": 100000,
        "status": "active",
        "max_credit_limit_extension": 0,
        "can_extend_character_limit": False,
        "next_invoice": {"amount_due_cents": 2200},
    }
    body.update(extra)
    return body


def test_failure_reason_map() -> None:
    assert failure_is_retryable("timeout") is True
    assert failure_is_retryable("model_error") is True
    assert failure_is_retryable("dependency_failed") is True
    assert failure_is_retryable("internal_error") is True
    assert failure_is_retryable("moderated") is False
    assert failure_is_retryable("invalid_parameters") is False
    assert failure_is_retryable("charging_failed") is False
    assert failure_is_retryable("brand_new_code") is False
    assert failure_is_retryable(None) is False


def test_capabilities_reject_before_any_http() -> None:
    script = Script()
    provider = _provider(script)
    with pytest.raises(VideoRequestError):
        provider.capabilities(_request(duration=5))
    with pytest.raises(VideoRequestError):
        provider.capabilities(_request(aspect="1:1"))
    with pytest.raises(VideoRequestError):
        provider.capabilities(_request(resolution="480p"))
    with pytest.raises(VideoRequestError):
        provider.capabilities(_request(prompt="  "))
    assert script.calls == []


def test_seedance_blocked_unless_config_allows_it() -> None:
    script = Script()
    blocked = _provider(script, model="bytedance-seedance-v2")
    with pytest.raises(VideoRequestError, match="access approval"):
        blocked.capabilities(_request(duration=5, resolution="720p"))
    allowed = _provider(script, model="bytedance-seedance-v2", allow_seedance=True)
    request = _request(duration=5, aspect="16:9", resolution="4k", seed=2)
    allowed.capabilities(request)
    body = allowed.public_params(request)
    assert body["model_id"] == "bytedance-seedance-v2"
    assert body["resolution"] == "4k"
    assert body["duration_secs"] == 5
    assert "negative_prompt" not in body
    assert "webhook" not in body
    assert script.calls == []


def test_veo_payload_omits_unset_fields_and_normalizes_4k(tmp_path: Path) -> None:
    script = Script()
    provider = _provider(script, model=QUALITY_MODEL)
    image = tmp_path / "frame.png"
    raw = b"\x89PNG-not-really"
    image.write_bytes(raw)
    request = _request(
        duration=8,
        aspect="9:16",
        resolution="4k",
        audio=False,
        negative_prompt="captions",
        seed=0,
        reference_images=(ReferenceImage(source=str(image), role="style"),),
    )
    provider.capabilities(request)
    body = provider._body(request, encode_files=True)
    encoded = base64.b64encode(raw).decode("ascii")
    assert body == {
        "model_id": QUALITY_MODEL,
        "prompt": "a gelatin waterpark slide at noon",
        "negative_prompt": "captions",
        "seed": 0,
        "duration_secs": 8,
        "aspect_ratio": "9:16",
        "resolution": "4K",
        "generate_audio": False,
        "images": [
            {
                "image": {
                    "type": "inline_base64",
                    "content_base64": encoded,
                    "mime_type": "image/png",
                },
                "role": "style",
            }
        ],
    }
    assert "webhook" not in body
    with pytest.raises(VideoRequestError, match="8-second"):
        provider.capabilities(
            _request(duration=4, reference_images=(ReferenceImage(str(image)),))
        )
    with pytest.raises(VideoRequestError, match="start or end frame"):
        provider.capabilities(
            _request(
                duration=8,
                start_frame=str(image),
                reference_images=(ReferenceImage(str(image)),),
            )
        )
    assert script.calls == []


def test_inline_image_over_limit_is_rejected(tmp_path: Path) -> None:
    image = tmp_path / "big.png"
    image.write_bytes(b"0123456789")
    provider = ElevenLabsVideoProvider(api_key="sk_test", transport=Script(), inline_limit=9)
    with pytest.raises(VideoRequestError, match="exceeds"):
        provider.capabilities(_request(start_frame=str(image)))


def test_generate_writes_sidecar_and_does_not_send_the_key_to_the_cdn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    script = Script()
    script.push(200, _sub(1000, credit_balance=50))
    script.push(200, {"id": "job_1", "status": "pending"})
    script.push(
        200,
        {
            "id": "job_1",
            "status": "completed",
            "content_url": "https://cdn.example/clip.mp4",
            "content_mime_type": "video/mp4",
        },
    )
    script.push(200, b"mp4-bytes", {"content-type": "video/mp4"})
    script.push(200, _sub(1400, credit_balance=50))
    dest = tmp_path / "endless_summer_paradise" / "clips" / "clip.mp4"
    clock = Clock()
    result = generate_clip(
        _request(duration=4, aspect="9:16", resolution="720p", audio=False),
        dest=dest,
        provider=_provider(script),
        force=True,
        sleep=clock.sleep,
        clock=clock,
    )
    assert clock.sleeps == []
    assert dest.read_bytes() == b"mp4-bytes"
    assert not dest.with_name(dest.name + ".part").exists()
    side = json.loads(Path(result.sidecar_path).read_text(encoding="utf-8"))
    assert side["provider"] == "elevenlabs"
    assert side["model"] == DEFAULT_MODEL
    assert side["job_id"] == "job_1"
    assert side["params"]["duration_secs"] == 4
    assert side["params"]["aspect_ratio"] == "9:16"
    assert side["params"]["resolution"] == "720p"
    assert side["params"]["generate_audio"] is False
    assert side["credits_before"] == 1000
    assert side["credits_after"] == 1400
    assert side["credits_delta"] == 400
    assert side["credits_unit"] == "character_count"
    assert side["elapsed_s"] == 0.0
    assert side["subscription_before"]["credit_balance"] == 50
    assert "next_invoice" not in side["subscription_before"]
    assert result.credits_delta == 400
    methods = [(call.get_method(), call.full_url) for call in script.calls]
    assert methods == [
        ("GET", "https://api.elevenlabs.io/v1/user/subscription"),
        ("POST", "https://api.elevenlabs.io/v1/flows/video"),
        ("GET", "https://api.elevenlabs.io/v1/flows/video/job_1"),
        ("GET", "https://cdn.example/clip.mp4"),
        ("GET", "https://api.elevenlabs.io/v1/user/subscription"),
    ]
    posted = json.loads(script.calls[1].data.decode("utf-8"))
    assert posted["model_id"] == DEFAULT_MODEL
    assert posted["duration_secs"] == 4
    assert "webhook" not in posted
    for call in script.calls:
        assert call.get_header("User-agent") == BROWSER_USER_AGENT
        assert str(call.get_header("User-agent")).startswith("Mozilla/5.0")
    assert script.calls[1].get_header("Xi-api-key") == "sk_test"
    assert script.calls[3].get_header("Xi-api-key") is None
    with pytest.raises(FileExistsError):
        generate_clip(
            _request(duration=4),
            dest=dest,
            provider=_provider(script),
            force=True,
            sleep=clock.sleep,
            clock=clock,
        )
    assert len(script.calls) == 5


def test_failed_generation_records_credits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    script = Script()
    script.push(200, _sub(10))
    script.push(200, {"id": "job_m", "status": "pending"})
    script.push(
        200,
        {
            "id": "job_m",
            "status": "failed",
            "failure_reason": "moderated",
            "error_message": "blocked",
        },
    )
    script.push(200, _sub(10))
    dest = tmp_path / "clips" / "nope.mp4"
    with pytest.raises(VideoProviderError) as caught:
        generate_clip(
            _request(duration=4),
            dest=dest,
            provider=_provider(script),
            force=True,
            sleep=Clock().sleep,
            clock=Clock(),
        )
    assert caught.value.retryable is False
    assert caught.value.failure_reason == "moderated"
    assert not dest.exists()
    side = json.loads(dest.with_suffix(".video.json").read_text(encoding="utf-8"))
    assert side["status"] == "failed"
    assert side["job_id"] == "job_m"
    assert side["retryable"] is False
    assert side["credits_delta"] == 0


def test_poll_backoff_never_faster_than_ten_seconds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    script = Script()
    script.push(200, _sub(1))
    script.push(200, {"id": "job_t", "status": "pending"})
    script.push(200, {"id": "job_t", "status": "pending"})
    script.push(200, {"id": "job_t", "status": "pending"})
    script.push(200, _sub(1))
    clock = Clock()
    dest = tmp_path / "clips" / "slow.mp4"
    with pytest.raises(VideoProviderError) as caught:
        generate_clip(
            _request(duration=4),
            dest=dest,
            provider=_provider(script),
            force=True,
            sleep=clock.sleep,
            clock=clock,
            timeout_s=25,
            min_interval_s=1,
        )
    assert caught.value.retryable is True
    assert caught.value.failure_reason == "timeout"
    assert clock.sleeps == [10.0, 15.0]
    assert all(delay >= 10 for delay in clock.sleeps)
    side = json.loads(dest.with_suffix(".video.json").read_text(encoding="utf-8"))
    assert side["status"] == "timeout"
    assert side["job_id"] == "job_t"
    assert side["elapsed_s"] == 25.0
    assert script._steps == []


def test_paid_plan_required_is_not_retryable() -> None:
    script = Script()
    script.push(
        402,
        {"detail": {"status": "paid_plan_required", "message": "Upgrade to Pro"}},
    )
    provider = _provider(script)
    with pytest.raises(VideoProviderError) as caught:
        provider.submit(_request(duration=4))
    assert caught.value.retryable is False
    assert caught.value.failure_reason == "paid_plan_required"


def test_expired_content_url_is_refetched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    script = Script()
    script.push(200, _sub(5))
    script.push(200, {"id": "job_u", "status": "pending"})
    script.push(
        200,
        {
            "id": "job_u",
            "status": "completed",
            "content_url": "https://cdn.example/old.mp4",
            "content_mime_type": "video/mp4",
        },
    )
    script.push(403, {"detail": "expired"})
    script.push(
        200,
        {
            "id": "job_u",
            "status": "completed",
            "content_url": "https://cdn.example/new.mp4",
            "content_mime_type": "video/mp4",
        },
    )
    script.push(200, b"fresh", {"content-type": "video/mp4"})
    script.push(200, _sub(9))
    dest = tmp_path / "clips" / "fresh.mp4"
    result = generate_clip(
        _request(duration=6, aspect="16:9", resolution="1080p"),
        dest=dest,
        provider=_provider(script),
        force=True,
        sleep=Clock().sleep,
        clock=Clock(),
    )
    assert dest.read_bytes() == b"fresh"
    assert result.credits_delta == 4
    urls = [call.full_url for call in script.calls]
    assert "https://cdn.example/old.mp4" in urls
    assert "https://cdn.example/new.mp4" in urls


def test_existing_part_file_is_not_replaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    dest = tmp_path / "clips" / "clip.mp4"
    dest.parent.mkdir(parents=True)
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(b"partial")
    script = Script()
    with pytest.raises(FileExistsError):
        generate_clip(
            _request(duration=4),
            dest=dest,
            provider=_provider(script),
            force=True,
        )
    assert part.read_bytes() == b"partial"
    assert script.calls == []


def test_channel_is_off_until_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_channel_settings("endless_summer_paradise")
    assert settings.enabled is False
    assert settings.provider == "elevenlabs"
    assert settings.model == DEFAULT_MODEL
    assert settings.allow_seedance is False
    assert settings.env_prefix == "ESP"
    assert settings.poll_min_interval_s == 10.0
    with pytest.raises(VideoDisabledError):
        generate_clip(_request(duration=4), dest=Path("ignored.mp4"), force=False)
    monkeypatch.setenv("VIDEO_ENABLED", "true")
    monkeypatch.setenv("VIDEO_MODEL", "veo-3.1-generate-001")
    overridden = load_channel_settings("endless_summer_paradise")
    assert overridden.enabled is True
    assert overridden.model == QUALITY_MODEL
    monkeypatch.setenv("ESP_VIDEO_ENABLED", "false")
    monkeypatch.setenv("ESP_VIDEO_MODEL", "veo-3.1-fast-generate-001")
    specific = load_channel_settings("endless_summer_paradise")
    assert specific.enabled is False
    assert specific.model == DEFAULT_MODEL
    monkeypatch.setenv("ESP_VIDEO_POLL_MIN_INTERVAL_S", "2")
    with pytest.raises(VideoRequestError, match="10 seconds"):
        load_channel_settings("endless_summer_paradise")


def test_unknown_channel_stays_off() -> None:
    settings = load_channel_settings("not_a_channel")
    assert settings.enabled is False
    assert settings.provider == "elevenlabs"
    assert settings.model == DEFAULT_MODEL


def test_skeletons_do_not_submit() -> None:
    request = _request(duration=4)
    gemini = get_provider("gemini")
    assert isinstance(gemini, GeminiVeoProvider)
    assert gemini.capabilities().active is False
    estimate = gemini.estimate_cost(request)
    assert estimate.usd == 0.2
    with pytest.raises(ProviderInactiveError):
        gemini.submit(request)
    kling = get_provider("fal")
    assert isinstance(kling, KlingFalProvider)
    cost = kling.estimate_cost(request)
    assert cost.usd_low == pytest.approx(0.336)
    assert cost.usd_high == pytest.approx(0.672)
    with pytest.raises(ProviderInactiveError):
        kling.poll("job")


def test_wait_uses_provider_floor_not_a_faster_caller_interval() -> None:
    script = Script()
    script.push(200, {"id": "job_w", "status": "pending"})
    script.push(
        200,
        {
            "id": "job_w",
            "status": "completed",
            "content_url": "https://cdn.example/x.mp4",
            "content_mime_type": "video/mp4",
        },
    )
    clock = Clock()
    job = wait_for_terminal(
        _provider(script),
        "job_w",
        sleep=clock.sleep,
        clock=clock,
        timeout_s=30,
        min_interval_s=1,
    )
    assert job.status == "completed"
    assert clock.sleeps == [10.0]


def test_bad_api_key_never_calls_http() -> None:
    script = Script()
    provider = ElevenLabsVideoProvider(api_key="not-secret", transport=script)
    with pytest.raises(VideoRequestError, match="sk_"):
        provider.submit(_request(duration=4))
    assert script.calls == []


def test_cli_resolves_under_output_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    seen: dict[str, object] = {}

    def fake_generate(request: VideoRequest, **kwargs: object) -> ClipResult:
        seen["request"] = request
        seen["dest"] = kwargs["dest"]
        seen["force"] = kwargs["force"]
        return ClipResult(
            path=str(kwargs["dest"]),
            sidecar_path=str(kwargs["dest"]) + ".video.json",
            provider="elevenlabs",
            model=DEFAULT_MODEL,
            job_id="job",
            elapsed_s=1.0,
            credits_before=1,
            credits_after=2,
            credits_delta=1,
        )

    monkeypatch.setattr("modules.video_providers.cli.generate_clip", fake_generate)
    code = main(
        [
            "--provider",
            "elevenlabs",
            "--model",
            "veo-3.1-fast-generate-001",
            "--duration",
            "4",
            "--aspect",
            "9:16",
            "--resolution",
            "720p",
            "--prompt",
            "a gelatin slide at noon",
        ]
    )
    assert code == 0
    assert seen["force"] is True
    request = seen["request"]
    assert isinstance(request, VideoRequest)
    assert request.duration == 4.0
    assert request.aspect == "9:16"
    assert request.resolution == "720p"
    dest = seen["dest"]
    assert isinstance(dest, Path)
    assert dest.is_relative_to(tmp_path)
    assert dest.parent.name == "clips"
    assert dest.parent.parent.name == "endless_summer_paradise"
    direct = resolve_clip_path("endless_summer_paradise", "Noon Glow")
    assert direct.parent == tmp_path / "endless_summer_paradise" / "clips"


def test_cli_inactive_provider_exits_without_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    code = main(["--provider", "kling", "--prompt", "hello", "--duration", "4"])
    assert code == 2


def test_parser_matches_the_documented_command() -> None:
    args = build_parser().parse_args(
        [
            "--provider",
            "elevenlabs",
            "--model",
            "veo-3.1-fast-generate-001",
            "--duration",
            "4",
            "--aspect",
            "9:16",
            "--resolution",
            "720p",
            "--prompt",
            "a gelatin slide",
        ]
    )
    request = request_from_args(args)
    assert request.prompt == "a gelatin slide"
    assert request.duration == 4
    assert request.aspect == "9:16"
    assert request.resolution == "720p"
    assert request.audio is None


def test_payload_matches_installed_sdk() -> None:
    pytest.importorskip("elevenlabs")
    from elevenlabs import (  # type: ignore
        VideoGenerationRequest_Veo31FastGenerate001,
        VideoGenerationRequest_Veo31Generate001,
    )

    provider = ElevenLabsVideoProvider(model=DEFAULT_MODEL, api_key="sk_test", transport=Script())
    request = _request(
        duration=4,
        aspect="9:16",
        resolution="720p",
        audio=True,
        negative_prompt="logos",
        seed=9,
        start_frame="asset:asset_123",
        end_frame="generation:gen_456",
    )
    body = provider._body(request, encode_files=True)
    parsed = VideoGenerationRequest_Veo31FastGenerate001.model_validate(body)
    assert parsed.model_dump(exclude_none=True, by_alias=True) == body
    quality = ElevenLabsVideoProvider(model=QUALITY_MODEL, api_key="sk_test", transport=Script())
    quality_body = quality._body(
        _request(duration=8, aspect="16:9", resolution="1080p"),
        encode_files=True,
    )
    parsed_q = VideoGenerationRequest_Veo31Generate001.model_validate(quality_body)
    assert parsed_q.model_dump(exclude_none=True, by_alias=True) == quality_body


def test_estimate_cost_is_unknown_for_elevenlabs() -> None:
    estimate = ElevenLabsVideoProvider(api_key="sk_test", transport=Script()).estimate_cost(
        _request(duration=4)
    )
    assert estimate.credits is None
    assert estimate.usd is None
    assert "subscription" in estimate.note
