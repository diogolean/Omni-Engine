# -*- coding: utf-8 -*-
"""Shared memory client with the server mocked."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from modules.memory.ancient import (
    ANCIENT_KNOWLEDGE_REEL_ANGLES,
    ANCIENT_KNOWLEDGE_REEL_VISUALS,
    load_visual_memory,
)
from modules.memory.client import MemoryClient, MemoryService
from modules.memory.config import load_settings
from modules.memory.gate import require_production_memory
from modules.memory.namespaces import memory_namespace
from modules.memory.transport import HttpTransport, SdkTransport, open_transport
from modules.memory.wsl import ensure_supermemory_running, reset_start_attempt


class FakeTransport:
    def __init__(self, *, up: bool = False) -> None:
        self.up = up
        self.adds: list[dict[str, Any]] = []
        self.health_calls = 0
        self.fail_adds = 0

    def health(self, timeout_s: float) -> bool:
        self.health_calls += 1
        return self.up

    def add(self, **kwargs: Any) -> bool:
        if self.fail_adds:
            self.fail_adds -= 1
            return False
        self.adds.append(kwargs)
        return True

    def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def list_documents(self, **kwargs: Any) -> list[Any]:
        return []


class FakeResponse:
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status_code = status
        self._payload = payload
        self.content = b"{}"

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str] | None, dict[str, Any] | None]] = []

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> FakeResponse:
        self.calls.append(("GET", url, headers, None))
        return FakeResponse(200, {"status": "ok"})

    def post(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> FakeResponse:
        self.calls.append(("POST", url, headers, json))
        if url.endswith("/v3/search"):
            return FakeResponse(
                200,
                {"results": [{"memory": "hit", "similarity": 0.2, "metadata": {}}]},
            )
        return FakeResponse(200, {"id": "doc"})


class _Bridge:
    def __init__(self, *, active: bool, duplicate: bool = False) -> None:
        self.is_active = active
        self.base_url = "http://supermemory:6767"
        self.history_container = "aiwake_production_history"
        self.duplicate = duplicate

    def check_topic_similarity(self, topic: str, target_model: str = "") -> bool:
        return self.duplicate


def _client(tmp_path: Path, transport: FakeTransport, **kwargs: Any) -> MemoryClient:
    settings = load_settings(
        base_url="http://supermemory:6767",
        api_key="sm_test",
        disabled=False,
        health_timeout_s=0.05,
    )
    service = MemoryService(settings, transport=transport)
    return MemoryClient(
        kwargs.pop("namespace", "aiwake_production_history"),
        channel=kwargs.pop("channel", "aiwake"),
        project="aiwake",
        service=service,
        ledger_dir=tmp_path,
        **kwargs,
    )


def test_namespace_names_project_channel_purpose() -> None:
    assert memory_namespace("ancient_knowledge", "reel", "angles") == ANCIENT_KNOWLEDGE_REEL_ANGLES
    assert (
        memory_namespace("Ancient Knowledge", "reel", "visuals")
        == ANCIENT_KNOWLEDGE_REEL_VISUALS
    )
    assert memory_namespace("aiwake", "production", "history") == "aiwake_production_history"


def test_offline_add_queues_and_replays_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr("modules.memory.client.time.monotonic", lambda: clock["now"])
    transport = FakeTransport(up=False)
    client = _client(tmp_path, transport)
    first = client.add("Who built you?", custom_id="sess-1", kind="approved_session")
    second = client.add("Who built you? revised", custom_id="sess-1", kind="approved_session")
    assert first.stored_locally is True
    assert first.remote is False
    assert first.queued is True
    assert second.queued is True
    assert len(client.ledger.pending()) == 1
    assert client.ledger.pending()[0]["content"] == "Who built you? revised"
    assert client.is_duplicate("Who built you?") is True
    assert transport.adds == []

    transport.up = True
    clock["now"] = 1_061.0
    assert client.health() is True
    assert [item["custom_id"] for item in transport.adds] == ["sess-1"]
    assert client.ledger.pending() == []

    clock["now"] = 1_130.0
    assert client.health() is True
    assert len(transport.adds) == 1


def test_failed_remote_write_is_resent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = {"now": 10.0}
    monkeypatch.setattr("modules.memory.client.time.monotonic", lambda: clock["now"])
    transport = FakeTransport(up=True)
    transport.fail_adds = 1
    client = _client(tmp_path, transport)
    result = client.add("caption rule", custom_id="aiwake-system-rule", kind="system_rule")
    assert result.remote is False
    assert result.queued is True
    assert transport.adds == []
    clock["now"] = 80.0
    assert client.health() is True
    assert [item["custom_id"] for item in transport.adds] == ["aiwake-system-rule"]
    assert client.ledger.pending() == []


def test_health_is_cached_for_sixty_seconds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = {"now": 50.0}
    monkeypatch.setattr("modules.memory.client.time.monotonic", lambda: clock["now"])
    transport = FakeTransport(up=True)
    client = _client(tmp_path, transport)
    assert client.health() is True
    assert client.health() is True
    assert transport.health_calls == 1
    clock["now"] = 111.0
    assert client.health() is True
    assert transport.health_calls == 2


def test_http_client_uses_configured_host_and_bearer() -> None:
    session = FakeSession()
    http = HttpTransport(
        "http://supermemory:6767",
        "sm_test",
        timeout_s=1.5,
        session=session,
    )
    assert http.health(1.5) is True
    assert http.add(
        content="rule",
        container_tag="aiwake_system_rules",
        metadata={"kind": "system_rule", "project": "aiwake"},
        custom_id="aiwake-system-rule",
    )
    method, url, headers, body = session.calls[-1]
    assert method == "POST"
    assert url == "http://supermemory:6767/v3/documents"
    assert headers is not None and headers["Authorization"] == "Bearer sm_test"
    assert body is not None
    assert body["containerTag"] == "aiwake_system_rules"
    assert body["customId"] == "aiwake-system-rule"
    assert "localhost" not in url


def test_sdk_transport_receives_any_host(monkeypatch: pytest.MonkeyPatch) -> None:
    import supermemory

    assert supermemory.__version__ == "3.62.0"
    captured: dict[str, str] = {}

    def fake_build(settings: Any) -> Any:
        captured["base_url"] = settings.base_url
        captured["api_key"] = settings.api_key
        raise RuntimeError("do not connect")

    monkeypatch.setattr("modules.memory.transport.build_sdk_client", fake_build)
    settings = load_settings(
        base_url="http://supermemory:6767",
        api_key="sm_secret",
        disabled=False,
    )
    transport = open_transport(settings)
    assert isinstance(transport, SdkTransport)
    with pytest.raises(RuntimeError):
        transport.client()
    assert captured == {"base_url": "http://supermemory:6767", "api_key": "sm_secret"}


def test_visual_memory_is_read_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
    path = tmp_path / "ancient_knowledge" / "_review" / "visual_memory.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {
            "kind": "angle",
            "topic": "roman-aqueducts",
            "text": "Roman aqueducts carried water across the valley.",
            "qa_status": "pass",
        },
        {
            "kind": "visual",
            "topic": "roman-aqueducts",
            "frame": "stone arches at dusk",
            "qa_status": "pass",
        },
    ]
    original = "".join(json.dumps(row) + "\n" for row in rows)
    path.write_text(original, encoding="utf-8")
    assert len(load_visual_memory("ancient_knowledge")) == 2

    transport = FakeTransport(up=False)
    settings = load_settings(base_url="http://supermemory:6767", api_key="sm_test", disabled=True)
    service = MemoryService(settings, transport=transport)
    angles = MemoryClient(
        ANCIENT_KNOWLEDGE_REEL_ANGLES,
        channel="ancient_knowledge",
        project="ancient_knowledge",
        purpose="angles",
        service=service,
        ledger_dir=tmp_path / "ledger",
    )
    visuals = MemoryClient(
        ANCIENT_KNOWLEDGE_REEL_VISUALS,
        channel="ancient_knowledge",
        project="ancient_knowledge",
        purpose="visuals",
        service=service,
        ledger_dir=tmp_path / "ledger",
    )
    angle_hits = angles.search("Roman aqueducts water valley")
    visual_hits = visuals.search("stone arches dusk")
    assert angle_hits and angle_hits[0]["metadata"]["kind"] == "angle"
    assert angle_hits[0]["metadata"]["qa_status"] == "pass"
    assert visual_hits and visual_hits[0]["metadata"]["kind"] == "visual"
    assert angles.search("unrelated pottery kiln") == []
    angles.add("a new angle", custom_id="new-angle", kind="angle")
    assert path.read_text(encoding="utf-8") == original


def test_soft_gate_allows_offline_and_strict_mode_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPERMEMORY_REQUIRED", raising=False)
    assert require_production_memory(_Bridge(active=False), "topic")
    monkeypatch.setenv("SUPERMEMORY_REQUIRED", "1")
    with pytest.raises(RuntimeError, match="SUPERMEMORY_REQUIRED"):
        require_production_memory(_Bridge(active=False), "topic")
    monkeypatch.delenv("SUPERMEMORY_REQUIRED", raising=False)
    with pytest.raises(ValueError, match="repetitive"):
        require_production_memory(_Bridge(active=False, duplicate=True), "topic")


def test_wsl_launch_is_windows_only_and_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    import modules.memory.wsl as wsl_module

    launched: list[list[str]] = []
    monkeypatch.setattr(wsl_module, "_port_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        wsl_module.subprocess,
        "Popen",
        lambda command, **kwargs: launched.append(command),
    )

    reset_start_attempt()
    monkeypatch.delenv("SUPERMEMORY_AUTOSTART_WSL", raising=False)
    monkeypatch.setattr(wsl_module.sys, "platform", "win32")
    assert ensure_supermemory_running(wait_s=2.0) is False
    assert launched == []

    reset_start_attempt()
    monkeypatch.setenv("SUPERMEMORY_AUTOSTART_WSL", "1")
    monkeypatch.setattr(wsl_module.sys, "platform", "linux")
    assert ensure_supermemory_running(wait_s=2.0) is False
    assert launched == []


def test_settings_key_is_not_localhost_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPERMEMORY_API_KEY", raising=False)
    monkeypatch.setattr(
        "modules.memory.config._key_file",
        lambda: "sm_from_file",
    )
    settings = load_settings(base_url="http://supermemory:6767")
    assert settings.base_url == "http://supermemory:6767"
    assert settings.api_key == "sm_from_file"
    assert settings.local_server is False
    explicit = load_settings(base_url="http://supermemory:6767", api_key="sm_env")
    assert explicit.api_key == "sm_env"
