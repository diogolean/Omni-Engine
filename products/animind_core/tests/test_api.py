import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    from products.animind_core.app import app

    return TestClient(app)


H = {"Authorization": "Bearer test-key"}


def test_rejects_missing_key(client):
    assert client.post("/v1/characters", json={}).status_code == 401


def test_upload_needs_rights(client):
    r = client.post("/v1/characters", headers=H, json={"source": {"kind": "upload", "imageDataUrl": "x"}})
    assert r.status_code == 400


def test_character_contract_shape(client):
    r = client.post("/v1/characters", headers=H, json={"source": {"kind": "prompt", "prompt": "owl professor"}})
    assert r.status_code == 200
    c = r.json()
    names = {layer["name"] for layer in c["layers"]}
    assert {"body", "head", "eyes", "eyes_closed", "mouth_rest", "mouth_A", "mouth_E", "mouth_O", "mouth_M"} <= names
    assert set(c["views"]) == {"facing_left", "facing_right", "facing_front"}
    assert len(c["views"]["facing_left"]["visemes"]) == 9
    path = c["layers"][0]["url"].split("/v1/files/")[1]
    assert client.get(f"/v1/files/{path}").headers["content-type"] == "image/png"


def test_files_cannot_escape(client):
    assert client.get("/v1/files/../../etc/passwd").status_code == 404


def test_unknown_job(client):
    assert client.get("/v1/jobs/nope", headers=H).status_code == 404


def test_default_script_debate():
    from products.animind_core.render import default_script

    lines = default_script("debate", "IA", 2, "pt-BR")
    assert {s for s, _ in lines} == {0, 1}
