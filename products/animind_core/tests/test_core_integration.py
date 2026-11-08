"""The six drafts, exercised as one flow. No provider call."""
from __future__ import annotations

import time
import uuid

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient

from products.animind_core import avatar_request, owner, personas
from products.animind_core.tests.test_spend_guard import URL, _limits, _postgres

H = {"Authorization": "Bearer test-key"}


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


def test_verified_persona_avatar_and_spend_stay_with_the_owner(schema, monkeypatch, tmp_path):
    _limits(monkeypatch, schema)
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "1")
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    calls: list[str] = []
    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        lambda *_args, **_kwargs: calls.append("image"),
    )
    monkeypatch.setattr(
        "products.animind_core.image_models.httpx.post",
        lambda *_args, **_kwargs: calls.append("image-http"),
    )
    monkeypatch.setattr(
        "products.animind_core.llm_providers.httpx.post",
        lambda *_args, **_kwargs: calls.append("llm"),
    )
    user = owner.accept_user("user_one")
    path = personas.cache_path("Ada", "pt-BR", user)
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({
        "name": "Ada",
        "lang": "pt-BR",
        "found": True,
        "version": personas.SHEET_VERSION,
        "fetchedAt": int(time.time()),
        "fixture": "labeled",
        "quotes": [{
            "id": "q1",
            "text": "A labeled fixture quote.",
            "source": "labeled fixture",
            "sourceKind": "primary",
            "url": "https://pt.wikiquote.org/wiki/Ada",
            "verification": "verified",
        }],
    }), encoding="utf-8")
    from products.animind_core.app import app

    client = TestClient(app)
    own = {**H, "X-Animind-User": "user_one", "X-Request-Id": "req_integration1"}
    sheet = client.get("/v1/personas/sheet", params={"name": "Ada", "lang": "pt-BR"}, headers=own)
    assert sheet.status_code == 200
    assert "owner" not in sheet.json()
    assert user not in sheet.text
    assert sheet.json()["fixture"] == "labeled"
    assert sheet.json()["quotes"][0]["verification"] == "verified"
    assert sheet.json()["quotes"][0]["source"] == "labeled fixture"
    hidden_sheet = client.get(
        "/v1/personas/sheet", params={"name": "Ada", "lang": "pt-BR"},
        headers={**H, "X-Animind-User": "user_two"},
    )
    assert hidden_sheet.status_code == 404
    assert hidden_sheet.json() == {"detail": "not found"}
    assert user not in hidden_sheet.text
    assert "labeled fixture" not in hidden_sheet.text
    assert "users/" not in hidden_sheet.text
    plan = client.post("/v1/avatars/plan", headers=own, json={"prompt": "Discurso de Ada", "language": "pt"})
    assert plan.status_code == 200
    assert plan.json()["intent"] == "monologue"
    assert plan.json()["personas"] == ["Ada"]
    assert plan.json()["needsChoice"] is False
    assert plan.json()["usage"]["schema"] == "animind.usage.v1"
    assert plan.json()["usage"]["totals"]["estimatedMicro"] is None
    portrait = client.post("/v1/portraits/plan", headers=own, json={"requestedResolution": "1k"})
    assert portrait.status_code == 200
    assert portrait.json()["usage"]["schema"] == "animind.usage.v1"
    assert portrait.json()["usage"]["totals"]["reportedMicro"] is None
    assert "usdPerImage" not in portrait.text
    made = client.post("/v1/avatars", headers=own, json={
        "prompt": "Discurso de Ada", "language": "pt", "requestedResolution": "1k",
    })
    assert made.status_code == 200
    job = made.json()
    assert job["events"][-1]["blockedBefore"] == "image generation"
    assert job["events"][-1]["error"]["code"] == "price_unavailable"
    assert job["events"][-1]["error"]["recoverable"] is True
    assert job["providerCalls"] == 0
    usage = job["usage"]
    assert usage["schema"] == "animind.usage.v1"
    assert usage["totals"]["estimatedMicro"] is None
    assert usage["totals"]["reportedMicro"] is None
    assert all(call.get("model") is None for call in usage["calls"])
    published = plan.text + made.text
    assert "production/cost" not in published
    assert "usdPerImage" not in published
    assert "gemini" not in published.lower()
    assert "deepseek" not in published.lower()
    assert calls == []
    seen = client.get(f"/v1/avatars/{job['id']}", headers=own)
    assert seen.status_code == 200
    assert seen.json()["events"] == job["events"]
    assert seen.json()["usage"] == usage
    hidden = client.get(f"/v1/avatars/{job['id']}", headers={**H, "X-Animind-User": "user_two"})
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "avatar not found"}
    assert "Ada" not in hidden.text
    assert "fixture quote" not in hidden.text
    assert "users/" not in hidden.text
    with psycopg.connect(URL) as conn:
        conn.execute(f"SET search_path TO {schema}")
        rows = conn.execute(
            "SELECT user_id, request_id, attempt, estimated_micro, reported_micro, status "
            "FROM animind_core_spend_entries"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == user
    assert rows[0][1] == "req_integration1"
    assert rows[0][2] == 1
    assert rows[0][3] is None and rows[0][3] != 0
    assert rows[0][4] is None
    assert rows[0][5] == "blocked"
    avatar_request._JOBS.clear()


VERIFIED = "A verdade secreta sustenta o que penso."
INVENTED = "Uma frase inventada sem pagina fonte."
PAGE = "Pagina fixture. A verdade secreta sustenta o que penso. Fim da pagina fixture."
SOURCE = "https://pt.wikiquote.org/wiki/Ada"


def test_labeled_fixture_walks_sheet_plan_and_render_for_one_owner(monkeypatch, tmp_path):
    """Offline fixture, not a production render. No provider is called."""
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    calls: list[str] = []
    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        lambda *_a, **_k: calls.append("image"),
    )
    monkeypatch.setattr(
        "products.animind_core.llm_providers.httpx.post",
        lambda *_a, **_k: calls.append("llm"),
    )
    user = owner.accept_user("user_one")
    raw = [
        {"text": VERIFIED, "source": "Livro fixture", "sourceKind": "primary", "url": SOURCE},
        {"text": INVENTED, "source": "Livro fixture", "sourceKind": "primary", "url": SOURCE},
    ]
    checked = personas._limit(personas.verify_quotes(raw, {SOURCE: PAGE}))
    assert personas.text_on_page(VERIFIED, PAGE)
    assert not personas.text_on_page(INVENTED, PAGE)
    assert checked[0]["verification"] == "verified"
    assert checked[1]["verification"] == "not_found"
    path = personas.cache_path("Ada", "pt-BR", user)
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({
        "name": "Ada",
        "lang": "pt-BR",
        "found": True,
        "version": personas.SHEET_VERSION,
        "fetchedAt": int(time.time()),
        "fixture": "labeled",
        "sourcePage": PAGE,
        "quotes": checked,
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")
    from products.animind_core import render

    monkeypatch.setattr(render.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)

    def voice(text, pick, mp3, clip, max_s, rate_adj=0):  # noqa: ARG001
        import numpy as np
        clip.parent.mkdir(parents=True, exist_ok=True)
        return np.zeros(render.SAMPLE_RATE // 5, np.int16), "+0%"

    def encode(self, frame_iter=None, audio_path=None, output_path=None, **_kwargs):  # noqa: ARG002
        from pathlib import Path
        Path(output_path).write_bytes(b"fixture-mp4")

    monkeypatch.setattr(render, "_voice_line", voice)
    monkeypatch.setattr(render.frameqa, "check", lambda *_a, **_k: {"pass": True, "badFrames": 0, "warnings": []})
    monkeypatch.setattr("animation.parametric.renderer.AnimationRenderer.render", encode)
    from fastapi.testclient import TestClient

    from products.animind_core.app import app

    client = TestClient(app)
    own = {**H, "X-Animind-User": "user_one"}
    other = {**H, "X-Animind-User": "user_two"}
    sheet = client.get("/v1/personas/sheet", params={"name": "Ada", "lang": "pt-BR"}, headers=own)
    assert sheet.status_code == 200, sheet.text
    assert sheet.json()["fixture"] == "labeled"
    spoken = [quote for quote in sheet.json()["quotes"] if quote.get("verification") == "verified"]
    assert [quote["text"] for quote in spoken] == [VERIFIED]
    plan = client.post("/v1/avatars/plan", headers=own, json={
        "prompt": "Discurso de Ada sobre a verdade secreta", "language": "pt",
    })
    assert plan.status_code == 200, plan.text
    assert plan.json()["fixture"] == "plan"
    assert plan.json()["personas"] == ["Ada"]
    assert plan.json()["intent"] == "monologue"
    assert plan.json()["consume"] is False
    made = client.post("/v1/render", headers=own, json={
        "characters": [{"id": "ada-fixture", "name": "Ada", "type": "stick"}],
        "template": "solo",
        "scenario": "studio",
        "topic": "verdade secreta",
        "language": "pt-BR",
        "duration": 10,
    })
    assert made.status_code == 200, made.text
    job_id = made.json()["id"]
    state = {}
    for _ in range(80):
        state = client.get(f"/v1/jobs/{job_id}", headers=own).json()
        if state.get("status") in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert state.get("status") == "done", state
    assert state["result"]["writer"] == "template"
    blob = state["result"]["script"]
    texts = [line["text"] for line in blob]
    assert VERIFIED in texts
    assert INVENTED not in texts
    assert all(line.get("kind") != "quote" or line["text"] != INVENTED for line in blob)
    video = state["result"]["videoUrl"]
    assert f"users/{user}/jobs/{job_id}/preview.mp4" in video
    relative = video.split("/v1/files/", 1)[1]
    assert client.get(f"/v1/files/{relative}", headers=own).content == b"fixture-mp4"
    for headers in (other, H):
        denied = client.get(f"/v1/jobs/{job_id}", headers=headers)
        assert denied.status_code == 404
        assert denied.json() == {"detail": "job not found"}
        hidden = client.get(f"/v1/files/{relative}", headers=headers)
        assert hidden.status_code == 404
        assert hidden.json() == {"detail": "not found"}
        for response in (denied, hidden):
            assert INVENTED not in response.text
            assert VERIFIED not in response.text
            assert "users/" not in response.text
    assert calls == []
    render._JOBS.clear()
