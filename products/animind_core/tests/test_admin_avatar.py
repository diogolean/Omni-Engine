"""Real factory entrance is admin-only. The image call stays patched."""
from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from products.animind_core.spend.prices import ImageRate, TokenRate, lookup
from products.animind_core.tests.test_spend_guard import URL, _limits, _postgres

H = {"Authorization": "Bearer test-key"}
TOKEN = "admin-server-token"
EXEC = {"Idempotency-Key": "avatar-test-001", "X-Request-Id": "avatar-request-001"}
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
        headers={**H, **EXEC, "X-Animind-User": "user_one", "X-Animind-Admin": TOKEN, "X-Animind-Role": "user"},
        json=BODY,
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "spend:credential"
    assert calls == []


def test_paid_factory_requires_explicit_idempotency_and_request_id(client, monkeypatch):
    monkeypatch.setenv("ANIMIND_ADMIN_TOKEN", TOKEN)
    calls = _called(monkeypatch)
    response = client.post(
        "/v1/avatars",
        headers={**H, "X-Animind-User": "user_one", "X-Animind-Admin": TOKEN},
        json=BODY,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "spend:idempotency_required"
    response = client.post(
        "/v1/avatars",
        headers={
            **H,
            "Idempotency-Key": "avatar-test-001",
            "X-Animind-User": "user_one",
            "X-Animind-Admin": TOKEN,
        },
        json=BODY,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "spend:request_id_required"
    assert calls == []


def test_official_gemini_rates_are_composite_and_versioned(schema, client, monkeypatch):
    _limits(monkeypatch, schema)
    monkeypatch.setenv("ANIMIND_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("GEMINI_API_KEY", "present-but-not-used")
    calls = _called(monkeypatch)
    response = client.post(
        "/v1/avatars",
        headers={**H, **EXEC, "X-Animind-User": "user_one", "X-Animind-Admin": TOKEN},
        json=BODY,
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "spend:approval"
    image = lookup("google", "gemini-2.5-flash-image")
    detect = lookup("google", "gemini-2.5-flash")
    assert isinstance(image, ImageRate)
    assert image.input_per_million == 300_000
    assert image.output_per_image == 39_000
    assert isinstance(detect, TokenRate)
    assert detect.output_per_million == 2_500_000
    assert image.source == "https://ai.google.dev/gemini-api/docs/pricing"
    assert image.source_date == "2026-11-08"
    assert calls == []


def test_admin_without_detect_price_stops_before_the_factory(schema, client, monkeypatch):
    _limits(monkeypatch, schema)
    monkeypatch.setenv("ANIMIND_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("GEMINI_API_KEY", "present-but-not-used")
    monkeypatch.setattr("products.animind_core.admin_avatar.DETECT_MODEL", "unpriced-model")
    response = client.post(
        "/v1/avatars",
        headers={**H, **EXEC, "X-Animind-User": "user_one", "X-Animind-Admin": TOKEN},
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
    monkeypatch.setenv("ANIMIND_AVATAR_SPEND_APPROVED", "1")
    response = client.post(
        "/v1/avatars",
        headers={**H, **EXEC, "X-Animind-User": "user_one", "X-Animind-Admin": TOKEN},
        json=BODY,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == "animind.puppet.v3"
    assert body["status"] == "review_required"
    assert body["ready"] is False
    assert body["review_required"] is True
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
        assert view["anchors"]["coordinateSpace"] == "canvas_px"
        assert view["anchors"]["neck"]
        assert view["anchors"]["mouth"]
    assert all(report["pass"] for report in body["alignment"].values())
    assert body["stages"]["pipeline"] == "products.animind_core.characters.create_character"
    assert "products.animind_core.rig.build_mock_rig" in body["stages"]["actualModules"]
    assert "products.animind_core.rig.build_rig" not in body["stages"]["actualModules"]
    assert body["qa"]["costUsd"] is None
    assert body["callPlan"]["image"]["maximum"] == 18
    assert body["callPlan"]["detectFeatures"]["maximum"] == 6
    assert body["callPlan"]["providerInternalRetries"] == 0
    assert body["scenario"]["embedded"] is False
    assert body["provenance"]["realImage"] is False
    assert body["usage"]["totals"]["estimatedMicro"] is None
    assert list((tmp_path / "users").rglob("character.json"))
    assert list((tmp_path / "users").rglob("rig.json"))
    assert lookup("google", "gemini-2.5-flash-image").micro_usd == 39_000
    loaded = client.get(
        f"/v1/avatars/{body['id']}",
        headers={**H, "X-Animind-User": "user_one"},
    )
    assert loaded.status_code == 200
    assert loaded.json() == body
    replay = client.post(
        "/v1/avatars",
        headers={**H, **EXEC, "X-Animind-User": "user_one", "X-Animind-Admin": TOKEN},
        json=BODY,
    )
    assert replay.status_code == 200
    assert replay.json() == body
    hidden = client.get(
        f"/v1/avatars/{body['id']}",
        headers={**H, "X-Animind-User": "different_user"},
    )
    assert hidden.status_code == 404


def test_preflight_is_read_only_and_accepts_admin_or_super(schema, client, monkeypatch):
    _limits(
        monkeypatch,
        schema,
        ANIMIND_SPEND_MAX_IMAGES="18",
        ANIMIND_SPEND_MAX_RETRIES="2",
    )
    monkeypatch.setenv("ANIMIND_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("ANIMIND_SUPER_ADMIN_TOKEN", "super-admin-token")
    monkeypatch.setenv("ANIMIND_AVATAR_SPEND_APPROVED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "present-but-not-used")
    calls = _called(monkeypatch)
    for token in (TOKEN, "super-admin-token"):
        response = client.post(
            "/v1/admin/avatars/preflight",
            headers={**H, "X-Animind-Admin": token},
        )
        assert response.status_code == 200
        assert response.json()["ready"] is True
        assert response.json()["providerCall"] is False
        assert response.json()["characters"] == 1
        assert response.json()["limits"]["requestCeilingCents"] > 0
    assert calls == []


def test_spend_book_is_super_admin_only(schema, client, monkeypatch):
    _limits(monkeypatch, schema)
    monkeypatch.setenv("ANIMIND_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("ANIMIND_SUPER_ADMIN_TOKEN", "super-admin-token")
    denied = client.get("/v1/admin/spend", headers={**H, "X-Animind-Admin": TOKEN})
    assert denied.status_code == 403
    allowed = client.get(
        "/v1/admin/spend",
        headers={**H, "X-Animind-Admin": "super-admin-token"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["authority"] == "animind_core"


def test_dry_run_post_get_returns_puppet_v3(client, monkeypatch, tmp_path):
    monkeypatch.setenv("ANIMIND_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("ANIMIND_DRY_RUN", "1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANIMIND_AVATAR_SPEND_APPROVED", raising=False)
    response = client.post(
        "/v1/avatars",
        headers={**H, **EXEC, "X-Animind-User": "user_one", "X-Animind-Admin": TOKEN},
        json={**BODY, "dryRun": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema"] == "animind.puppet.v3"
    assert body["dryRun"] is True
    assert body["providerCall"] is False
    assert body["generator"] == "mock"
    assert "products.animind_core.rig.build_mock_rig" in body["stages"]["actualModules"]
    for view in body["views"].values():
        layers = {layer["name"] for layer in view["layers"]}
        assert {"torso", "neck", "head", "eyes", "brows"} <= layers
    loaded = client.get(
        f"/v1/avatars/{body['id']}",
        headers={**H, "X-Animind-User": "user_one"},
    )
    assert loaded.status_code == 200
    assert loaded.json() == body


def test_dry_run_body_without_server_flag_is_refused(client, monkeypatch):
    monkeypatch.setenv("ANIMIND_ADMIN_TOKEN", TOKEN)
    monkeypatch.delenv("ANIMIND_DRY_RUN", raising=False)
    response = client.post(
        "/v1/avatars",
        headers={**H, **EXEC, "X-Animind-User": "user_one", "X-Animind-Admin": TOKEN},
        json={**BODY, "dryRun": True},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "dry_run_disabled"
