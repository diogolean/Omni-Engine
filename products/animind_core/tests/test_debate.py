"""A fixture debate is owned, and the other user cannot read it."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from products.animind_core import debate, owner

SECRET_NAME = "Albertina Segredo"
OTHER_NAME = "Bento Oculto"
SECRET_THEME = "verdade secreta"
VERIFIED = "A verdade secreta sustenta o que penso."
HIDDEN = "A verdade secreta que nao pode sair."
H = {"Authorization": "Bearer test-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "1")
    sheets = {
        SECRET_NAME: {
            "quotes": [
                {
                    "id": "q1", "text": VERIFIED, "source": "Livro", "sourceKind": "primary",
                    "url": "https://pt.wikiquote.org/wiki/Albertina", "verification": "verified",
                },
                {
                    "id": "q2", "text": HIDDEN, "source": "Livro", "sourceKind": "primary",
                    "url": "https://pt.wikiquote.org/wiki/Albertina", "verification": "not_found",
                },
            ],
        },
        OTHER_NAME: {
            "quotes": [
                {
                    "id": "q1", "text": "O mar nao entra neste tema.", "source": "Carta",
                    "sourceKind": "primary", "url": "https://pt.wikiquote.org/wiki/Bento",
                    "verification": "verified",
                },
            ],
        },
    }

    def load(name, lang="pt-BR", fetch=True):  # noqa: ARG001
        return sheets.get(name) or {"quotes": []}

    monkeypatch.setattr("products.animind_core.personas.load", load)
    from products.animind_core.app import app

    return TestClient(app)


def test_owned_debate_hides_the_script_from_everyone_else(client, tmp_path):
    own = {**H, "X-Animind-User": "user_one"}
    other = {**H, "X-Animind-User": "user_two"}
    created = client.post("/v1/debates", headers=own, json={
        "personas": [SECRET_NAME, OTHER_NAME],
        "theme": SECRET_THEME,
        "language": "pt",
    })
    assert created.status_code == 200
    body = created.json()
    debate_id = body["id"]
    assert body["writer"] == "fixture"
    assert body["llm"] == {"provider": "fixture", "model": "fixture", "live": False}
    assert body["language"] == "pt"
    assert body["lines"][0]["kind"] == "quote"
    assert body["lines"][0]["text"] == VERIFIED
    assert body["lines"][0]["quote"]["id"] == "q1"
    assert body["lines"][0]["quote"]["verification"] == "verified"
    assert body["lines"][1] == {
        "speaker": 1, "name": OTHER_NAME, "text": None, "kind": "fixture", "quote": None,
    }
    assert HIDDEN not in created.text
    assert "_owner" not in body
    assert "users/" not in created.text
    stored = debate._DEBATES[debate_id]
    assert stored["_engine"]["spoken"] == ["fixture.", "fixture."]
    assert stored["_engine"]["provider"] == "fixture"
    try:
        seen = client.get(f"/v1/debates/{debate_id}", headers=own)
        assert seen.status_code == 200
        assert seen.json()["lines"][0]["text"] == VERIFIED
        for headers in (other, H):
            denied = client.get(f"/v1/debates/{debate_id}", headers=headers)
            assert denied.status_code == 404
            assert denied.json() == {"detail": "debate not found"}
            assert SECRET_NAME not in denied.text
            assert OTHER_NAME not in denied.text
            assert SECRET_THEME not in denied.text
            assert VERIFIED not in denied.text
            assert HIDDEN not in denied.text
            assert "users/" not in denied.text
        relative = debate.file_rel(owner.accept_user("user_one"), debate_id)
        assert (tmp_path / relative).is_file()
        assert client.get(f"/v1/files/{relative}", headers=own).status_code == 200
        for headers in (other, H):
            hidden = client.get(f"/v1/files/{relative}", headers=headers)
            assert hidden.status_code == 404
            assert hidden.json() == {"detail": "not found"}
            assert VERIFIED not in hidden.text
            assert SECRET_NAME not in hidden.text
        emailed = client.get(f"/v1/debates/{debate_id}", headers={**H, "X-Animind-User": "ada@example.com"})
        assert emailed.status_code == 400
        assert "ada@example.com" not in emailed.text
        assert VERIFIED not in emailed.text
    finally:
        debate._DEBATES.pop(debate_id, None)


def test_anonymous_debate_is_not_readable_by_a_named_user(client):
    created = client.post("/v1/debates", headers=H, json={
        "personas": ["Ada", "Beto"],
        "theme": "mar",
        "language": "en",
    })
    assert created.status_code == 200
    debate_id = created.json()["id"]
    assert created.json()["language"] == "en"
    assert created.json()["llm"]["live"] is False
    try:
        assert client.get(f"/v1/debates/{debate_id}", headers=H).status_code == 200
        named = client.get(f"/v1/debates/{debate_id}", headers={**H, "X-Animind-User": "user_one"})
        assert named.status_code == 404
        assert named.json() == {"detail": "debate not found"}
    finally:
        debate._DEBATES.pop(debate_id, None)


def test_one_persona_is_refused(client):
    refused = client.post("/v1/debates", headers=H, json={"personas": ["Ada"], "theme": "mar", "language": "pt"})
    assert refused.status_code == 400
    assert refused.json() == {"detail": "personas"}


def test_legacy_sheet_quote_still_supports_a_line():
    sheet = {"quotes": [{
        "id": "q1", "text": "A verdade secreta fica.", "source": "Livro", "sourceKind": "primary",
        "url": "https://pt.wikiquote.org/wiki/Ada",
    }]}
    lines = debate._lines(["Ada", "Beto"], [sheet, {"quotes": []}], "verdade secreta")
    assert lines[0]["kind"] == "quote"
    assert lines[0]["text"] == "A verdade secreta fica."
    assert "verification" not in lines[0]["quote"]
    assert lines[1]["kind"] == "fixture"
    saved = json.dumps(lines)
    assert "fixture." not in saved
