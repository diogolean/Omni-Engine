"""Asking for the same persona again reuses its approved rig instead of paying for a new image."""
import json

import pytest
from fastapi.testclient import TestClient

H = {"Authorization": "Bearer test-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "1")
    from products.animind_core.app import app

    return TestClient(app)


def _post(client, **body):
    r = client.post("/v1/characters", headers=H, json={"source": {"kind": "prompt", "prompt": "Sócrates"}, "artStyle": "anime", **body})
    assert r.status_code == 200, r.text
    return r.json()


def _approve(tmp_path, cid, score=90, host="http://old-host:8787"):
    path = tmp_path / "assets" / "puppets" / cid / "character.json"
    c = json.loads(path.read_text())
    c.update(generator="gemini", qa={"score": score, "costUsd": 0.0},
             imageUrl=f"{host}/v1/files/puppets/{cid}/character.png")
    path.write_text(json.dumps(c))


def test_same_persona_reuses_the_approved_rig(client, tmp_path):
    first = _post(client)
    _approve(tmp_path, first["id"])
    again = _post(client, source={"kind": "prompt", "prompt": "socrates"})  # accents and case folded
    assert again["id"] == first["id"] and again["reused"] is True
    assert again["imageUrl"].startswith("http://testserver/v1/files/puppets/")  # rebased on this server


def test_reuse_needs_qa_style_and_consent(client, tmp_path):
    first = _post(client)
    _approve(tmp_path, first["id"], score=60)
    # A single failed QA used to pay for a new rig. Keep the saved one.
    again = _post(client)
    assert again["id"] == first["id"] and again["reused"] is True
    assert "qaWarning" in again
    assert _post(client, artStyle="aquarela")["id"] != first["id"]  # other art style
    assert _post(client, reuse=False)["id"] != first["id"]  # explicit opt-out
    _approve(tmp_path, first["id"])
    assert _post(client)["id"] == first["id"]


def test_a_later_qa_failure_does_not_hide_the_passing_rig(client, tmp_path):
    passed = _post(client)
    _approve(tmp_path, passed["id"], score=92)
    failed = _post(client, reuse=False)
    _approve(tmp_path, failed["id"], score=40)
    again = _post(client)
    assert again["id"] == passed["id"] and again["qa"]["score"] == 92


def test_image_cap_reuses_instead_of_paying_past_thirty_cents(client, tmp_path):
    import json

    from products.animind_core import characters

    first = _post(client)
    _approve(tmp_path, first["id"], score=88)
    ledger = characters._spend_path("Sócrates", "anime")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"spentUsd": 0.30, "bestId": first["id"], "bestScore": 88}))
    again = _post(client, reuse=False)
    assert again["id"] == first["id"] and again["reused"] is True
    assert "0.30" in again["qaWarning"]


def test_mock_rigs_are_never_reused(client):
    a, b = _post(client), _post(client)
    assert a["id"] != b["id"] and "reused" not in b
