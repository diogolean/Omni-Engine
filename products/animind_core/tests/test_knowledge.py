import time

import pytest
from fastapi.testclient import TestClient

from products.animind_core import knowledge, script

H = {"Authorization": "Bearer test-key"}
TEXT = """Sócrates foi um filósofo ateniense do período clássico da Grécia Antiga. Creditado como um dos fundadores
da filosofia ocidental, ele é conhecido pelo método socrático de perguntas, a maiêutica e a ironia.

Ele foi condenado à morte por cicuta em 399 a.C., acusado de corromper a juventude e de impiedade contra os deuses
da cidade. Seus diálogos chegaram até nós pelos escritos de Platão e Xenofonte, seus discípulos mais conhecidos."""


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "1")


def _fake_collect(name, lang, mode, sheet):
    chunks = knowledge.chunk(TEXT * (3 if mode == "deep" else 1), source="Wikipedia: Sócrates", url="https://pt.wikipedia.org/wiki/S", kind="wikipedia", size=40, overlap=10)
    if mode == "deep":
        chunks.append({"text": "A virtude é conhecimento e ninguém erra de propósito; " * 4, "source": "Wikisource: Apologia",
                       "url": "https://pt.wikisource.org/wiki/A", "kind": "wikisource"})
    return chunks


def test_embed_retrieve_ranks_the_relevant_passage():
    chunks = [{"text": "a cicuta e a condenação à morte em Atenas", "kind": "wikipedia", "source": "x", "url": "u"},
              {"text": "astronomia moderna e telescópios espaciais", "kind": "wikipedia", "source": "y", "url": "u"}]
    ix = ({"chunks": chunks}, knowledge.embed([c["text"] for c in chunks]))
    top = knowledge.retrieve(ix, "condenação de Sócrates à morte", k=1)
    assert top[0]["source"] == "x"
    assert knowledge.embed(["x"]).shape == (1, knowledge.DIM)


def test_copies_passage_detects_verbatim_runs():
    p = [{"text": TEXT}]
    assert knowledge.copies_passage("ele é conhecido pelo método socrático de perguntas e mais", p)
    assert not knowledge.copies_passage("Pergunte sempre por quê, e a verdade aparece aos poucos.", p)


def test_quick_and_deep_are_cached_separately(monkeypatch):
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "0")
    monkeypatch.setattr(knowledge, "collect", _fake_collect)
    q = knowledge.build("Sócrates", "pt-BR", "quick", sheet={"quotes": []})
    d = knowledge.build("Sócrates", "pt-BR", "deep", sheet={"quotes": []})
    assert d["chunks"] > q["chunks"] and "wikisource" in d["counts"]
    meta, vecs = knowledge.load("Sócrates", "pt-BR", "deep")
    assert len(meta["chunks"]) == len(vecs)
    assert knowledge.paths("Sócrates", "pt-BR", "deep")[0].parent.name == "pt-BR"


def test_script_uses_passages_but_quotes_stay_verbatim(monkeypatch):
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "0")
    monkeypatch.setattr(knowledge, "collect", _fake_collect)
    sheet = {"found": True, "quotes": [{"id": "q1", "text": "Só sei que nada sei.", "source": "Apologia", "sourceKind": "primary",
                                        "url": "https://pt.wikiquote.org/wiki/S"}]}
    seen = {}
    monkeypatch.setattr(script, "_gemini", lambda prompt: seen.setdefault("p", prompt) and [])
    out = script.write("solo", "a morte e a virtude", ["Sócrates"], sheets=[sheet], use_llm=True, mode="deep")
    assert "Background passages" in seen["p"] and "NOT quotes" in seen["p"]
    assert out["personas"][0]["mode"] == "deep" and out["personas"][0]["passages"]
    assert script.check(out["lines"], [sheet]) == []


def test_deep_job_api(monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "0")
    monkeypatch.setattr(knowledge, "collect", _fake_collect)
    monkeypatch.setattr(knowledge.personas, "load", lambda *a, **k: {"quotes": []})
    from products.animind_core.app import app

    c = TestClient(app)
    assert c.get("/v1/personas/deep", params={"name": "Platão"}, headers=H).json()["status"] == "missing"
    job = c.post("/v1/personas/deep", json={"name": "Platão, o filósofo"}, headers=H).json()
    assert job["name"] == "Platão"
    for _ in range(100):
        st = c.get("/v1/personas/deep", params={"name": "Platão"}, headers=H).json()
        if st["status"] == "ready":
            break
        time.sleep(0.05)
    assert st["status"] == "ready" and st["chunks"] > 0
    assert c.post("/v1/personas/deep", json={"name": ""}, headers=H).status_code == 400
