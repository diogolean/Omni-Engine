"""Real factory entrance is admin-only. The image call stays patched."""
from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from production.cost.pricing import GEMINI_FLASH_IMAGE_1K_USD
from products.animind_core.spend.prices import lookup
from products.animind_core.tests.test_spend_guard import URL, _limits, _postgres

H = {"Authorization": "Bearer test-key"}
TOKEN = "admin-server-token"
BODY = {"prompt": "Ada de perfil", "name": "Ada", "language": "pt", "generate": True}


@pytest.fixture()
def schema():
    _postgres()
    name = f"t_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(URL) as conn:
        conn.execute(f"CREATE SCHEMA {name}")
        conn.commit()
    yield name
    with psycopg.connect(URL) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
        conn.commit()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "1")
    monkeypatch.delenv("ANIMIND_GENERATION", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("ANIMIND_ADMIN_TOKEN", raising=False)
    from products.animind_core.app import app

    return TestClient(app)


def _called(monkeypatch) -> list[str]:
    calls: list[str] = []

    def build_rig(*_args, **_kwargs):
        calls.append("factory")
        return {"id": "rig-admin", "generator": "gemini", "qa": {"pass": True}}

    monkeypatch.setattr("products.animind_core.rig.build_rig", build_rig)
    return calls


def test_a_browser_role_does_not_open_the_factory(client, monkeypatch):
    calls = _called(monkeypatch)
    response = client.post("/v1/avatars", headers={**H, "X-Animind-User": "user_one", "X-Animind-Role": "admin"}, json=BODY)
    assert response.status_code == 403
    assert response.json()["detail"] == "admin"
    assert calls == []


def test_admin_without_a_gemini_key_stops_before_the_factory(schema, client, monkeypatch):
    _limits(monkeypatch, schema)
    monkeypatch.setenv("ANIMIND_ADMIN_TOKEN", TOKEN)
    calls = _called(monkeypatch)
    response = client.post(
        "/v1/avatars",
        headers={**H, "X-Animind-User": "user_one", "X-Animind-Admin": TOKEN, "X-Animind-Role": "user"},
        json=BODY,
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "spend:credential"
    assert calls == []


def test_admin_without_a_published_image_rate_stops_before_the_factory(schema, client, monkeypatch):
    _limits(monkeypatch, schema)
    monkeypatch.setenv("ANIMIND_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("GEMINI_API_KEY", "present-but-not-used")
    calls = _called(monkeypatch)
    response = client.post(
        "/v1/avatars",
        headers={**H, "X-Animind-User": "user_one", "X-Animind-Admin": TOKEN},
        json=BODY,
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "spend:price_unavailable"
    assert lookup("google", "gemini-2.5-flash-image") is None
    assert calls == []


def test_admin_without_detect_price_stops_before_the_factory(schema, client, monkeypatch):
    _limits(monkeypatch, schema)
    monkeypatch.setenv("ANIMIND_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("GEMINI_API_KEY", "present-but-not-used")
    monkeypatch.setenv("ANIMIND_PRICE_TABLE_JSON", json.dumps({
        "version": "image-only",
        "entries": [{
            "provider": "google",
            "model": "gemini-2.5-flash-image",
            "unit": "image",
            "microUsd": 39_000,
            "source": "test-only image rate",
            "sourceDate": "2026-11-07",
        }],
    }))
    response = client.post(
        "/v1/avatars",
        headers={**H, "X-Animind-User": "user_one", "X-Animind-Admin": TOKEN},
        json=BODY,
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "spend:price_unavailable"


def test_admin_adapter_runs_the_canonical_fixture_pipeline(schema, client, monkeypatch, tmp_path):
    _limits(
        monkeypatch,
        schema,
        ANIMIND_SPEND_MAX_IMAGES="18",
        ANIMIND_SPEND_MAX_RETRIES="2",
    )
    monkeypatch.setenv("ANIMIND_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("GEMINI_API_KEY", "present-but-not-used")
    micro = int(round(GEMINI_FLASH_IMAGE_1K_USD * 1_000_000))
    monkeypatch.setenv("ANIMIND_PRICE_TABLE_JSON", json.dumps({
        "version": "gemini-flash-image-1k",
        "entries": [
            {
                "provider": "google",
                "model": "gemini-2.5-flash-image",
                "unit": "image",
                "microUsd": micro,
                "source": "production.cost.pricing.GEMINI_FLASH_IMAGE_1K_USD",
                "sourceDate": "2026-11-07",
            },
            {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "unit": "tokens",
                "inputMissMicroPerMillion": 1,
                "inputHitMicroPerMillion": 1,
                "outputMicroPerMillion": 1,
                "source": "test-only fixture rate",
                "sourceDate": "2026-11-07",
            },
        ],
    }))
    response = client.post(
        "/v1/avatars",
        headers={**H, "X-Animind-User": "user_one", "X-Animind-Admin": TOKEN},
        json=BODY,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == "animind.puppet.v3"
    assert body["status"] == "review_required"
    assert body["ready"] is False
    assert "provider_fixture" in body["defects"]
    assert "view_profile_missing" in body["defects"]
    assert set(body["views"]) == {"front", "left", "right"}
    for view in body["views"].values():
        layers = {layer["name"] for layer in view["layers"]}
        assert {"torso", "neck", "head", "eyes", "eyes_half", "eyes_closed", "brows"} <= layers
        assert set(view["visemes"]) == set("XABCDEFGH")
        assert set(view["expressions"]) == {
            "neutral", "happy", "angry", "sad", "surprised", "thoughtful", "ironic",
        }
        assert view["eyeRig"]["maxShift"]
        assert view["browModel"]
    assert body["stages"]["pipeline"] == "products.animind_core.characters.create_character"
    assert body["callPlan"]["image"]["maximum"] == 18
    assert body["callPlan"]["detectFeatures"]["maximum"] == 6
    assert body["callPlan"]["providerInternalRetries"] == 0
    assert body["scenario"]["embedded"] is False
    assert body["provenance"]["realImage"] is False
    assert body["usage"]["totals"]["estimatedMicro"] is None
    assert list((tmp_path / "users").rglob("character.json"))
    assert list((tmp_path / "users").rglob("rig.json"))
    assert lookup("google", "gemini-2.5-flash-image").micro_usd == micro
