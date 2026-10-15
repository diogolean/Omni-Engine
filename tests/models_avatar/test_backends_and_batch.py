# -*- coding: utf-8 -*-
"""Mocked RunPod HTTP, stubs, Civitai downloads, and the batch runner."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from models_avatar.avatar_config import load_avatar
from models_avatar.backends import get_backend
from models_avatar.backends.base import GenerationJob, GenerationResult
from models_avatar.backends.runpod_pod import RunPodPodBackend
from models_avatar.backends.runpod_serverless import (
    RunPodServerlessBackend,
    build_run_payload,
    image_requirements,
)
from models_avatar.backends.stubs import CivitaiOnsiteBackend, GeminiImageBackend
from models_avatar.batch import run_batch
from models_avatar.civitai.client import CivitaiClient
from models_avatar.civitai.training import build_training_payload, submit_training_job
from models_avatar.errors import (
    BackendNotImplemented,
    CivitaiError,
    GenerationError,
    RetryableGenerationError,
)
from models_avatar.http_client import BROWSER_USER_AGENT
from models_avatar.prompts import compose_positive
from models_avatar.service import prepare_job, render

_SCENE = (
    "relaxed closed-mouth smile, standing with shoulders square to the camera, "
    "waist-up portrait facing the camera, wearing a beige t-shirt and light blue jeans, "
    "in a quiet room, soft morning window light"
)


class _Resp:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None, status: int = 200) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _job(tmp_path: Path, preset: str = "fast") -> GenerationJob:
    avatar = load_avatar("sophie_bennet")
    return prepare_job(
        avatar,
        preset=preset,
        scene=_SCENE,
        seed=99,
        dest=tmp_path / "out.png",
    )


def test_browser_user_agent_is_not_urllib() -> None:
    assert BROWSER_USER_AGENT.startswith("Mozilla/5.0")
    assert "python-urllib" not in BROWSER_USER_AGENT.lower()


def test_pod_backend_posts_prompt_and_downloads_view(tmp_path: Path) -> None:
    seen: list = []

    def transport(req, timeout=0):  # noqa: ANN001
        seen.append(req)
        url = req.full_url
        if url.endswith("/prompt"):
            body = json.dumps({"prompt_id": "pid-1", "node_errors": {}}).encode()
            return _Resp(body)
        if "/history/pid-1" in url:
            payload = {
                "pid-1": {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {
                        "9": {
                            "images": [
                                {"filename": "sophie.png", "subfolder": "", "type": "output"}
                            ]
                        }
                    },
                }
            }
            return _Resp(json.dumps(payload).encode())
        if "/view?" in url:
            assert "filename=sophie.png" in url
            return _Resp(b"\x89PNG-bytes")
        raise AssertionError(url)

    backend = RunPodPodBackend(
        base_url="https://example.proxy.runpod.net",
        api_key="pod-key",
        transport=transport,
        poll_interval_s=0.01,
        timeout_s=5,
    )
    result = backend.generate(_job(tmp_path))
    assert result.job_id == "pid-1"
    assert result.path.read_bytes() == b"\x89PNG-bytes"
    assert result.backend == "runpod_pod"
    assert len(seen) == 3
    for req in seen:
        assert req.get_header("User-agent") == BROWSER_USER_AGENT
        assert req.get_header("Authorization") == "Bearer pod-key"
    posted = json.loads(seen[0].data.decode())
    assert posted["prompt"]["1"]["inputs"]["ckpt_name"].endswith(".safetensors")
    assert "nude" in posted["prompt"]["5"]["inputs"]["text"]


def test_pod_polls_until_history_has_the_save_node(tmp_path: Path) -> None:
    clock = _Clock()
    calls = {"history": 0}

    def transport(req, timeout=0):  # noqa: ANN001
        url = req.full_url
        if url.endswith("/prompt"):
            return _Resp(json.dumps({"prompt_id": "pid-2"}).encode())
        if "/history/" in url:
            calls["history"] += 1
            if calls["history"] == 1:
                return _Resp(b"{}")
            payload = {
                "pid-2": {
                    "outputs": {
                        "9": {"images": [{"filename": "b.png", "subfolder": "x", "type": "output"}]}
                    }
                }
            }
            return _Resp(json.dumps(payload).encode())
        if "/view?" in url:
            assert "subfolder=x" in url
            return _Resp(b"img")
        raise AssertionError(url)

    backend = RunPodPodBackend(
        base_url="http://127.0.0.1:8188",
        api_key="",
        transport=transport,
        poll_interval_s=2,
        timeout_s=10,
        sleep=clock.sleep,
        monotonic=clock,
    )
    result = backend.generate(_job(tmp_path))
    assert calls["history"] == 2
    assert clock.now == pytest.approx(2)
    assert result.path.read_bytes() == b"img"


def test_serverless_payload_and_base64_image(tmp_path: Path) -> None:
    avatar = load_avatar("sophie_bennet")
    job = _job(tmp_path, "ultra_detail")
    payload = build_run_payload(job.workflow, timeout_s=600)
    assert payload["input"]["workflow"]["33"]["class_type"] == "FaceDetailer"
    assert "images" not in payload["input"]
    assert payload["policy"]["executionTimeout"] == 600000
    needs = image_requirements(avatar)
    assert "cyberrealisticXL_v100_fp16.safetensors" in needs["checkpoints"]
    assert avatar.identity_lora.filename in needs["loras"]
    assert "perfection_style_SDXL.safetensors" in needs["loras"]
    assert "bbox/face_yolov8m.pt" in needs["ultralytics_bbox"]
    assert "bbox/hand_yolov8s.pt" in needs["ultralytics_bbox"]
    assert "ultralytics" in needs["python_packages"]

    clock = _Clock()
    state = {"status": 0}
    encoded = base64.b64encode(b"serverless-png").decode("ascii")

    def transport(req, timeout=0):  # noqa: ANN001
        assert req.get_header("User-agent") == BROWSER_USER_AGENT
        assert req.get_header("Authorization") == "Bearer srv-key"
        if req.full_url.endswith("/run"):
            body = json.loads(req.data.decode())
            assert body["input"]["workflow"]["40"]["inputs"]["strength_model"] == pytest.approx(0.85)
            return _Resp(json.dumps({"id": "job-9"}).encode())
        state["status"] += 1
        if state["status"] == 1:
            return _Resp(json.dumps({"status": "IN_PROGRESS"}).encode())
        return _Resp(
            json.dumps(
                {
                    "status": "COMPLETED",
                    "output": {"images": [{"filename": "out.png", "data": encoded, "node_id": "9"}]},
                }
            ).encode()
        )

    backend = RunPodServerlessBackend(
        base_url="https://api.runpod.ai/v2/endpoint123",
        api_key="srv-key",
        transport=transport,
        poll_interval_s=1,
        timeout_s=10,
        sleep=clock.sleep,
        monotonic=clock,
    )
    result = backend.generate(job)
    assert result.job_id == "job-9"
    assert result.path.read_bytes() == b"serverless-png"
    assert result.backend == "runpod_serverless"


def test_extension_backends_are_not_implemented() -> None:
    with pytest.raises(BackendNotImplemented):
        CivitaiOnsiteBackend().generate(None)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        GeminiImageBackend().generate(None)  # type: ignore[arg-type]
    with pytest.raises(GenerationError):
        get_backend("nope")


def test_default_backend_is_the_live_pod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODELS_AVATAR_BACKEND", raising=False)
    backend = get_backend()
    assert backend.name == "runpod_pod"


def test_civitai_download_uses_the_token_and_lora_folder(tmp_path: Path) -> None:
    with pytest.raises(CivitaiError):
        CivitaiClient(token="").get_model_version(1)

    version = {
        "id": 340833,
        "model": {"type": "LORA", "name": "Skin"},
        "files": [
            {
                "name": "skin_realism_imperfections_xl.safetensors",
                "primary": True,
                "type": "Model",
            }
        ],
    }

    def transport(req, timeout=0):  # noqa: ANN001
        assert req.get_header("Authorization") == "Bearer test-token"
        assert req.get_header("User-agent") == BROWSER_USER_AGENT
        assert "test-token" not in req.full_url
        if "/model-versions/340833" in req.full_url:
            return _Resp(json.dumps(version).encode())
        if "/download/models/340833" in req.full_url:
            return _Resp(
                b"weights",
                headers={"Content-Disposition": 'attachment; filename="skin_realism_imperfections_xl.safetensors"'},
            )
        raise AssertionError(req.full_url)

    client = CivitaiClient(token="test-token", transport=transport, models_root=tmp_path)
    dest = client.download_version(340833)
    assert dest == tmp_path / "loras" / "skin_realism_imperfections_xl.safetensors"
    assert dest.read_bytes() == b"weights"
    assert _REPO_ROOT not in dest.parents


_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_civitai_checkpoint_folder_and_training_scaffold(tmp_path: Path) -> None:
    version = {
        "id": 5,
        "model": {"type": "Checkpoint"},
        "files": [{"name": "cyberrealisticXL_v100_fp16.safetensors", "type": "Model"}],
    }

    def transport(req, timeout=0):  # noqa: ANN001
        if "/model-versions/5" in req.full_url:
            return _Resp(json.dumps(version).encode())
        return _Resp(b"ckpt-bytes")

    client = CivitaiClient(token="test-token", transport=transport, models_root=tmp_path)
    dest = client.download_version(5)
    assert dest.parent.name == "checkpoints"
    avatar = load_avatar("sophie_bennet")
    payload = build_training_payload(avatar, dataset_dir="/datasets/sophie")
    assert payload["trigger_word"] == "Sophie Bennet"
    assert payload["output_filename"] == avatar.identity_lora.filename
    with pytest.raises(NotImplementedError):
        submit_training_job(payload)


class _FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[GenerationJob] = []
        self.fail_times = 0

    def generate(self, job: GenerationJob) -> GenerationResult:
        self.calls.append(job)
        if self.fail_times:
            self.fail_times -= 1
            raise RetryableGenerationError("temporary")
        job.dest.parent.mkdir(parents=True, exist_ok=True)
        job.dest.write_bytes(b"png")
        return GenerationResult(
            path=job.dest,
            job_id=f"job-{job.seed}",
            backend=self.name,
            seed=job.seed,
            preset=job.preset,
            prompt=job.positive,
            negative=job.negative,
        )


def test_batch_is_resumable_and_records_a_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    sleeps: list[float] = []
    backend = _FakeBackend()
    backend.fail_times = 1
    report = run_batch(
        "sophie_bennet",
        set_name="lifestyle",
        count=2,
        preset="fast",
        batch_id="night-1",
        backend=backend,
        seed_base=123,
        day="2026-10-15",
        max_attempts=3,
        interval_s=0.5,
        sleep=sleeps.append,
    )
    assert report.completed == 2
    assert report.failed == 0
    assert report.batch_dir == (
        tmp_path / "models_avatar" / "sophie_bennet" / "2026-10-15" / "fast" / "night-1"
    )
    # One backoff after the first failure, then the between-job interval.
    assert 1.0 in sleeps
    assert 0.5 in sleeps
    lines = (report.batch_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ok_rows = [json.loads(line) for line in lines if json.loads(line)["status"] == "ok"]
    assert len(ok_rows) == 2
    for row in ok_rows:
        assert row["prompt"].startswith("Sophie Bennet,")
        assert "nude" in row["negative"]
        assert "teen" in row["negative"]
        assert row["preset"] == "fast"
        assert row["seed"]
        assert Path(row["file"]).is_file()
        assert row["file"].endswith(".png")
    calls_after_first = len(backend.calls)
    again = run_batch(
        "sophie_bennet",
        set_name="lifestyle",
        count=2,
        preset="fast",
        batch_id="night-1",
        backend=backend,
        seed_base=999,
        day="2026-10-15",
        interval_s=5,
        sleep=sleeps.append,
    )
    assert again.skipped == 2
    assert again.completed == 0
    assert len(backend.calls) == calls_after_first


def test_single_render_does_not_reuse_seed_filename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    backend = _FakeBackend()
    first = render(
        "sophie_bennet",
        preset="fast",
        prompt=_SCENE,
        seed=99,
        backend=backend,
        day="2026-10-15",
    )
    second = render(
        "sophie_bennet",
        preset="fast",
        prompt=_SCENE,
        seed=99,
        backend=backend,
        day="2026-10-15",
    )
    assert first.path.name != "99.png"
    assert second.path.name != "99.png"
    assert first.path != second.path
    stamp, short = first.path.stem.split("_", 1)
    assert len(stamp) == 15 and stamp[8] == "T"
    assert len(short) == 8
    assert first.path.read_bytes() == b"png"
    assert second.path.read_bytes() == b"png"
    kept = tmp_path / "kept.png"
    kept.write_bytes(b"original")
    calls = len(backend.calls)
    with pytest.raises(GenerationError):
        render(
            "sophie_bennet",
            preset="fast",
            prompt=_SCENE,
            seed=99,
            backend=backend,
            dest=kept,
        )
    assert kept.read_bytes() == b"original"
    assert len(backend.calls) == calls


def test_pod_writes_part_then_replaces_and_refuses_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replaced: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def spy(src: str | Path, dst: str | Path) -> None:
        replaced.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr("models_avatar.paths.os.replace", spy)
    seen: list = []

    def transport(req, timeout=0):  # noqa: ANN001
        seen.append(req)
        url = req.full_url
        if url.endswith("/prompt"):
            return _Resp(json.dumps({"prompt_id": "pid-part"}).encode())
        if "/history/" in url:
            payload = {
                "pid-part": {
                    "outputs": {
                        "9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}
                    }
                }
            }
            return _Resp(json.dumps(payload).encode())
        if "/view?" in url:
            return _Resp(b"\x89PNG-bytes")
        raise AssertionError(url)

    backend = RunPodPodBackend(
        base_url="https://example.proxy.runpod.net",
        api_key="",
        transport=transport,
        poll_interval_s=0.01,
        timeout_s=5,
    )
    result = backend.generate(_job(tmp_path))
    assert result.path.read_bytes() == b"\x89PNG-bytes"
    assert not Path(str(result.path) + ".part").exists()
    assert len(replaced) == 1
    assert replaced[0][0].name == result.path.name + ".part"
    assert replaced[0][1] == result.path
    for req in seen:
        assert req.get_header("User-agent") == BROWSER_USER_AGENT
    with pytest.raises(GenerationError):
        backend.generate(_job(tmp_path))
    assert result.path.read_bytes() == b"\x89PNG-bytes"
    assert len(replaced) == 1


def test_single_render_writes_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    result = render(
        "sophie_bennet",
        preset="fast",
        prompt=_SCENE,
        seed=42,
        backend=_FakeBackend(),
        day="2026-10-15",
    )
    sidecar_path = result.path.with_suffix(".json")
    assert sidecar_path.is_file()
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert set(sidecar) == {"prompt", "negative", "seed", "preset", "workflow"}
    assert sidecar["prompt"].startswith("Sophie Bennet,")
    assert sidecar["seed"] == 42
    assert sidecar["preset"] == "fast"
    assert "nude" in sidecar["negative"]
    assert "wet t-shirt" in sidecar["negative"]
    assert sidecar["workflow"]["7"]["inputs"]["seed"] == 42
    assert sidecar["workflow"]["4"]["inputs"]["text"] == sidecar["prompt"]


def test_compose_used_by_prepare_mentions_both_garments() -> None:
    avatar = load_avatar("sophie_bennet")
    text = compose_positive(avatar, _SCENE)
    assert "t-shirt" in text
    assert "jeans" in text
