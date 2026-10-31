"""A depleted Gemini key (402) must not break character creation: mock art, with the reason."""
import pytest
from fastapi.testclient import TestClient

H = {"Authorization": "Bearer test-key"}
DEPLETED = "402 RESOURCE_EXHAUSTED. Your prepayment credits are depleted."


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    monkeypatch.delenv("ANIMIND_FORCE_MOCK", raising=False)
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "1")
    from production.formats.economic_reel.artefact import provider_health
    from products.animind_core import rig

    provider_health.reset_providers()

    def no_credit(*_a, **_k):
        provider_health.mark_dead("gemini", DEPLETED)
        raise RuntimeError(DEPLETED)

    monkeypatch.setattr(rig, "build_rig", no_credit)
    from products.animind_core.app import app

    yield TestClient(app)
    provider_health.reset_providers()


def _post(client, prompt="Machado de Assis"):
    return client.post("/v1/characters", headers=H, json={"source": {"kind": "prompt", "prompt": prompt}, "artStyle": "anime"})


def test_depleted_key_falls_back_to_mock_art(client):
    assert client.get("/v1/health").json()["generator"] == "gemini"
    r = _post(client)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["generator"] == "mock" and "402" in c["generatorNote"]
    assert set(c["views"]) == {"front", "left", "right"}
    h = client.get("/v1/health").json()  # the process now knows: no second paid attempt, badge says mock
    assert h["generator"] == "mock" and "depleted" in h["generatorNote"]
    again = _post(client, "Clarice Lispector").json()
    assert again["generator"] == "mock" and "Gemini indisponível" in again["generatorNote"]


def test_other_errors_still_fail_loudly(client, monkeypatch):
    from products.animind_core import rig

    def boom(*_a, **_k):
        raise RuntimeError("matte crashed")

    monkeypatch.setattr(rig, "build_rig", boom)
    with pytest.raises(RuntimeError, match="matte crashed"):
        _post(client)
