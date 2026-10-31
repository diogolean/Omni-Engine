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


def test_vertical_plan_inserts_reaction_cutaway_on_long_lines():
    import numpy as np

    from products.animind_core import scene

    script = [{"speaker": 0, "text": "a " * 40, "kind": "line", "startMs": 0, "endMs": 8000},
              {"speaker": 1, "text": "b", "kind": "quote", "startMs": 8000, "endMs": 10000}]
    sc = scene.Scene(template="debate", scenario="studio", rigs=[None, None], script=script, envelope=np.ones(1100) * 0.5,
                     env_fps=100, duration_s=11.0, disclaimer="x", fmt="9:16")
    kinds = [s.kind for _, s, _ in sc.shots]
    assert "react" in kinds and "wide" not in kinds


@pytest.mark.parametrize("dur, adj, want", [(0.6, -6, ["-1%"]), (0.6, 4, ["+9%"]), (3.0, -6, ["-1%", "+20%"]),
                                            (3.0, 4, ["+9%", "+24%"])])
def test_beat_pace_nudges_the_cast_rate(monkeypatch, tmp_path, dur, adj, want):
    """A quote is voiced a bit slower and a jab a bit faster than the cast pace; an overrun re-voice speeds up from
    the jab pace (never from a slowed quote), so a line can never end slower than it was budgeted."""
    from products.animind_core import render

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    rates = []

    async def fake_tts(text, voice, out, rate="+0%", pitch="+0Hz"):
        rates.append(rate)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={dur}", str(out)],
                       check=True)

    monkeypatch.setattr(render, "_tts", fake_tts)
    pick = {"voice": "pt-BR-AntonioNeural", "ratePct": 5, "pitchHz": 0}
    _, used = render._voice_line("x", pick, tmp_path / "a.mp3", tmp_path / "a.wav", 2.0, adj)
    assert rates == want and used == want[-1]
