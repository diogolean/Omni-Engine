import shutil
import subprocess
import time

import pytest

from products.animind_core import casting
from .test_api import H, _make, client  # noqa: F401


def test_casting_by_persona_and_override():
    a, b = casting.cast([{"name": "Simone de Beauvoir", "persona": "filósofa existencialista"},
                         {"name": "Nietzsche", "persona": "filósofo provocativo", "born": 1844}], "pt-BR")
    assert a["voice"] == "pt-BR-FranciscaNeural" and b["voice"] == "pt-BR-AntonioNeural"
    assert b["traits"]["temper"] == "fiery" and b["traits"]["age"] == "elder"
    x, y = casting.cast([{"name": "Sócrates"}, {"name": "Platão"}], "pt-BR")
    assert x["voice"] == y["voice"] and x["pitchHz"] != y["pitchHz"]  # one male pt-BR voice: split by pitch
    (o,) = casting.cast([{"name": "Sócrates"}], "pt-BR", [{"voice": "pt-BR-ThalitaMultilingualNeural"}])
    assert o["voice"] == "pt-BR-ThalitaMultilingualNeural" and o["auto"] is False


def test_vertical_debate_has_shot_countershot(client, monkeypatch):  # noqa: F811
    from products.animind_core import render

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")

    async def fake_tts(text, voice, out, rate="+0%", pitch="+0Hz"):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=0.6", str(out)], check=True)

    monkeypatch.setattr(render, "_tts", fake_tts)
    a, b = _make(client, "owl"), _make(client, "cat")
    job = client.post("/v1/render", headers=H, json={"characters": [a, b], "template": "debate", "format": "9:16",
                                                    "topic": "IA"}).json()
    for _ in range(400):
        state = client.get(f"/v1/jobs/{job['id']}", headers=H).json()
        if state["status"] in {"done", "failed"}:
            break
        time.sleep(0.5)
    assert state["status"] == "done", state
    res = state["result"]
    assert (res["format"], res["width"], res["height"]) == ("9:16", 1080, 1920)
    assert len(res["voices"]) == 2
    path = client.get("/v1/files/" + res["videoUrl"].split("/v1/files/")[1])
    assert path.status_code == 200
