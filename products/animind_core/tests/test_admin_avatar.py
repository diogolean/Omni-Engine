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


def test_admin_reaches_the_aiwake_factory_when_the_rate_is_published(schema, client, monkeypatch):
    _limits(monkeypatch, schema)
    monkeypatch.setenv("ANIMIND_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("GEMINI_API_KEY", "present-but-not-used")
    micro = int(round(GEMINI_FLASH_IMAGE_1K_USD * 1_000_000))
    monkeypatch.setenv("ANIMIND_PRICE_TABLE_JSON", json.dumps({
        "version": "gemini-flash-image-1k",
        "entries": [{
            "provider": "google",
            "model": "gemini-2.5-flash-image",
            "unit": "image",
            "microUsd": micro,
            "source": "production.cost.pricing.GEMINI_FLASH_IMAGE_1K_USD",
            "sourceDate": "2026-11-07",
        }],
    }))
    calls = _called(monkeypatch)
    response = client.post(
        "/v1/avatars",
        headers={**H, "X-Animind-User": "user_one", "X-Animind-Admin": TOKEN},
        json=BODY,
    )
    assert response.status_code == 200, response.text
    assert response.json()["generator"] == "gemini"
    assert calls == ["factory"]
    assert lookup("google", "gemini-2.5-flash-image").micro_usd == micro
