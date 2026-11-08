"""Two owners cannot read each other's job or the file it points at."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from products.animind_core import owner, render

SECRET_NAME = "Albertina Segredo"
SECRET_PROMPT = "PROMPT-NAO-VAZAR"
H = {"Authorization": "Bearer test-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "1")
    from products.animind_core.app import app

    return TestClient(app)


def test_owned_job_and_file_are_invisible_to_everyone_else(client, tmp_path):
    owner_a = owner.accept_user("user_one")
    owner_b = owner.accept_user("user_two")
    assert owner_a != owner_b
    job_id = "jobowner0001"
    relative = f"users/{owner_a}/jobs/{job_id}/note.json"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"name": SECRET_NAME, "prompt": SECRET_PROMPT}), encoding="utf-8")
    render._JOBS[job_id] = {
        "id": job_id,
        "status": "done",
        "progress": 1.0,
        "_owner": owner_a,
        "result": {
            "characters": [{"name": SECRET_NAME}],
            "topic": SECRET_PROMPT,
            "videoUrl": f"http://test/v1/files/{relative}",
        },
    }
    try:
        own = {**H, "X-Animind-User": "user_one"}
        other = {**H, "X-Animind-User": "user_two"}
        seen = client.get(f"/v1/jobs/{job_id}", headers=own)
        assert seen.status_code == 200
        assert seen.json()["result"]["characters"][0]["name"] == SECRET_NAME
        assert "_owner" not in seen.json()
        for headers in (other, H):
            denied = client.get(f"/v1/jobs/{job_id}", headers=headers)
            assert denied.status_code == 404
            assert denied.json() == {"detail": "job not found"}
            assert SECRET_NAME not in denied.text
            assert SECRET_PROMPT not in denied.text
            assert owner_a not in denied.text
            assert "users/" not in denied.text
        assert client.get(f"/v1/files/{relative}", headers=own).status_code == 200
        for headers in (other, H):
            hidden = client.get(f"/v1/files/{relative}", headers=headers)
            assert hidden.status_code == 404
            assert hidden.json() == {"detail": "not found"}
            assert SECRET_NAME not in hidden.text
            assert SECRET_PROMPT not in hidden.text
            assert owner_a not in hidden.text
            assert "note.json" not in hidden.text
        emailed = client.get(f"/v1/jobs/{job_id}", headers={**H, "X-Animind-User": "ada@example.com"})
        assert emailed.status_code == 400
        assert SECRET_NAME not in emailed.text
        assert "ada@example.com" not in emailed.text
    finally:
        render._JOBS.pop(job_id, None)


def test_owner_poll_reloads_the_job_file_after_memory_is_cleared(client, monkeypatch):
    """A queued fixture stays on disk. No video file, so the poll is not a ready preview."""
    monkeypatch.setattr(render.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)

    class _Stopped:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def start(self) -> None:
            return None

    monkeypatch.setattr(render.threading, "Thread", _Stopped)
    own = {**H, "X-Animind-User": "user_one"}
    other = {**H, "X-Animind-User": "user_two"}
    created = client.post("/v1/render", headers=own, json={
        "characters": [{"id": "c1", "name": SECRET_NAME}],
        "template": "solo",
        "scenario": "studio",
        "topic": SECRET_PROMPT,
    })
    assert created.status_code == 200, created.text
    job = created.json()
    assert job["status"] == "queued"
    assert "videoUrl" not in created.text
    assert job["usage"]["schema"] == "animind.usage.v1"
    assert job["usage"]["totals"]["estimatedMicro"] is None
    assert job["usage"]["totals"]["reportedMicro"] is None
    assert job["usage"]["totals"]["estimatedMicro"] != 0
    render._JOBS.clear()
    seen = client.get(f"/v1/jobs/{job['id']}", headers=own)
    assert seen.status_code == 200, seen.text
    assert seen.json()["status"] == "queued"
    assert seen.json()["id"] == job["id"]
    assert "videoUrl" not in seen.text
    assert seen.json()["usage"]["schema"] == "animind.usage.v1"
    assert seen.json()["usage"]["totals"]["estimatedMicro"] is None
    assert seen.json()["usage"]["totals"]["reportedMicro"] is None
    for headers in (other, H):
        denied = client.get(f"/v1/jobs/{job['id']}", headers=headers)
        assert denied.status_code == 404
        assert denied.json() == {"detail": "job not found"}
        assert SECRET_NAME not in denied.text
        assert SECRET_PROMPT not in denied.text
        assert "users/" not in denied.text
        assert "usage" not in denied.text
        assert job["usage"]["userRef"] not in denied.text
    render._JOBS.clear()


def test_owner_rereads_the_preview_file_after_memory_is_cleared(client, monkeypatch, tmp_path):
    """A planted preview stays with the owner. A loose jobs/ URL is not the public path."""
    monkeypatch.setattr(render.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)

    class _Stopped:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def start(self) -> None:
            return None

    monkeypatch.setattr(render.threading, "Thread", _Stopped)
    own = {**H, "X-Animind-User": "user_one"}
    other = {**H, "X-Animind-User": "user_two"}
    created = client.post("/v1/render", headers=own, json={
        "characters": [{"id": "c1", "name": SECRET_NAME}],
        "template": "solo",
        "scenario": "studio",
        "topic": SECRET_PROMPT,
    })
    assert created.status_code == 200, created.text
    job = created.json()
    digest = owner.accept_user("user_one")
    directory = tmp_path / "users" / digest / "jobs" / job["id"]
    (directory / "preview.mp4").write_bytes(b"fixture-preview")
    record = json.loads((directory / "job.json").read_text(encoding="utf-8"))
    record["status"] = "done"
    record["progress"] = 1.0
    record["result"] = {
        "characters": [{"name": SECRET_NAME}],
        "topic": SECRET_PROMPT,
        "videoUrl": f"http://test/v1/files/jobs/{job['id']}/preview.mp4",
    }
    (directory / "job.json").write_text(json.dumps(record), encoding="utf-8")
    loose = tmp_path / "jobs" / job["id"] / "preview.mp4"
    loose.parent.mkdir(parents=True)
    loose.write_bytes(b"loose-preview")
    public = tmp_path / "puppets" / "fixture.png"
    public.parent.mkdir(parents=True)
    public.write_bytes(b"public-asset")
    render._JOBS.clear()
    seen = client.get(f"/v1/jobs/{job['id']}", headers=own)
    assert seen.status_code == 200, seen.text
    body = seen.json()
    assert body["status"] == "done"
    video = body["result"]["videoUrl"]
    assert f"users/{digest}/jobs/{job['id']}/preview.mp4" in video
    assert "/v1/files/jobs/" not in video
    assert body["usage"]["schema"] == "animind.usage.v1"
    assert body["usage"]["totals"]["estimatedMicro"] is None
    relative = video.split("/v1/files/", 1)[1]
    fetched = client.get(f"/v1/files/{relative}", headers=own)
    assert fetched.status_code == 200
    assert fetched.content == b"fixture-preview"
    for headers in (other, H):
        denied = client.get(f"/v1/jobs/{job['id']}", headers=headers)
        assert denied.status_code == 404
        assert denied.json() == {"detail": "job not found"}
        hidden = client.get(f"/v1/files/{relative}", headers=headers)
        assert hidden.status_code == 404
        assert hidden.json() == {"detail": "not found"}
        for response in (denied, hidden):
            assert SECRET_NAME not in response.text
            assert SECRET_PROMPT not in response.text
            assert "users/" not in response.text
            assert "usage" not in response.text
            assert digest not in response.text
            assert b"fixture-preview" not in response.content
    stolen = client.get(f"/v1/files/jobs/{job['id']}/preview.mp4", headers=other)
    assert stolen.status_code == 404
    assert stolen.content != b"loose-preview"
    assert client.get("/v1/files/puppets/fixture.png", headers=H).content == b"public-asset"
    render._JOBS.clear()


def test_anonymous_job_is_not_readable_by_a_named_user(client):
    job_id = "anonjob0001"
    render._JOBS[job_id] = {"id": job_id, "status": "queued", "progress": 0.0, "_owner": None}
    try:
        assert client.get(f"/v1/jobs/{job_id}", headers=H).status_code == 200
        named = client.get(f"/v1/jobs/{job_id}", headers={**H, "X-Animind-User": "user_one"})
        assert named.status_code == 404
        assert named.json() == {"detail": "job not found"}
    finally:
        render._JOBS.pop(job_id, None)


def test_owned_render_files_live_under_the_user_hash():
    digest = owner.accept_user("user_one")
    assert owner.job_rel(digest, "abc") == f"users/{digest}/jobs/abc"
    assert owner.job_rel(None, "abc") == "jobs/abc"
