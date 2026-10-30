import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_FORCE_MOCK", "1")
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "1")
    from products.animind_core.app import app

    return TestClient(app)


H = {"Authorization": "Bearer test-key"}


def test_rejects_missing_key(client):
    assert client.post("/v1/characters", json={}).status_code == 401


def test_upload_needs_rights(client):
    r = client.post("/v1/characters", headers=H, json={"source": {"kind": "upload", "imageDataUrl": "x"}})
    assert r.status_code == 400


def _make(client, prompt="owl professor"):
    r = client.post("/v1/characters", headers=H, json={"source": {"kind": "prompt", "prompt": prompt}})
    assert r.status_code == 200, r.text
    return r.json()


def test_character_contract_v2_shape(client):
    c = _make(client)
    assert c["status"] == "ready" and c["style"] == "rig" and c["generator"] == "mock"
    assert set(c["views"]) == {"front", "left", "right"}
    for view in c["views"].values():
        assert {"body", "head", "eyes", "eyes_closed"} <= {layer["name"] for layer in view["layers"]}
        assert set(view["visemes"]) == set("XABCDEFGH")
        for layer in [*view["layers"], *view["visemes"].values()]:
            assert {"name", "url", "x", "y", "w", "h", "z"} <= set(layer)
            assert 0 <= layer["x"] and layer["x"] + layer["w"] <= view["canvas"]["w"]
    path = c["views"]["left"]["layers"][0]["url"].split("/v1/files/")[1]
    assert client.get(f"/v1/files/{path}").headers["content-type"] == "image/png"


def test_right_view_mirrors_left(client):
    c = _make(client)
    w = c["views"]["left"]["canvas"]["w"]
    left = {layer["name"]: layer for layer in c["views"]["left"]["layers"]}
    for layer in c["views"]["right"]["layers"]:
        assert layer["x"] == w - left[layer["name"]]["x"] - left[layer["name"]]["w"]


def test_render_job_has_visemes_and_video(client, monkeypatch):
    """Full mock pipeline: edge-tts is stubbed, Rhubarb may be absent (envelope)."""
    import shutil
    import subprocess

    from products.animind_core import render

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")

    async def fake_tts(text, voice, out, rate="+0%"):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=0.8", str(out)], check=True)

    monkeypatch.setattr(render, "_tts", fake_tts)
    c = _make(client)
    job = client.post("/v1/render", headers=H, json={"characters": [c], "template": "solo", "scenario": "studio", "topic": "IA"}).json()
    for _ in range(240):
        state = client.get(f"/v1/jobs/{job['id']}", headers=H).json()
        if state["status"] in {"done", "failed"}:
            break
        time.sleep(0.5)
    assert state["status"] == "done", state
    res = state["result"]
    assert res["rigged"] == [True]
    assert all(line["visemes"] and line["view"] in {"front", "left", "right"} for line in res["script"])
    assert res["writer"] == "template" and res["personas"][0]["found"] is False
    assert {line["kind"] for line in res["script"]} == {"paraphrase"}  # no sheet offline: nothing passes as a quote
    assert [line["beat"] for line in res["script"]][0] == "gancho"
    assert client.get("/v1/files/" + res["videoUrl"].split("/v1/files/")[1]).status_code == 200


def test_unknown_character_is_not_loaded():
    from products.animind_core import characters

    assert characters.load("../../etc") is None


def test_files_cannot_escape(client):
    assert client.get("/v1/files/../../etc/passwd").status_code == 404


def test_unknown_job(client):
    assert client.get("/v1/jobs/nope", headers=H).status_code == 404


def test_template_debate_alternates_speakers():
    from products.animind_core import script

    lines = script.write("debate", "IA", ["A", "B"], sheets=[{"quotes": []}, {"quotes": []}], use_llm=False)["lines"]
    assert {line["speaker"] for line in lines} == {0, 1}


def test_line_views():
    from products.animind_core.render import line_view

    assert [line_view("debate", s, i) for i, s in enumerate((0, 1))] == ["right", "left"]
    assert line_view("solo", 0, 1) == "right"


def test_despill_removes_magenta():
    from PIL import Image

    from products.animind_core.rig import _despill

    img = Image.new("RGBA", (2, 1), (255, 0, 255, 255))
    img.putpixel((1, 0), (200, 40, 40, 255))  # a red tie stays
    out = _despill(img)
    assert out.getpixel((0, 0))[3] == 0 and out.getpixel((1, 0))[3] == 255


def test_slug_folds_accents():
    from products.animind_core.characters import _slug

    assert _slug("Pirata filósofo de chapéu") == "pirata_filosofo_de_chapeu"


def _scene(template="debate", scenario="arena"):
    import numpy as np

    from products.animind_core import scene

    script = [{"speaker": 0, "text": "Primeira fala.", "startMs": 0, "endMs": 2500},
              {"speaker": 1, "text": "Segunda fala.", "startMs": 2800, "endMs": 5000}]
    return scene.Scene(template=template, scenario=scenario, rigs=[None, None], script=script, envelope=np.ones(600) * 0.5,
                       env_fps=100, duration_s=5.5, disclaimer="Paródia • Gerado por IA", names=["A", "B"], roles=["x", "y"])


def test_cinema_frames_are_deterministic():
    a, b = _scene(), _scene()
    for t in (0.2, 1.6, 2.85, 4.0):
        fa, fb = a.frame(t), b.frame(t)
        assert fa.size == (1280, 720) and fa.tobytes() == fb.tobytes()


def test_debate_camera_cuts_to_each_speaker():
    sc = _scene()
    starts = [(round(s, 2), shot.cx < 640, kind, shot.kind) for s, shot, kind in sc.shots]
    assert starts[0][2] == "cut" and starts[0][3] == "wide" and len(starts) == 3  # establishing wide first
    assert starts[1][1] and not starts[2][1]  # speaker 0 sits left, speaker 1 right
    assert [s[3] for s in starts[1:]] == ["ots", "ots"] and [s[2] for s in starts[1:]] == ["dissolve", "cut"]
    assert len(sc.camera(starts[1][0] + 0.05)) in {1, 2}


def test_shot_grammar_quotes_get_a_close_up_and_the_close_is_a_two_shot():
    import numpy as np

    from products.animind_core import scene

    script = [{"speaker": i % 2, "text": f"Fala {i}.", "startMs": i * 3000, "endMs": i * 3000 + 2500,
               "kind": "quote" if i == 1 else "paraphrase"} for i in range(4)]
    sc = scene.Scene(template="debate", scenario="studio", rigs=[None, None], script=script, envelope=np.ones(1300) * 0.5,
                     env_fps=100, duration_s=12.5, disclaimer="x", names=["A", "B"], roles=["x", "y"])
    kinds = [shot.kind for _, shot, _ in sc.shots]
    assert kinds == ["wide", "ots", "cu", "ots", "two"]
    cu = sc.shots[2][1]
    assert cu.zoom > sc.shots[1][1].zoom and cu.cx > 640  # speaker 1's quote, tight on the right
    frame = sc.frame(6.5)  # the listener is out of focus in a single; still a full frame
    assert frame.size == (1280, 720)
