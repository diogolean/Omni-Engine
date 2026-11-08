"""Fixture proof that a quote is verified only when the source page contains it.

The page texts below are labeled fixtures. They are not a live Wikimedia call.
"""
from __future__ import annotations

import os

import pytest

from products.animind_core import personas, script

# fixture: a short plain-text extract shaped like a Wikimedia explaintext response
WIKIQUOTE_PAGE = (
    "Sócrates foi um filósofo. "
    "As palavras falsas não são apenas más em si, mas também infectam a alma com o mal. "
    "Platão registrou o diálogo."
)
WIKISOURCE_PAGE = "No Fédon está escrito: a admiração é o sentimento de um filósofo, e a filosofia começa pela admiração."
WIKIQUOTE_URL = "https://pt.wikiquote.org/wiki/S%C3%B3crates"
WIKIPEDIA_URL = "https://pt.wikipedia.org/wiki/S%C3%B3crates"
WIKISOURCE_URL = "https://pt.wikisource.org/wiki/F%C3%A9don"


def _quote(text: str, **extra: str) -> dict:
    quote = {
        "id": "q1",
        "text": text,
        "source": "Platão, Fédon",
        "work": "Fédon",
        "sourceKind": "primary",
        "url": WIKIQUOTE_URL,
    }
    quote.update(extra)
    return quote


def test_quote_found_on_wikisource_is_verified_and_wikiquote_is_the_fallback():
    pages = {WIKIQUOTE_URL: WIKIQUOTE_PAGE, WIKISOURCE_URL: WIKISOURCE_PAGE, WIKIPEDIA_URL: "Sócrates, filósofo ateniense."}
    on_source = _quote(
        "a admiração é o sentimento de um filósofo, e a filosofia começa pela admiração",
        wikisource=WIKISOURCE_URL,
        wikipedia=WIKIPEDIA_URL,
    )
    on_quote = _quote("As palavras falsas não são apenas más em si, mas também infectam a alma com o mal.")
    missing = _quote("Esta frase não está em nenhuma das páginas gravadas.")
    marked = personas.verify_quotes([on_source, on_quote, missing], pages)
    assert marked[0]["verification"] == "verified"
    assert marked[0]["sourceSite"] == "Wikisource"
    assert marked[0]["url"] == WIKISOURCE_URL
    assert marked[1]["verification"] == "verified"
    assert marked[1]["sourceSite"] == "Wikiquote"
    assert marked[2]["verification"] == "not_found"
    assert marked[2]["sourceSite"] is None


def test_a_page_that_was_not_fetched_cannot_verify_the_quote():
    quote = _quote("As palavras falsas não são apenas más em si, mas também infectam a alma com o mal.")
    marked = personas.verify_quotes([quote], {})
    assert marked[0]["verification"] == "not_found"


def test_unverified_quote_is_not_spoken_as_a_real_line():
    sheet = {
        "name": "Sócrates",
        "found": True,
        "quotes": [
            {**_quote("As palavras falsas não são apenas más em si, mas também infectam a alma com o mal."), "verification": "not_found"},
            {**_quote("A admiração é o sentimento de um filósofo, e a filosofia começa pela admiração."), "id": "q2", "verification": "verified"},
        ],
    }
    speakable = script._quotable(sheet)
    assert [quote["id"] for quote in speakable] == ["q2"]
    problems = script.check(
        [{
            "speaker": 0,
            "kind": "quote",
            "text": sheet["quotes"][0]["text"],
            "quote": sheet["quotes"][0],
        }],
        [sheet],
    )
    assert any("not found on its source page" in problem for problem in problems)


def test_sheet_keeps_about_ten_and_puts_verified_quotes_first():
    quotes = [
        {"id": f"q{i}", "text": f"frase número {i} com palavras bastantes aqui", "verification": "not_found" if i < 3 else "verified"}
        for i in range(1, 15)
    ]
    kept = personas._limit(quotes)
    assert len(kept) == 10
    assert kept[0]["verification"] == "verified"
    assert kept[-1]["id"] == "q10"
    assert all(quote["verification"] == "verified" for quote in kept[:7])


def test_user_cache_is_separate_and_email_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    owner = personas.accept_user("user_one")
    other = personas.accept_user("user_two")
    assert owner != other
    with pytest.raises(ValueError):
        personas.accept_user("diogo@example.com")
    with personas.user_scope(owner):
        path = personas.cache_path("Sócrates", "pt-BR")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("name: Sócrates\nfound: true\nversion: 4\nquotes: []\n", encoding="utf-8")
    with personas.user_scope(other):
        assert personas.load("Sócrates", fetch=False)["found"] is False
    with personas.user_scope(owner):
        assert personas.load("Sócrates", fetch=False)["found"] is True
    assert "diogo@example.com" not in str(path)


def test_wikimedia_request_sends_the_user_agent_and_waits(monkeypatch):
    seen = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"parse": {"title": "S", "wikitext": ""}}'

    def urlopen(request, timeout=0):
        seen["agent"] = request.get_header("User-agent")
        seen["timeout"] = timeout
        return _Response()

    waits = []
    monkeypatch.setattr(personas.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(personas.time, "sleep", lambda seconds: waits.append(seconds))
    monkeypatch.setattr(personas.time, "monotonic", lambda: 0.0)
    monkeypatch.setenv("ANIMIND_WIKI_MIN_INTERVAL_S", "1")
    personas._NEXT_API = 5.0
    personas._api("pt.wikiquote.org", action="parse", page="Sócrates")
    assert seen["agent"].startswith("AnimindCore/1.0")
    assert waits == [5.0]


# Labeled HTTP fixture. These bodies are not a live Wikimedia response.
_FIXTURE_WIKI = """{{info/biografia|nome=Ada Fixture}}
== Citações ==
* A verdade fixture cabe nesta frase curta.
** Livro fixture, 1905
* Esta outra frase nao sera verificada nunca.
** Livro fixture, 1906
"""
_FIXTURE_EXTRACT = "Ada Fixture escreveu: A verdade fixture cabe nesta frase curta."


def test_recorded_wikiquote_responses_build_a_cached_sheet(tmp_path, monkeypatch):
    """CI replay. No network. The sheet is a labeled fixture, not a live page."""
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMIND_PERSONAS_OFFLINE", "0")
    monkeypatch.setenv("ANIMIND_WIKI_MIN_INTERVAL_S", "0")
    personas._NEXT_API = 0.0
    calls: list[str] = []

    def urlopen(request, timeout=0):  # noqa: ARG001
        import json as _json
        from urllib.parse import parse_qs, urlparse
        calls.append(request.full_url)
        query = parse_qs(urlparse(request.full_url).query)
        action = (query.get("action") or [""])[0]
        search = (query.get("search") or query.get("page") or query.get("titles") or [""])[0]
        host = urlparse(request.full_url).netloc
        if action == "opensearch":
            body = [search, ["Ada Fixture"], [""], ["https://pt.wikiquote.org/wiki/Ada_Fixture"]] if search == "Ada Fixture" else [search, [], [], []]
        elif action == "parse":
            body = (
                {"parse": {"title": "Ada Fixture", "wikitext": _FIXTURE_WIKI, "revid": 4242}}
                if search == "Ada Fixture"
                else {"error": {"code": "missingtitle"}}
            )
        elif "wikiquote.org" in host:
            body = {"query": {"pages": [{"extract": _FIXTURE_EXTRACT, "revisions": [{"revid": 4242}]}]}}
        else:
            body = {"query": {"pages": [{"extract": "Ada Fixture, nota fixture.", "revisions": [{"revid": 99}]}]}}
        encoded = _json.dumps(body).encode()

        class _Body:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return encoded

        return _Body()

    monkeypatch.setattr(personas.urllib.request, "urlopen", urlopen)
    from fastapi.testclient import TestClient

    from products.animind_core.app import app

    client = TestClient(app)
    headers = {"Authorization": "Bearer test-key", "X-Animind-User": "user_one"}
    monkeypatch.setenv("CORE_API_KEY", "test-key")
    found = client.get("/v1/personas/sheet", params={"name": "Ada Fixture", "lang": "pt-BR"}, headers=headers)
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["found"] is True
    assert body["wikiquote"] == "https://pt.wikiquote.org/wiki/Ada_Fixture"
    verified = [quote for quote in body["quotes"] if quote["verification"] == "verified"]
    missed = [quote for quote in body["quotes"] if quote["verification"] == "not_found"]
    assert [quote["text"] for quote in verified] == ["A verdade fixture cabe nesta frase curta."]
    assert verified[0]["url"] == body["wikiquote"]
    assert verified[0]["revision"] == 4242
    assert [quote["text"] for quote in missed] == ["Esta outra frase nao sera verificada nunca."]
    assert missed[0]["revision"] is None
    cached = tmp_path / "personas" / "users" / personas.accept_user("user_one") / "pt-BR" / "ada_fixture.yaml"
    assert cached.is_file()
    assert cached.is_relative_to(tmp_path)
    missing = client.get(
        "/v1/personas/sheet", params={"name": "Ninguem Inexistente Xy", "lang": "pt-BR"}, headers=headers,
    )
    assert missing.status_code == 404
    assert missing.json() == {"detail": "not found"}
    assert all(call.startswith("https://") for call in calls)
    assert "api.openai.com" not in "".join(calls)


@pytest.mark.skipif(os.getenv("ANIMIND_PERSONA_NETWORK") != "1", reason="live Wikimedia is opt-in")
def test_live_socrates_page_contains_a_real_quote():
    plain = personas.page_plain("https://pt.wikiquote.org/wiki/S%C3%B3crates")
    assert "filósofo" in plain.lower() or "filosofo" in personas.fold(plain)
