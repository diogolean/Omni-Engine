"""HTTP end-to-end run of the Animind core: persona sheet -> script -> rig -> render.

Opt-in (minutes of CPU, network): ``ANIMIND_E2E=1 pytest products/animind_core/tests/test_e2e_http.py``.

- By default a real ``uvicorn`` is started on a free port with a temp ``ANIMIND_DATA_DIR``.
  ``ANIMIND_E2E_CORE_URL`` + ``ANIMIND_E2E_CORE_KEY`` point the test at a running core instead.
- Network (Wikiquote + edge-tts) is required for the run; without it the tests skip.
- Rig art: Gemini when ``GEMINI_API_KEY``/``GOOGLE_API_KEY`` is set, else the flat mock art
  (``ANIMIND_E2E_MOCK_ART=1`` forces mock art). The Gemini-only check skips without a key.
- ``ANIMIND_E2E_TEMPLATE`` (``lesson`` | ``debate`` | ``solo``, default ``lesson``),
  ``ANIMIND_E2E_PERSONA`` (default ``Sócrates``), ``ANIMIND_E2E_RIVAL`` (debate, default ``Nietzsche``).
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(os.getenv("ANIMIND_E2E") != "1", reason="opt-in: set ANIMIND_E2E=1"),
    pytest.mark.timeout(1800),
]

ROOT = Path(__file__).resolve().parents[3]
PERSONA = os.getenv("ANIMIND_E2E_PERSONA", "Sócrates")
RIVAL = os.getenv("ANIMIND_E2E_RIVAL", "Nietzsche")
TEMPLATE = os.getenv("ANIMIND_E2E_TEMPLATE", "lesson")
UA = "AnimindCoreE2E/1.0 (https://github.com/diogolean/Omni-Engine)"


def _online(host: str, port: int = 443) -> bool:
    try:
        socket.create_connection((host, port), timeout=5).close()
        return True
    except OSError:
        return False


def _gemini_key() -> bool:
    return bool((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())


def _call(base: str, key: str, path: str, body: dict | None = None, timeout: float = 900) -> dict:
    req = urllib.request.Request(f"{base}/v1{path}", data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — local core
        return json.loads(r.read())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def core(tmp_path_factory):
    """(base_url, key, data_dir or None) of a running core."""
    if not (_online("pt.wikiquote.org") and _online("speech.platform.bing.com")):
        pytest.skip("no network: Wikiquote and edge-tts are needed for a full run")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not installed")
    url = os.getenv("ANIMIND_E2E_CORE_URL")
    if url:
        yield url.rstrip("/"), os.getenv("ANIMIND_E2E_CORE_KEY", ""), None
        return
    data = tmp_path_factory.mktemp("animind-core")
    port, key = _free_port(), secrets.token_hex(16)
    env = {**os.environ, "CORE_API_KEY": key, "ANIMIND_DATA_DIR": str(data), "CORE_PUBLIC_URL": f"http://127.0.0.1:{port}"}
    if os.getenv("ANIMIND_E2E_MOCK_ART") == "1":
        env["ANIMIND_FORCE_MOCK"] = "1"
    log = open(data / "core.log", "wb")  # noqa: SIM115 — closed below
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "products.animind_core.app:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(f"{base}/v1/health", timeout=2)  # noqa: S310
                break
            except (urllib.error.URLError, ConnectionError):
                if proc.poll() is not None:
                    pytest.fail(f"core exited: {(data / 'core.log').read_text(errors='replace')[-2000:]}")
                time.sleep(0.5)
        yield base, key, data
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


@pytest.fixture(scope="module")
def run(core):
    """One full run: characters over HTTP, one render job polled to the end, MP4 downloaded."""
    base, key, data = core
    names = [PERSONA, RIVAL] if TEMPLATE == "debate" else [PERSONA]
    chars = [_call(base, key, "/characters", {"source": {"kind": "prompt", "prompt": n}}) for n in names]
    job = _call(base, key, "/render", {"characters": chars, "template": TEMPLATE, "scenario": "classroom",
                                       "topic": "a verdade", "lang": "pt-BR"})
    deadline = time.time() + 1500
    while True:
        state = _call(base, key, f"/jobs/{job['id']}")
        assert 0 <= state["progress"] <= 1, state["progress"]
        if state["status"] in {"done", "failed"} or time.time() > deadline:
            break
        time.sleep(2)
    assert state["status"] == "done", state.get("error") or state["status"]
    res = state["result"]
    out = (data or Path(os.getenv("TMPDIR", "/tmp"))) / f"e2e-{job['id']}.mp4"
    with urllib.request.urlopen(res["videoUrl"], timeout=120) as r:  # noqa: S310 — local core
        out.write_bytes(r.read())
    return {"base": base, "key": key, "data": data, "chars": chars, "result": res, "mp4": out}


def _probe(path: Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=60, check=True)
    return json.loads(p.stdout)


def test_mp4_has_audio_and_runs_over_15s(run):
    info = _probe(run["mp4"])
    kinds = {s["codec_type"] for s in info["streams"]}
    assert {"video", "audio"} <= kinds, kinds
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1280, 720)
    duration = float(info["format"]["duration"])
    assert duration > 15, duration
    # The audio track covers the script, not just a blip.
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert float(audio.get("duration") or duration) > 15
    assert abs(duration - run["result"]["durationMs"] / 1000) < 1.5


def test_script_follows_beats_and_marks_kinds(run):
    res = run["result"]
    beats = [line["beat"] for line in res["script"]]
    assert beats[0] == "gancho"
    if TEMPLATE == "lesson":
        assert {"pergunta", "citação", "exemplo", "síntese"} <= set(beats)
    if TEMPLATE == "debate":
        assert {"tese", "réplica", "tréplica"} <= set(beats)
        assert {line["speaker"] for line in res["script"]} == {0, 1}
    assert all(line["kind"] in {"quote", "paraphrase"} for line in res["script"])
    for a, b in zip(res["script"], res["script"][1:]):
        assert a["endMs"] <= b["startMs"]
        assert a["visemes"], "each line carries visemes"


def test_persona_sheet_found_on_wikiquote(run):
    personas = run["result"]["personas"]
    assert personas and personas[0]["found"], personas
    for p in personas:
        if p["found"]:
            assert p["wikiquote"].startswith("https://pt.wikiquote.org/wiki/")
            assert p["quotes"] > 0
            assert "CC BY-SA" in p["license"]


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", html.unescape(text))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"\w+", text))


_PAGES: dict[str, str] = {}


def _page_text(url: str) -> str:
    """Rendered text of a Wikiquote/Wikisource page, folded (accents, case, punctuation)."""
    if url not in _PAGES:
        parts = urllib.parse.urlsplit(url)
        title = urllib.parse.unquote(parts.path.removeprefix("/wiki/"))
        api = f"{parts.scheme}://{parts.netloc}/w/api.php?" + urllib.parse.urlencode(
            {"action": "parse", "page": title, "prop": "text", "format": "json", "formatversion": 2, "redirects": 1})
        with urllib.request.urlopen(urllib.request.Request(api, headers={"User-Agent": UA}), timeout=30) as r:  # noqa: S310
            body = json.loads(r.read())["parse"]["text"]
        _PAGES[url] = _fold(re.sub(r"<[^>]+>", " ", body))
    return _PAGES[url]


def test_quote_lines_carry_real_sources(run):
    res = run["result"]
    quotes = [line for line in res["script"] if line["kind"] == "quote"]
    assert quotes, "a run with a found persona sheet quotes it"
    for line in quotes:
        q = line["quote"]
        assert line["text"] == q["text"], "quote text is copied, not rewritten"
        assert q["source"].strip() and q["site"]
        assert q["url"].startswith("https://pt.wikiquote.org/wiki/")
        # The quoted words are on the cited page.
        words = _fold(q["text"]).split()
        assert " ".join(words[:12]) in _page_text(q["url"]), (q["text"], q["url"])
    if run["data"]:
        # And byte for byte in the cached sheet the script was built from.
        import yaml  # noqa: PLC0415

        sheets = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in (run["data"] / "personas").rglob("*.yaml")]
        texts = {q["text"] for s in sheets for q in s.get("quotes") or []}
        assert all(line["text"] in texts for line in quotes)
    for line in res["script"]:
        if line["kind"] == "paraphrase":
            assert "quote" not in line
            assert not re.search(r"[“”\"]", line["text"])


def test_rig_layers_are_served(run):
    for c in run["chars"]:
        assert c["status"] == "ready" and set(c["views"]) == {"front", "left", "right"}
        layer = c["views"]["left"]["layers"][0]
        with urllib.request.urlopen(layer["url"], timeout=30) as r:  # noqa: S310 — local core
            assert r.headers["content-type"] == "image/png"
            assert r.read(8) == b"\x89PNG\r\n\x1a\n"
    assert all(run["result"]["rigged"]), run["result"]["rigged"]


@pytest.mark.skipif(not _gemini_key() or os.getenv("ANIMIND_E2E_MOCK_ART") == "1", reason="no Gemini key: rig art is the mock drawing")
def test_rig_art_is_gemini(run):
    assert all(c["generator"] == "gemini" for c in run["chars"]), [c.get("generator") for c in run["chars"]]
