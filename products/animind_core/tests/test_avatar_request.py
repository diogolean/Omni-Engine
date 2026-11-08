"""Plan one or two people, and do not spend without a logged-in click."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from products.animind_core import avatar_request

H = {"Authorization": "Bearer test-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    from products.animind_core.app import app

    return TestClient(app)


def test_intents_do_not_invent_a_second_person_or_a_chair():
    speech = avatar_request.infer("Discurso de Ada sobre o mar", language="pt")
    assert speech["intent"] == "monologue" and speech["personas"] == ["Ada"]
    assert speech["needsChoice"] is False and speech["liveDebate"] is False
    english = avatar_request.infer("Speech by Ada about the sea", language="en")
    assert english["language"] == "en" and english["personas"] == ["Ada"]
    debate = avatar_request.infer("Debate entre Ada e Bento", language="pt")
    assert debate["intent"] == "debate" and debate["personas"] == ["Ada", "Bento"]
    missing = avatar_request.infer("Debate sobre a verdade", language="pt")
    assert missing["needsChoice"] is True and missing["personas"] == []
    assert "missing_persona" in missing["reasons"]
    chair = avatar_request.infer("Uma cadeira na praia", language="pt")
    assert chair["personas"] == [] and chair["needsChoice"] is True
    both = avatar_request.infer("Monólogo de Ada e Bento", language="pt")
    assert both["personas"] == ["Ada", "Bento"] and "persona_count" in both["reasons"]
    styled = avatar_request.infer("Discurso de Ada em anime", language="pt")
    assert styled["style"] == "anime"


def test_retry_is_the_same_job_and_the_other_user_cannot_read_it(client, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "products.aiwake.animator.factory.create_puppet.generate_character_image",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("paid image call")),
    )
    own = {**H, "X-Animind-User": "user_one"}
    body = {"prompt": "Discurso de Ada sobre o mar", "language": "pt", "requestedResolution": "1k"}
    first = client.post("/v1/avatars", headers=own, json=body)
    assert first.status_code == 200
    job = first.json()
    assert job["status"] == "blocked" and job["charged"] is False and job["providerCalls"] == 0
    assert job["execution"] == "background" and job["liveImage"] is False
    assert job["progress"] is None and job["story"] is None
    assert [item["stage"] for item in job["events"]] == ["plan", "queued", "fail"]
    assert job["events"][-1]["blockedBefore"] == "image generation"
    assert job["events"][-1]["error"] == {"code": "generation_not_authorized", "recoverable": True}
    assert "image generation" not in [item["stage"] for item in job["events"]]
    assert job["plan"]["resolution"]["consume"] is False
    second = client.post("/v1/avatars", headers=own, json=body)
    assert second.json()["id"] == job["id"]
    assert second.json()["retries"] == 1
    assert second.json()["providerCalls"] == 0
    assert second.json()["events"] == job["events"]
    assert "costUsd" not in second.text and "tokens" not in second.text
    seen = client.get(f"/v1/avatars/{job['id']}", headers=own)
    assert seen.status_code == 200 and seen.json()["events"] == job["events"]
    admin = (tmp_path / "telemetry" / "avatar_admin.jsonl").read_text(encoding="utf-8")
    assert admin.count("\n") == 1
    assert '"costUsd": null' in admin and '"tokens": null' in admin
    assert "Ada" not in admin and "mar" not in admin
    assert "mar" not in second.text
    hidden = client.get(f"/v1/avatars/{job['id']}", headers={**H, "X-Animind-User": "user_two"})
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "avatar not found"}
    assert "Ada" not in hidden.text
    assert client.post("/v1/avatars", headers=H, json=body).status_code == 403
    emailed = client.post("/v1/avatars", headers={**H, "X-Animind-User": "ada@example.com"}, json=body)
    assert emailed.status_code == 400 and "ada@example.com" not in emailed.text
    avatar_request._JOBS.clear()


def test_premium_4k_still_does_not_spend(client, monkeypatch):
    monkeypatch.setenv("ANIMIND_PREMIUM_USERS", "user_one")
    body = {"prompt": "Speech by Ada", "language": "en", "requestedResolution": "4k", "premium": True}
    made = client.post("/v1/avatars", headers={**H, "X-Animind-User": "user_one"}, json=body)
    assert made.status_code == 200
    plan = made.json()["plan"]
    assert plan["entitlement"] == "server"
    assert plan["resolution"]["consume"] is False
    assert plan["resolution"]["mappedPreset"] is None
    assert made.json()["charged"] is False
    avatar_request._JOBS.clear()


def test_fixture_plates_show_partial_qa_without_a_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    user = avatar_request.accept_user("user_one")

    def plate(color, checks=False):
        image = Image.new("RGBA", (1024, 1280), color + (255,))
        if checks:
            paint = ImageDraw.Draw(image)
            for y in range(0, 1280, 8):
                for x in range(0, 1024, 8):
                    if (x // 8 + y // 8) % 2:
                        paint.rectangle((x, y, x + 8, y + 8), fill=(255, 255, 0, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 120, 200, 360), fill=(210, 170, 140, 255))
        return image

    job = avatar_request.generate_avatar(
        {"prompt": "Debate entre Ada e Bento", "language": "pt"},
        user,
        plates={"Ada": plate((0, 0, 0)), "Bento": plate((0, 0, 0), checks=True)},
    )
    assert job["status"] == "partial"
    assert job["cutout"] == "real" and job["liveImage"] is False
    assert job["fixture"] == "plate"
    by_name = {item["name"]: item["status"] for item in job["participants"]}
    assert by_name == {"Ada": "ready", "Bento": "rejected"}
    stages = [item["stage"] for item in job["events"]]
    assert "image generation" not in stages and "rig" not in stages
    assert stages.count("QA") == 2 and stages.count("cutout") == 2
    assert job["progress"] is None and job["story"] is None
    again = avatar_request.generate_avatar(
        {"prompt": "Debate entre Ada e Bento", "language": "pt"}, user,
    )
    assert again["id"] == job["id"] and again["providerCalls"] == 0
    avatar_request._JOBS.clear()
