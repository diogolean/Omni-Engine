"""Script behaviour: real quotes stay literal and sourced, paraphrases are marked, beats keep their shape."""
import pytest

from products.animind_core import personas, script

WIKI = """{{Wikipedia}}
'''Sócrates''' foi um filósofo.

== Citações ==
=== Platão ===
* "Só sei que nada sei, e o fato de saber isso me coloca em vantagem sobre aqueles que acham que sabem alguma coisa."
:- [[Apologia de Sócrates]] 21d
* ''Apologia de Sócrates''
* "A vida sem exame não vale a pena ser vivida pelo homem."
:- Platão, ''[[s:Apologia de Sócrates|Apologia de Sócrates]]'', 38a
* "Uma frase curta."
* «Nenhum homem se torna sábio por acaso.»
:: Nota: tradução livre
:- Sêneca, carta 76

== Atribuídas ==
* "Esta citação é atribuída e não deve entrar na ficha da persona."

[[Categoria:Filósofos]]
"""
URL = "https://pt.wikiquote.org/wiki/S%C3%B3crates"


def sheet():
    qs = personas.pick(personas.parse_quotes(WIKI, site_lang="pt", page_url=URL))
    return {"name": "Sócrates", "found": True, "quotes": qs, "wikiquote": URL}


def test_parser_keeps_quotes_with_sources_and_skips_attributed():
    qs = sheet()["quotes"]
    texts = [q["text"] for q in qs]
    assert texts[0].startswith("Só sei que nada sei") and not texts[0].startswith('"')
    assert "A vida sem exame não vale a pena ser vivida pelo homem." in texts
    assert not any("atribuída" in t for t in texts)  # == Atribuídas == is not the persona's own words
    assert "Apologia de Sócrates" not in texts  # an italic title line is not a quote
    assert "Uma frase curta." not in texts  # under MIN_WORDS
    assert all(q["url"] == URL and q["id"].startswith("q") for q in qs)
    by = {q["text"]: q for q in qs}
    assert "Platão" in by["A vida sem exame não vale a pena ser vivida pelo homem."]["source"]
    assert by["A vida sem exame não vale a pena ser vivida pelo homem."]["wikisource"].startswith("https://pt.wikisource.org/")
    assert "Nota" not in by["Nenhum homem se torna sábio por acaso."]["source"]


def test_persona_name_cuts_descriptions():
    assert personas.persona_name("Sócrates, filósofo grego de toga") == "Sócrates"
    assert personas.persona_name("Nietzsche (bigode)") == "Nietzsche"


@pytest.mark.parametrize("template,n", [("debate", 2), ("lesson", 1), ("solo", 1)])
def test_every_quote_is_literal_and_sourced(template, n):
    sheets = [sheet()] * n
    out = script.write(template, "a vida e o saber", ["Sócrates", "Sócrates"][:n], sheets=sheets, use_llm=False)
    lines = out["lines"]
    assert script.check(lines, sheets) == []
    quotes = [line for line in lines if line["kind"] == "quote"]
    assert quotes, "the sheet has quotes, so quote beats must use them"
    sheet_texts = {q["text"] for q in sheets[0]["quotes"]}
    for line in quotes:
        assert line["text"] in sheet_texts and line["text"] == line["quote"]["text"]
        assert line["quote"]["url"].startswith("https://") and line["quote"]["source"]
    assert len({line["quote"]["id"] for line in quotes if line["speaker"] == 0}) == len([q for q in quotes if q["speaker"] == 0])


def test_debate_shape_and_pacing():
    lines = script.write("debate", "IA", ["A", "B"], sheets=[sheet(), sheet()], use_llm=False)["lines"]
    beats = [line["beat"] for line in lines]
    assert beats[0] == "gancho" and beats[-1] == "fechamento" and "tréplica" in beats and beats.count("réplica") >= 2
    assert all(a["speaker"] != b["speaker"] for a, b in zip(lines[1:-1], lines[2:-1]) if a["beat"] == "réplica")
    for line in lines:
        assert script.words(line["text"]) / script.WORDS_PER_S <= line["maxS"]
        assert 0 <= line["pauseAfterS"] <= 0.5
        if line["kind"] == "paraphrase":
            assert script.words(line["text"]) <= 16 + 1


def test_lesson_has_hook_three_points_example_summary():
    beats = [line["beat"] for line in script.write("lesson", "IA", ["S"], sheets=[sheet()], use_llm=False)["lines"]]
    assert beats == ["gancho", "ponto 1", "ponto 2", "ponto 3", "exemplo", "resumo"]


def test_no_sheet_means_marked_paraphrase_only():
    lines = script.write("debate", "IA", ["O cético", "X"], sheets=[{"quotes": []}, {"quotes": []}], use_llm=False)["lines"]
    assert {line["kind"] for line in lines} == {"paraphrase"} and not any("quote" in line for line in lines)


def test_writer_cannot_invent_or_steal_quotes(monkeypatch):
    s = sheet()
    real = s["quotes"][0]

    def fake(prompt):
        return [
            {"i": 0, "text": "“Eu inventei esta frase”, disse ele, e é falsa."},
            {"i": 1, "quoteId": "q999", "text": "Citação inventada pelo modelo."},  # unknown id -> a real sheet quote
            {"i": 2, "quoteId": real["id"]},  # other speaker's id must not be used for speaker 1
            {"i": 3, "text": real["text"]},  # an unmarked copy of a quote is rejected
            {"i": 4, "text": "Curta e boa.", "basisId": real["id"]},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    other = {"quotes": []}
    lines = script.write("debate", "IA", ["Sócrates", "B"], sheets=[s, other], use_llm=True)["lines"]
    assert script.check(lines, [s, other]) == []
    assert not any(c in lines[0]["text"] for c in script.QUOTE_MARKS)
    assert lines[1]["kind"] == "quote" and lines[1]["text"] in {q["text"] for q in s["quotes"]}
    assert lines[2]["speaker"] == 1 and lines[2]["kind"] == "paraphrase"
    assert lines[3]["text"] != real["text"]
    assert lines[4]["kind"] == "paraphrase" and "basis" not in lines[4]  # basis must be the speaker's own quote


def test_offline_load_does_not_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(personas, "build", lambda *a, **k: pytest.fail("network"))
    assert personas.load("Sócrates", fetch=False)["found"] is False


@pytest.mark.parametrize("template", ["debate", "lesson"])
def test_scene_marks_quotes_on_screen(template):
    import numpy as np

    from products.animind_core import scene

    q = sheet()["quotes"][0]
    lines = [{"speaker": 0, "beat": "tese", "kind": "quote", "text": q["text"], "quote": script.cite(q), "startMs": 0, "endMs": 6000},
             {"speaker": 1 if template == "debate" else 0, "beat": "réplica", "kind": "paraphrase", "text": "Prove.",
              "startMs": 6200, "endMs": 7000}]
    sc = scene.Scene(template=template, scenario="classroom", rigs=[None, None], script=lines, envelope=np.ones(800) * 0.5,
                     env_fps=100, duration_s=7.5, disclaimer="Paródia", names=["Sócrates", "B"], roles=["x", "y"])
    assert sc._tag(lines[0]) is not None and sc._tag(lines[1]) is not None
    assert len(sc._pages(lines[0])) >= 2 and sc._page_at(lines[0], 0.1) == 0 and sc._page_at(lines[0], 5.9) == len(sc._pages(lines[0])) - 1
    assert sc._pages(lines[0])[0][0].startswith("“") and sc._pages(lines[0])[-1][-1].endswith("”")
    assert sc.frame(3.0).size == (1280, 720)
