"""/v1/health lists what this process can render, so a stale core is visible before a render falls back."""
from fastapi.testclient import TestClient


def test_health_lists_scenarios_and_templates(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    from products.animind_core import scene
    from products.animind_core.app import app

    h = TestClient(app).get("/v1/health").json()
    assert h["ok"] is True
    assert h["scenarios"] == list(scene.SCENARIOS)
    assert "agora" in h["scenarios"]
    assert {"debate", "lesson", "solo"} <= set(h["templates"])
