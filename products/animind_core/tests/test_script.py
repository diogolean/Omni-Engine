"""Script behaviour: real quotes stay literal and sourced, paraphrases are marked, beats keep their shape."""
import json

import pytest

from products.animind_core import personas, script

_PROVIDER_KEYS = ("DEEPSEEK_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY")


@pytest.fixture(autouse=True)
def _no_live_script_providers(monkeypatch):
    """A developer key in the environment must not turn these tests into live calls."""
    for name in _PROVIDER_KEYS:
        monkeypatch.delenv(name, raising=False)


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
    assert any(t.startswith("Só sei que nada sei") for t in texts) and not any(t.startswith('"') for t in texts)
    assert "A vida sem exame não vale a pena ser vivida pelo homem." in texts
    assert not any("atribuída" in t for t in texts)  # == Atribuídas == is not the persona's own words
    assert "Apologia de Sócrates" not in texts  # an italic title line is not a quote
    assert "Uma frase curta." not in texts  # under MIN_WORDS
    assert all(q["url"] == URL and q["id"].startswith("q") for q in qs)
    by = {q["text"]: q for q in qs}
    assert "Platão" in by["A vida sem exame não vale a pena ser vivida pelo homem."]["source"]
    assert by["A vida sem exame não vale a pena ser vivida pelo homem."]["wikisource"].startswith("https://pt.wikisource.org/")
    assert "Nota" not in by["Nenhum homem se torna sábio por acaso."]["source"]


def test_short_names_do_not_attach_to_a_different_person():
    assert personas.title_matches("Ben", "Benjamin Franklin") is False
    assert personas.title_matches("Ben", "Ben Franklin") is False
    assert personas.title_matches("Ada", "Ada Lovelace") is False
    assert personas.title_matches("Benjamin Franklin", "Benjamin Franklin") is True
    assert personas.title_matches("Nietzsche", "Friedrich Nietzsche") is True
    assert personas.title_matches("Sócrates", "Sócrates") is True


def test_stray_verdict_line_fails_script_qa():
    line = {"speaker": 0, "kind": "paraphrase", "text": "Child reaches one final verdict, Ada: test it in practice", "beat": "fechamento"}
    problems = script.check([line], [{"quotes": []}], "Why students should verify claims made by AI", ["Ada", "Ben"])
    assert any("incoherent" in problem for problem in problems)


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
        assert 0 <= line["pauseAfterS"] <= 0.8
        if line["kind"] == "paraphrase":
            assert script.words(line["text"]) <= 16 + 1


LESSON_BEATS = ["gancho", "pergunta", "citação", "exemplo", "virada", "síntese"]


def test_lesson_progresses_question_quote_example_synthesis():
    beats = [line["beat"] for line in script.write("lesson", "IA", ["S"], sheets=[sheet()], use_llm=False)["lines"]]
    assert beats == LESSON_BEATS


def test_no_sheet_means_marked_paraphrase_only():
    lines = script.write("debate", "IA", ["O cético", "X"], sheets=[{"quotes": []}, {"quotes": []}], use_llm=False)["lines"]
    assert {line["kind"] for line in lines} == {"paraphrase"} and not any("quote" in line for line in lines)


def test_writer_cannot_invent_or_steal_quotes(monkeypatch):
    s = sheet()
    real = s["quotes"][0]

    def fake(prompt):  # debate arc: 0 hook (A), 1 thesis quote (B), 4 turn quote (A), 5 reply (B)
        return [
            {"i": 0, "text": "“Eu inventei esta frase”, disse ele, e é falsa."},
            {"i": 1, "quoteId": "q999", "text": "Citação inventada pelo modelo."},  # unknown id -> a real sheet quote
            {"i": 4, "quoteId": real["id"]},  # the other speaker's id must not be used for speaker 0
            {"i": 3, "text": "Uma fala escrita, sem citação."},
            {"i": 6, "text": "Curta e boa.", "basisId": real["id"]},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    other = {"quotes": []}
    lines = script.write("debate", "IA", ["B", "Sócrates"], sheets=[other, s], use_llm=True)["lines"]
    assert script.check(lines, [other, s]) == []
    assert not any(c in lines[0]["text"] for c in script.QUOTE_MARKS)
    assert lines[1]["speaker"] == 1 and lines[1]["kind"] == "quote" and lines[1]["text"] in {q["text"] for q in s["quotes"]}
    assert lines[4]["speaker"] == 0 and lines[4]["kind"] == "paraphrase"
    assert lines[3]["kind"] == "paraphrase"
    assert lines[6]["kind"] == "paraphrase" and "basis" not in lines[6]  # basis must be the speaker's own quote


def test_offline_load_does_not_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(personas, "build", lambda *a, **k: pytest.fail("network"))
    assert personas.load("Sócrates", fetch=False)["found"] is False


@pytest.mark.parametrize("template", ["debate", "lesson"])
def test_scene_marks_quotes_on_screen(template):
    import numpy as np

    from products.animind_core import scene

    q = max(sheet()["quotes"], key=lambda x: len(x["text"]))
    lines = [{"speaker": 0, "beat": "tese", "kind": "quote", "text": q["text"], "quote": script.cite(q), "startMs": 0, "endMs": 6000},
             {"speaker": 1 if template == "debate" else 0, "beat": "réplica", "kind": "paraphrase", "text": "Prove.",
              "startMs": 6200, "endMs": 7000}]
    sc = scene.Scene(template=template, scenario="classroom", rigs=[None, None], script=lines, envelope=np.ones(800) * 0.5,
                     env_fps=100, duration_s=7.5, disclaimer="Paródia", names=["Sócrates", "B"], roles=["x", "y"])
    assert sc._tag(lines[0]) is not None and sc._tag(lines[1]) is not None
    assert len(sc._pages(lines[0])) >= 2 and sc._page_at(lines[0], 0.1) == 0 and sc._page_at(lines[0], 5.9) == len(sc._pages(lines[0])) - 1
    assert sc._pages(lines[0])[0][0].startswith("“") and sc._pages(lines[0])[-1][-1].endswith("”")
    assert sc.frame(3.0).size == (1280, 720)


# Modeled on the real pt.wikiquote pages (checked 2026-10-30): Sócrates has == Platão == with a
# === Apologia === subsection that also holds "Revista Nova Escola" and "citado em" entries, then
# == Plutarco == (a note only), == Atribuídas ==, == Sobre Sócrates ==; Nietzsche has === Verificadas ===
# (German original + ::: citation), one === <book> === section per work, == Atribuídas == ("como citado in").
SOCRATES_PAGE = """{{Info/Biografia/Wikidata}}
=== Verificadas ===
; Sócrates não deixou escritos.

==Platão==
* As palavras falsas não são apenas más em si, mas também infectam a alma com o mal.
**''Fédon'', 91

===''[[w:Apologia de Sócrates|Apologia de Sócrates]]''===
*Prefiro morrer tendo falado a minha maneira, do que falar da sua maneira e viver.
**38e-39a

* "O princípio dos [[raciocínio]]s é constituído pela [[essência]] das coisas"
:- ''Fonte: [http://revistaescola.abril.com.br/x.shtml Revista Nova Escola, 179, jan-fev05]''

* "O mais razoável que se possa dizer sobre o [[matrimônio]] é isto: haja o que haja você se arrependerá."
::- ''citado em "Speaker's handbook of epigrams and witticisms" - Página 40, Harper, 1955

* "A verdade sobre o raciocínio é que ele nasce da dúvida honesta de cada um."
:- Frase atribuída a Sócrates, sem fonte primária

==Plutarco==
*Não sou um ateniense ou um grego, mas um cidadão do mundo.
**Nota: Comparar ''Críton'' de Platão.

==Atribuídas==
* "Se alguém procura a saúde pergunta-lhe primeiro se está disposto a evitar as causas da doença."
::- ''[[Sócrates]] como citado in: Filosofia Para O Dia A Dia - Página 182

==Sobre Sócrates==
* "Sócrates foi o homem mais sábio de Atenas, disse o oráculo de Delfos."

[[Categoria:Filósofos]]
"""
NIETZSCHE_PAGE = """{{Info/Biografia/Wikidata}}
=== Verificadas ===
* "Não há fatos eternos, como não há [[verdade]]s absolutas".
::- es gibt keine ewigen Tatsachen: so wie es keine absoluten Wahrheiten gibt.
:::- Sachregister - Volume 23 de Gesammelte Werke - Página 289, [[Friedrich Wilhelm Nietzsche]], Musarion, 1929

* "A maturidade do homem consiste em ter reencontrado a seriedade que em criança se colocava nos jogos."
::- ''Werke - volume 2, página 629, Friedrich Wilhelm Nietzsche - C. Hanser, 1956

=== O Crepúsculo dos Ídolos ===
[https://books.google.com.br/books?id=br4 O Crepúsculo dos Ídolos], [[Friedrich Nietzsche]]; Hemus, 2004

* "O que não me mata me fortalece."
::- [https://books.google.com.br/books?id=br4&pg=PA9 Página 10], Máximas e Sátiras, 8

* "Sem música a vida seria um erro."
::- [https://books.google.com.br/books?id=br4&pg=PA13 Página 13] Máximas e Sátiras, 33

=== Humano, Demasiado Humano ===
* "As convicções são inimigas mais perigosas da verdade do que as mentiras."
::- Aforismo 483

==Atribuídas==
* "A [[fé]] é querer ignorar tudo aquilo que é [[verdade]]."
::- ''[[Nietzsche]] como citado in Vidas e Obras - Página 281, Ferrari, 2007

==Sobre==
* "A filosofia moderna foi e é nietzscheana, porque ele não parou de falar de sentido."
:- ''[[Gilles Deleuze]], filósofo francês''

[[Categoria:Pessoas]]
"""


def real_sheet(page, name, url):
    return {"name": name, "found": True, "wikiquote": url,
            "quotes": personas.pick(personas.parse_quotes(page, site_lang="pt", page_url=url))}


def socrates():
    return real_sheet(SOCRATES_PAGE, "Sócrates", URL)


def nietzsche():
    return real_sheet(NIETZSCHE_PAGE, "Nietzsche", "https://pt.wikiquote.org/wiki/Friedrich_Nietzsche")


def test_sheet_ranks_primary_sources_first_and_drops_disputed():
    qs = socrates()["quotes"]
    by = {q["text"]: q for q in qs}
    primary = [q["text"] for q in qs if q["sourceKind"] == "primary"]
    assert primary == ["As palavras falsas não são apenas más em si, mas também infectam a alma com o mal.",
                       "Prefiro morrer tendo falado a minha maneira, do que falar da sua maneira e viver."]
    magazine = by["O princípio dos raciocínios é constituído pela essência das coisas"]
    assert magazine["sourceKind"] == "secondary" and "Nova Escola" in magazine["source"]
    assert "Apologia" not in magazine["source"]  # the magazine is not the dialogue
    assert by["Não sou um ateniense ou um grego, mas um cidadão do mundo."]["sourceKind"] == "secondary"  # no work named
    assert by[next(t for t in by if t.startswith("O mais razoável"))]["sourceKind"] == "secondary"  # "citado em" a handbook
    assert [q["sourceKind"] for q in qs] == sorted(q["sourceKind"] for q in qs)  # all primary before any secondary
    texts = " ".join(by)
    assert "nasce da dúvida" not in texts  # flagged "atribuída" in its source line
    assert "procura a saúde" not in texts and "oráculo" not in texts  # == Atribuídas == and == Sobre == sections
    assert "Apologia de Sócrates" in by["Prefiro morrer tendo falado a minha maneira, do que falar da sua maneira e viver."]["source"]
    n = {q["text"]: q for q in nietzsche()["quotes"]}
    assert n["O que não me mata me fortalece."]["source"].startswith("O Crepúsculo dos Ídolos · Página 10")
    assert n["Não há fatos eternos, como não há verdades absolutas"]["sourceKind"] == "secondary"  # an index page
    assert all(q["sourceKind"] == "primary" for t, q in n.items() if not t.startswith("Não há fatos"))
    assert not any("fé é querer" in t for t in n)


def test_index_or_register_pages_are_not_primary_sources():
    """A "Sachregister"/"Índice" page lists where words occur; it is not the work, so never a "Citação real"."""
    n = {q["text"]: q for q in nietzsche()["quotes"]}
    q = n["Não há fatos eternos, como não há verdades absolutas"]
    assert q["source"].startswith("Sachregister") and not personas.is_primary(q)
    # sheets cached before the fix still say "primary": judged by the source text
    assert not personas.is_primary({"source": "Sachregister - Volume 23 de Gesammelte Werke - Página 289", "sourceKind": "primary"})
    assert not personas.is_primary({"source": "Índice remissivo, p. 12", "sourceKind": "primary"})
    assert personas.is_primary({"source": "O Crepúsculo dos Ídolos · Página 10", "sourceKind": "primary"})
    assert personas.is_primary({"source": "Ecce Homo, Por que sou tão sábio, §1", "sourceKind": "primary"})
    assert personas.SHEET_VERSION >= 3  # cached sheets get rebuilt with the new rule


def test_real_quote_lines_never_use_secondary_sources(monkeypatch):
    s, n = socrates(), nietzsche()
    secondary = {q["id"] for q in s["quotes"] if q["sourceKind"] == "secondary"}
    monkeypatch.setattr(script, "_gemini", lambda prompt: [{"i": 4, "quoteId": sorted(secondary)[0]}, {"i": 8, "quoteId": sorted(secondary)[-1]}])
    # the topic matches the magazine quote best; it still must not be a "Citação real"
    out = script.write("debate", "o raciocínio e a essência das coisas", ["Sócrates", "Nietzsche"], sheets=[s, n], use_llm=True)
    assert script.check(out["lines"], [s, n]) == []
    quotes = [line for line in out["lines"] if line["kind"] == "quote"]
    assert quotes and not any(line["quote"]["id"] in secondary for line in quotes if line["speaker"] == 0)
    assert all("Nova Escola" not in line["quote"]["source"] and "citado em" not in line["quote"]["source"] for line in quotes)
    assert out["personas"][0]["primaryQuotes"] == 2


def test_written_line_that_is_a_real_quote_is_marked_with_its_source(monkeypatch):
    s, n = socrates(), nietzsche()
    ids = {q["text"][:12]: q["id"] for q in n["quotes"]}
    # Nietzsche's quote beat takes another quote; then the writer puts his real line in a written beat
    # (fechamento, speaker 1) with other casing/punctuation, and a 7-word excerpt of Sócrates in another
    def fake(prompt):
        if "Rewrite only line 2" in prompt:
            return [{"i": 2, "text": "Essa frase ainda pede uma prova."}]
        return [
            {"i": 1, "quoteId": ids["As convicçõe"]},
            {"i": 7, "text": "o que NÃO me mata, me fortalece!"},
            {"i": 2, "text": "Prefiro morrer tendo falado a minha maneira"},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    lines = script.write("debate", "a verdade", ["Sócrates", "Nietzsche"], sheets=[s, n], use_llm=True)["lines"]
    assert script.check(lines, [s, n]) == []
    close = lines[7]
    assert close["speaker"] == 1 and close["kind"] == "quote"
    assert close["text"] == "O que não me mata me fortalece."  # the sheet's literal text, not the writer's
    assert "Crepúsculo dos Ídolos" in close["quote"]["source"] and close["quote"]["url"].startswith("https://")
    # 7 of 15 words is too little to call it the quote: it stays a marked paraphrase and is not an unmarked copy
    assert lines[2]["kind"] == "paraphrase" and not script.copies_quote(lines[2]["text"], s)
    texts = [line["text"] for line in lines if line["kind"] == "quote"]
    assert len(texts) == len(set(texts))  # never the same quote twice


def test_quote_beats_prefer_the_topic_and_pacing_stays_tight(monkeypatch):
    s, n = socrates(), nietzsche()
    off_topic = next(q["id"] for q in n["quotes"] if q["text"].startswith("Sem música"))

    def fake(prompt):
        # The quote answers the hook, so a later pass must not swap it for a more topical one.
        return [
            {"i": 0, "text": "Nietzsche, sem música a verdade existe?"},
            {"i": 1, "quoteId": off_topic},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write("debate", "a verdade", ["Sócrates", "Nietzsche"], sheets=[s, n], use_llm=True)
    lines = out["lines"]
    # the writer picked this quote; a later pass must not swap it for a more topical one
    assert lines[1]["kind"] == "quote" and lines[1]["text"].startswith("Sem música")
    assert lines[1]["quote"]["id"] == off_topic
    assert all("Sachregister" not in line["quote"]["source"] for line in lines if line["kind"] == "quote")
    assert [line["beat"] for line in lines][0] == "gancho" and lines[-1]["beat"] == "fechamento"
    assert all(a["speaker"] != b["speaker"] for a, b in zip(lines, lines[1:]))  # strict alternation, hook included
    for line in lines:
        limit = 16 if line["kind"] == "paraphrase" else 30
        assert script.words(line["text"]) <= limit + 1
    assert sum(script.words(line["text"]) for line in lines) <= 140  # about a minute of speech


def test_tidy_cleans_punctuation():
    assert script.tidy("a verdade existe ?.") == "A verdade existe?"
    assert script.tidy("verdade ,  existe ..") == "Verdade, existe."
    assert script.tidy("existe? barba, responda") == "Existe? Barba, responda."


def test_source_label_is_short_and_readable():
    assert script.source_label("Nietzsches Werke\u200e - Página 183, Friedrich Wilhelm Nietzsche - Alfred Kröner, 1921") == "Nietzsches Werke"
    assert script.source_label("Humano, demasiado humano, página 9, Friedrich Nietzsche, Traduzido por X") == "Humano, demasiado humano"
    assert script.source_label("Platão · Apologia de Sócrates · 38e-39a") == "Platão · Apologia de Sócrates · 38e-39a"
    assert len(script.source_label("x" * 200)) <= 48


def test_offline_debate_quotes_reply_and_never_repeats():
    s, n = socrates(), nietzsche()
    out = script.write("debate", "a verdade existe?", ["Sócrates", "Nietzsche"], sheets=[s, n], use_llm=False)
    lines = out["lines"]
    texts = [line["text"] for line in lines]
    assert len(set(texts)) == len(texts)  # no line twice
    quotes = [line for line in lines if line["kind"] == "quote"]
    assert out["quoteLines"] == len(quotes) >= 2
    for q in quotes:  # every quote is the sheet's own text, with a short on-screen label
        sheet = s if q["speaker"] == 0 else n
        assert any(q["text"] == item["text"] for item in sheet["quotes"])
        assert q["quote"]["label"] and len(q["quote"]["label"]) <= 48
    for line in lines:
        assert line["text"][0].isupper() and line["text"][-1] in ".?!…" and " ," not in line["text"]
    # written replies address the other speaker by name
    names = ["Sócrates", "Nietzsche"]
    for line in lines[1:]:
        if line["kind"] == "paraphrase":
            assert names[1 - line["speaker"]] in line["text"]


def test_offline_lesson_quotes_stay_on_the_thread():
    out = script.write("lesson", "a verdade existe?", ["Sócrates"], sheets=[socrates()], use_llm=False)
    lines = out["lines"]
    assert [line["beat"] for line in lines] == LESSON_BEATS
    assert out["quoteLines"] >= 1 and all(line["kind"] == "quote" for line in lines if line["beat"] == "citação")
    assert out["scriptQA"]["pass"], out["scriptQA"]
    # the fixture sheet has a single quote about truth: the turn is written, never an off-topic quote
    turn = lines[LESSON_BEATS.index("virada")]
    assert turn["kind"] == "paraphrase" and turn["act"] == "virada"
    assert lines[1]["text"].endswith("?")  # the lesson opens on a question, not on "Segundo:" without a "Primeiro"
    assert not any(t in line["text"] for line in lines for t in ("Segundo", "sobre verdade"))


def test_offline_debate_has_a_turn_and_closes_on_the_strongest_quote():
    out = script.write("debate", "A verdade existe?", ["Sócrates", "Nietzsche"], lang="pt-BR",
                       sheets=[socrates(), nietzsche()], use_llm=False)
    lines = out["lines"]
    texts = " ".join(line["text"] for line in lines)
    assert "Sem música" not in texts  # v17 used an off-topic quote as a reply
    assert " de a " not in texts and " em o " not in texts  # molds contract articles in pt-BR
    assert lines[-1]["kind"] == "quote" and lines[-1]["speaker"] == 0 and lines[-1]["pauseAfterS"] == 0
    # the counter-reply answers the opponent's line: its key word, or his certainty when he quoted
    tre = next(i for i, line in enumerate(lines) if line["beat"] == "tréplica")
    assert lines[tre - 1]["speaker"] == 1


def test_lines_carry_rhythm_quotes_slower_jabs_faster():
    lines = script.write("debate", "a verdade existe?", ["Sócrates", "Nietzsche"], sheets=[socrates(), nietzsche()], use_llm=False)["lines"]
    assert all(isinstance(line["rateAdj"], int) and -10 <= line["rateAdj"] <= 10 for line in lines)
    assert all(line["rateAdj"] < 0 for line in lines if line["beat"] == "tese")
    assert any(line["rateAdj"] > 0 for line in lines if line["kind"] == "paraphrase")
    assert len({line["pauseAfterS"] for line in lines}) >= 3  # pauses vary with the beat, not one flat gap


@pytest.mark.parametrize("topic", ["A verdade existe?", "O que é a felicidade?", "inteligência artificial", "A morte é o fim?"])
def test_offline_molds_never_repeat_inside_a_script(topic):
    for template, names, sheets in (("debate", ["Sócrates", "Nietzsche"], [socrates(), nietzsche()]),
                                    ("lesson", ["Sócrates"], [socrates()])):
        lines = script.write(template, topic, names, lang="pt-BR", sheets=sheets, use_llm=False)["lines"]
        heads = [" ".join(line["text"].split()[:2]) for line in lines if line["kind"] == "paraphrase"]
        assert len(set(heads)) == len(heads), heads


def test_keyword_prefers_nouns():
    assert script.keyword("Prefiro morrer tendo falado a minha maneira", "a verdade") == "maneira"
    assert script.keyword("É preciso que nunca avaliemos um artista pela medida de suas obras.", "arte") == "artista"
    assert script.keyword("As palavras falsas infectam a alma", "a verdade") == "falsa"


def test_gemini_answers_are_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(script, "_gemini_call", lambda prompt: calls.append(prompt) or [{"i": 0, "text": "Olá."}])
    assert script._gemini("p1") == [{"i": 0, "text": "Olá."}]
    assert script._gemini("p1") == [{"i": 0, "text": "Olá."}]
    assert len(calls) == 1 and list((tmp_path / "cache" / "script").glob("*.json"))


PAIRS = [(["Sócrates", "Nietzsche"], lambda: [socrates(), nietzsche()]),
         (["Nietzsche", "Sócrates"], lambda: [nietzsche(), socrates()])]
TOPICS = ["A verdade existe?", "O que é a felicidade?", "inteligência artificial", "A morte é o fim?", "o amor"]


@pytest.mark.parametrize("pair", range(len(PAIRS)))
@pytest.mark.parametrize("topic", TOPICS)
def test_debate_is_a_real_exchange_with_an_arc(topic, pair):
    names, make = PAIRS[pair]
    sheets = make()
    out = script.write("debate", topic, names, lang="pt-BR", sheets=sheets, use_llm=False)
    lines = out["lines"]
    assert out["scriptQA"]["pass"] and out["scriptQA"]["problems"] == [], out["scriptQA"]
    # every spoken quote is literally in the speaker's own sheet, with its source
    for line in lines:
        if line["kind"] == "quote":
            own = {q["id"]: q["text"] for q in sheets[line["speaker"]]["quotes"]}
            assert own[line["quote"]["id"]] == line["text"] == line["quote"]["text"]
            assert line["quote"]["source"] and line["quote"]["url"].startswith("https://")
    # each speaker uses at least one real quote of his own
    assert {line["speaker"] for line in lines if line["kind"] == "quote"} == {0, 1}
    # every line answers the one before: speakers alternate and the reply picks up an idea of the previous line
    for i in range(1, len(lines)):
        assert lines[i]["speaker"] != lines[i - 1]["speaker"]
        reply = lines[i]["replyTo"]
        assert reply["line"] == i - 1 and reply["idea"], (i, lines[i - 1]["text"], lines[i]["text"])
        assert reply["idea"] == script.link(lines[i - 1]["text"], lines[i]["text"], quote=lines[i]["kind"] == "quote", names=names)
    # arc: hook, clash, a turn in the middle, a close
    acts = [line["act"] for line in lines]
    assert acts[0] == "abertura" and acts[-1] == "fechamento" and lines[0]["beat"] == "gancho"
    turn = next(i for i, line in enumerate(lines) if line["beat"] == "virada")
    assert len(lines) / 4 <= turn <= 3 * len(lines) / 4 and lines[turn]["kind"] == "quote"
    # short, varied lines
    counts = [script.words(line["text"]) for line in lines]
    for line, w in zip(lines, counts):
        assert w <= (script.QUOTE_MAX_WORDS if line["kind"] == "quote" else script.PARAPHRASE_MAX_WORDS + 1)
    assert min(counts) <= 9 and len(set(counts)) >= len(counts) // 2


def test_link_needs_a_shared_idea_not_a_name_or_a_mold_word():
    assert script.link("Vontade de potência, Sócrates.", "Vontade? Prove, Nietzsche.") == "vontade"
    assert script.link("Prove, Nietzsche.", "Sócrates, você de novo.", names=["Sócrates", "Nietzsche"]) == ""
    assert script.link("Isso é medo de admitir.", "Não te acovardes diante de tuas ações!", quote=True) == "medo~acovardes"
    assert script.link("Isso é medo de admitir.", "Não te acovardes diante de tuas ações!") == ""  # concepts only for quotes


def test_check_debate_flags_monologues_fake_quotes_and_long_lines():
    s, n = socrates(), nietzsche()
    good = script.write("debate", "A verdade existe?", ["Sócrates", "Nietzsche"], sheets=[s, n], use_llm=False)["lines"]
    bad = [dict(line) for line in good]
    bad[2] = {**bad[2], "kind": "paraphrase", "text": "Gatos dormem muito durante a tarde inteira, Nietzsche."}
    bad[3] = {**bad[3], "speaker": 0}
    bad[5] = {**bad[5], "text": " ".join(["palavra"] * 20) + "."}
    fake = {**bad[4], "text": "Uma frase que Sócrates nunca disse."}
    fake["quote"] = {**bad[4]["quote"], "text": fake["text"]}
    bad[4] = fake
    problems = script.check(bad, [s, n]) + script.check_debate(bad, [s, n], ["Sócrates", "Nietzsche"])
    notes = script.answer_notes(bad, ["Sócrates", "Nietzsche"])
    assert "line 4: quote is not literally in the sheet" in problems
    assert "line 2: does not answer line 1" not in problems
    assert any("line 2: keyword overlap" in note for note in notes)
    assert "line 3: same speaker twice in a row" in problems
    assert "line 5: 20 words is too long" in problems


def test_llm_lines_are_not_replaced_with_template_filler(monkeypatch):
    s, n = socrates(), nietzsche()
    monkeypatch.setattr(script, "_gemini", lambda prompt: [
        {"i": i, "text": "Gatos dormem muito durante a tarde."} for i in (0, 2, 3, 5, 6, 7)])
    out = script.write("debate", "A verdade existe?", ["Sócrates", "Nietzsche"], sheets=[s, n], use_llm=True)
    written = [line["text"] for line in out["lines"] if line["beat"] != "citação"]
    assert any("Gatos dormem muito durante a tarde." in text for text in written)
    filler = ("Life reveals consequences", "only an opinion", "sem isso", "A vida revela")
    assert not any(phrase in text for text in written for phrase in filler)


def test_prompt_describes_the_arc():
    names = ["Sócrates", "Nietzsche"]
    prompt = script._prompt(
        script.plan_for("debate", 2, 30), [socrates(), nietzsche()], names, "a verdade", "pt-BR", "debate",
        duration=30, words_per_s=2.4,
    )
    assert "act: virada" in prompt and "act: abertura" in prompt
    assert "repeat one content word" in prompt and "at least one of his real quotes" in prompt
    assert "at most 14 words" in prompt and "at most 16 words" in prompt
    assert "at most 9 words" in prompt and "at most 12 words" in prompt
    assert "quoteId" in prompt and "Spoken word budget" in prompt and "2.40 words/second" in prompt


# --- quote fidelity and lesson/debate quality (scriptQA) ---------------------------------------------------------

LESSON_TOPICS = ["A verdade existe?", "Deus está morto?", "o conhecimento", "A morte é o fim?", "a vida e o saber"]


@pytest.mark.parametrize("who", ["Sócrates", "Nietzsche"])
@pytest.mark.parametrize("topic", LESSON_TOPICS)
def test_lesson_is_a_real_lesson_with_real_quotes(topic, who):
    sheets = [socrates() if who == "Sócrates" else nietzsche()]
    out = script.write("lesson", topic, [who], sheets=sheets, use_llm=False)
    lines = out["lines"]
    assert out["scriptQA"]["pass"] and out["scriptQA"]["problems"] == [], out["scriptQA"]
    assert [line["beat"] for line in lines] == LESSON_BEATS
    assert [line["act"] for line in lines] == ["abertura", "abertura", "desenvolvimento", "desenvolvimento", "virada", "fechamento"]
    assert out["quoteLines"] >= 1
    own = {q["id"]: q for q in sheets[0]["quotes"]}
    for i, line in enumerate(lines):
        if line["kind"] == "quote":  # literal, with the sheet's own source and link
            q = own[line["quote"]["id"]]
            assert line["text"] == q["text"] and line["quote"]["source"] == q["source"] and line["quote"]["url"] == q["url"]
            assert script.on_thread(line["text"], lines[i - 1]["text"], topic) or script.link(line["text"], lines[i + 1]["text"])
        else:
            assert not any(c in line["text"] for c in script.QUOTE_MARKS) and not script.copies_quote(line["text"], sheets[0])
    assert script.strong_hook(lines[0]["text"]) and lines[1]["text"].endswith("?")
    assert script.words(lines[-1]["text"]) <= script.CLOSE_MAX_WORDS
    counts = [script.words(line["text"]) for line in lines]
    assert len(set(counts)) >= len(counts) // 2


def test_lesson_takes_a_second_quote_only_when_it_stays_on_the_thread():
    n = nietzsche()
    out = script.write("lesson", "Deus está morto?", ["Nietzsche"], sheets=[n], use_llm=False)
    lines = out["lines"]
    for i, line in enumerate(lines):
        if line["kind"] == "quote":
            assert script.on_thread(line["text"], lines[i - 1]["text"], "Deus está morto?") or script.link(line["text"], lines[i + 1]["text"])


@pytest.mark.parametrize("template,names", [("debate", ["Sócrates", "Nietzsche"]), ("lesson", ["Sócrates"]), ("solo", ["Nietzsche"])])
def test_script_qa_is_reported_for_every_template(template, names):
    sheets = [socrates() if nm == "Sócrates" else nietzsche() for nm in names]
    qa = script.write(template, "A verdade existe?", names, sheets=sheets, use_llm=False)["scriptQA"]
    assert qa["pass"] and qa["problems"] == [] and isinstance(qa["notes"], list), qa


def _lesson(topic="A verdade existe?"):
    s = nietzsche()
    return s, script.write("lesson", topic, ["Nietzsche"], sheets=[s], use_llm=False)["lines"]


def test_check_rejects_invented_and_paraphrased_quotes():
    s, lines = _lesson()
    i = next(k for k, line in enumerate(lines) if line["kind"] == "quote")
    real = lines[i]["quote"]
    # a paraphrase of a real quote presented as literal (same id, reworded text)
    words = real["text"].rstrip(".").split()
    reworded = " ".join(words[:-2] + ["no", "fundo"]) + "."
    para = {**lines[i], "text": reworded, "quote": {**real, "text": reworded}}
    problems = script.check(lines[:i] + [para] + lines[i + 1:], [s])
    assert f"line {i}: quote is not literally in the sheet" in problems
    assert any(p.startswith(f"line {i}: paraphrased quote shown as literal") for p in problems), problems
    # an invented quote with a real-looking id and source
    fake = "A internet é o martelo com que se filosofa hoje."
    inv = {**lines[i], "text": fake, "quote": {**real, "text": fake}}
    problems = script.check(lines[:i] + [inv] + lines[i + 1:], [s])
    assert f"line {i}: invented quote (nothing like it in the sheet)" in problems
    # a real quote with a source or link that is not the sheet's
    for tamper, msg in (({"source": "Assim falou Zaratustra, cap. 99"}, "quote source differs from the sheet"),
                        ({"source": ""}, "quote has no source"),
                        ({"url": "https://example.com/frases"}, "quote has no source link")):
        bad = {**lines[i], "quote": {**real, **tamper}}
        assert f"line {i}: {msg}" in script.check(lines[:i] + [bad] + lines[i + 1:], [s])
    assert script.check(lines, [s]) == []


@pytest.mark.parametrize("text,msg", [
    ("Como eu escrevi: a verdade é só vontade de poder.", "paraphrase posed as a literal quote"),
    ("Nas palavras de Nietzsche, a verdade é um exército de metáforas.", "paraphrase posed as a literal quote"),
    ("Palavra por palavra: Deus está morto e fomos nós.", "paraphrase posed as a literal quote"),
    ("Ele disse “a verdade é feia” e saiu.", "paraphrase uses quotation marks"),
])
def test_check_rejects_paraphrase_presented_as_literal(text, msg):
    s, lines = _lesson()
    i = next(k for k, line in enumerate(lines) if line["kind"] == "paraphrase" and k > 0)
    bad = lines[:i] + [{**lines[i], "text": text}] + lines[i + 1:]
    assert any(p.startswith(f"line {i}: {msg}") for p in script.check(bad, [s])), script.check(bad, [s])


def test_check_rejects_a_real_quote_smuggled_into_a_written_line_or_a_written_line_labelled_as_quote():
    s, lines = _lesson()
    i = next(k for k, line in enumerate(lines) if line["kind"] == "paraphrase" and k > 0)
    real = s["quotes"][0]["text"]
    smuggled = lines[:i] + [{**lines[i], "text": real}] + lines[i + 1:]
    assert f"line {i}: paraphrase repeats a real quote without marking it" in script.check(smuggled, [s])
    labelled = lines[:i] + [{**lines[i], "beat": "citação"}] + lines[i + 1:]
    assert f"line {i}: written line labelled as a quote" in script.check(labelled, [s])


def test_check_lesson_flags_weak_hook_missing_question_off_thread_quote_and_long_close():
    s, lines = _lesson()
    bad = [dict(line) for line in lines]
    bad[0]["text"] = "Hoje vamos conversar com calma e bastante tempo sobre um assunto muito importante para todos nós aqui."
    bad[1]["text"] = "Comece pela pergunta certa sobre a verdade."
    q = next(k for k, line in enumerate(bad) if line["kind"] == "quote")
    off = next(x for x in s["quotes"] if not script.on_thread(x["text"], bad[q - 1]["text"], "A verdade existe?")
               and not script.link(x["text"], bad[q + 1]["text"]))
    bad[q] = {**bad[q], "text": off["text"], "quote": {**bad[q]["quote"], "id": off["id"], "text": off["text"]}}
    bad[-1]["text"] = "Então, para resumir tudo o que vimos hoje, a verdade é algo que precisamos examinar com muito cuidado sempre."
    problems = script.check_lesson(bad, [s], "A verdade existe?")
    assert "lesson: weak hook (more than 14 words, no question, no short punch)" in problems
    assert "lesson: the question beat is not a question" in problems
    assert f"line {q}: quote is off the lesson's thread" in problems
    assert any(p.startswith("lesson: the close is too long") for p in problems)


def test_check_debate_wants_a_strong_hook_and_a_memorable_close():
    s, n = socrates(), nietzsche()
    good = script.write("debate", "A verdade existe?", ["Sócrates", "Nietzsche"], sheets=[s, n], use_llm=False)["lines"]
    assert script.check_debate(good, [s, n], ["Sócrates", "Nietzsche"], "A verdade existe?") == []
    bad = [dict(line) for line in good]
    bad[0]["text"] = "Nietzsche, hoje eu gostaria de conversar com você longamente sobre a verdade e outras coisas."
    bad[-1] = {**bad[-1], "kind": "paraphrase", "text": "Bom, acho que cada um de nós tem um pouco de razão e a conversa poderia continuar amanhã.",
               "quote": None}
    problems = script.check_debate(bad, [s, n], ["Sócrates", "Nietzsche"], "A verdade existe?")
    assert "debate: weak hook (more than 14 words, no question, no short punch)" in problems
    assert any(p.startswith("debate: the close is not memorable") for p in problems), problems


def test_llm_line_that_claims_to_be_literal_is_rewritten(monkeypatch):
    s = nietzsche()

    def fake(prompt):
        if "Rewrite only line 3" in prompt:
            return [{"i": 3, "text": "A verdade é uma metáfora gasta."}]
        if "Rewrite only line 5" in prompt:
            return [{"i": 5, "text": "Deus morreu e a verdade também."}]
        return [
            {"i": 3, "text": "Como eu escrevi: a verdade é só uma metáfora gasta."},
            {"i": 5, "text": "Nas minhas palavras, Deus morreu e a verdade também."},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write("lesson", "A verdade existe?", ["Nietzsche"], sheets=[s], use_llm=True)
    assert not any("escrevi" in line["text"] or "Nas minhas palavras" in line["text"] for line in out["lines"])
    assert out["scriptQA"]["pass"], out["scriptQA"]


def test_debate_transcript_keeps_the_writer_ending(monkeypatch):
    import numpy as np

    from products.animind_core import scene

    plan = script.plan_for("debate", 2)
    hold = "I want us to keep on testing it from here every perspective we can hold."
    truest = "What he said is careful but it is not always the truest."
    drafts = []
    used = {"hold": False, "truest": False}
    for index, beat in enumerate(plan):
        if beat.kind == "quote":
            continue
        if not used["hold"] and script.words(hold) > beat.max_words:
            drafts.append({"i": index, "text": hold})
            used["hold"] = True
        elif not used["truest"] and script.words(truest) > beat.max_words:
            drafts.append({"i": index, "text": truest})
            used["truest"] = True
        else:
            drafts.append({"i": index, "text": f"Knowledge still needs line {index} checked."})
    assert used == {"hold": True, "truest": True}

    monkeypatch.setattr(script, "_gemini", lambda prompt: drafts)
    out = script.write(
        "debate", "knowledge", ["Ada", "Ben"], "en-US",
        sheets=[{"quotes": []}, {"quotes": []}], use_llm=True,
    )
    texts = [line["text"] for line in out["lines"]]
    assert any(text.endswith("we can hold.") for text in texts), texts
    assert any(text.endswith("the truest.") for text in texts), texts
    timed = []
    cursor = 2000
    for line in out["lines"]:
        timed.append({**line, "startMs": cursor, "endMs": cursor + 2000})
        cursor += 2300
    sc = scene.Scene(
        template="debate", scenario="studio", rigs=[None, None], script=timed,
        envelope=np.ones(800), env_fps=100, duration_s=cursor / 1000 + 3,
        disclaimer="Parody", names=["Ada", "Ben"], lang="en-US",
    )
    captions = [" ".join(row for page in sc._pages(line) for row in page) for line in sc.script]
    assert any(caption.endswith("we can hold.") for caption in captions)
    assert any(caption.endswith("the truest.") for caption in captions)


def test_a_swapped_quote_regenerates_the_whole_debate(monkeypatch):
    pipe = "A pipe is only a thought " * 6
    pipe = pipe.strip() + "."
    short = {
        "id": "q-short",
        "text": "Imagination matters more than knowledge.",
        "source": "Essays",
        "sourceKind": "primary",
        "url": "https://en.wikiquote.org/wiki/Albert_Einstein",
    }
    long = {
        "id": "q-pipe",
        "text": pipe,
        "source": "Letters",
        "sourceKind": "primary",
        "url": "https://en.wikiquote.org/wiki/Friedrich_Nietzsche",
    }
    calls = {"replan": 0}

    def fake(prompt):
        if "Write every beat again" in prompt:
            calls["replan"] += 1
            assert "Discarded quotes:" in prompt and "pipe" in prompt.lower()
            return [
                {"i": 0, "text": "Is imagination only a decoration?"},
                {"i": 1, "quoteId": "q-short"},
                {"i": 2, "text": "Imagination is not a proof by itself."},
                {"i": 3, "text": "Then imagination still needs a test."},
                {"i": 5, "text": "A test can fail and still teach."},
                {"i": 7, "text": "Keep the test."},
            ]
        if "Rewrite only line" in prompt:
            # A longer line that keeps the discarded idea must not replace the draft.
            return [{"i": 2, "text": "A pipe is not a proof and the pipe idea keeps growing longer every time."}]
        return [
            {"i": 0, "text": "Is imagination only a decoration?"},
            {"i": 1, "quoteId": "q-pipe"},
            {"i": 2, "text": "A pipe is not a proof."},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write(
        "debate", "imagination", ["Albert Einstein", "Friedrich Nietzsche"], "en-US",
        sheets=[{"quotes": [short]}, {"quotes": [long, short]}], use_llm=True,
    )
    assert calls["replan"] == 1
    assert all("pipe" not in line["text"].lower() for line in out["lines"]), [line["text"] for line in out["lines"]]
    assert any("Imagination is not a proof" in line["text"] for line in out["lines"])


def _shared_id_quote(qid: str, text: str, page: str) -> dict:
    return {
        "id": qid,
        "text": text,
        "source": "Essays",
        "sourceKind": "primary",
        "url": f"https://en.wikiquote.org/wiki/{page}",
    }


def test_shared_quote_ids_resolve_on_the_speakers_own_sheet(monkeypatch):
    """q1 on Einstein is not q1 on Nietzsche. The other speaker's id does not rewrite the debate."""
    einstein = {"quotes": [
        _shared_id_quote("q1", "That price can drop.", "Albert_Einstein"),
        _shared_id_quote("q10", "Keep the price.", "Albert_Einstein"),
    ]}
    nietzsche = {"quotes": [
        _shared_id_quote("q1", "Simplicity has a price.", "Friedrich_Nietzsche"),
        _shared_id_quote("q10", "Power hides in a mask.", "Friedrich_Nietzsche"),
    ]}
    names = ["Albert Einstein", "Friedrich Nietzsche"]
    sheets = [einstein, nietzsche]
    hook = "Nietzsche, is simplicity worth the cost?"
    reply = "A drop still needs care."
    calls: list[str] = []

    def run(drafts):
        calls.clear()

        def fake(prompt):
            if "Rewrite only line" in prompt or "Write every beat again" in prompt:
                calls.append(prompt)
                return []
            return drafts

        monkeypatch.setattr(script, "_gemini", fake)
        return script.write("debate", "simplicity", names, "en-US", sheets=sheets, use_llm=True, duration=30)

    owned = run([
        {"i": 0, "text": hook},
        {"i": 1, "quoteId": "q1"},
        {"i": 2, "quoteId": "albert_einstein:q1"},
        {"i": 3, "text": reply},
        {"i": 4, "quoteId": "q10"},
    ])
    assert owned["scriptQA"]["pass"], owned["scriptQA"]
    assert owned["lines"][1]["text"] == "Simplicity has a price."
    assert owned["lines"][1]["speaker"] == 1
    assert owned["lines"][2]["text"] == "That price can drop."
    assert owned["lines"][2]["speaker"] == 0 and owned["lines"][2]["quote"]["id"] == "q1"
    assert owned["lines"][4]["text"] == "Keep the price."
    assert owned["lines"][4]["quote"]["id"] == "q10"
    assert calls == []
    prompt = script._prompt(script.plan_for("debate", 2, 30), sheets, names, "simplicity", "en-US", "debate", duration=30)
    assert "albert_einstein:q1" in prompt and "friedrich_nietzsche:q1" in prompt

    stolen = run([
        {"i": 0, "text": hook},
        {"i": 1, "quoteId": "friedrich_nietzsche:q1"},
        {"i": 2, "quoteId": "friedrich_nietzsche:q1", "text": "That price can drop."},
        {"i": 3, "text": reply},
        {"i": 4, "quoteId": "friedrich_nietzsche:q10", "text": "Keep the price."},
    ])
    problems = stolen["scriptQA"]["problems"]
    assert stolen["scriptQA"]["pass"] is False
    assert problems.count("speaker 0: no real quote of his own sheet") == 1
    assert not any(line["speaker"] == 0 and line["text"] == "Simplicity has a price." for line in stolen["lines"])
    assert not any(line["speaker"] == 0 and line.get("kind") == "quote" for line in stolen["lines"])
    assert calls == []


def _quote(qid: str, text: str, page: str) -> dict:
    return {
        "id": qid,
        "text": text,
        "source": "Essays",
        "sourceKind": "primary",
        "url": f"https://en.wikiquote.org/wiki/{page}",
    }


def test_a_line_must_not_address_its_own_speaker():
    names = ["Albert Einstein", "Friedrich Nietzsche"]
    assert script.mentions_speaker("Einstein, is truth objective?", names[0])
    assert not script.mentions_speaker("Nietzsche, is truth objective?", names[0])
    assert script.mentions_speaker("Nietzsche, is truth objective?", names[1])
    s, n = socrates(), nietzsche()
    cast = ["Sócrates", "Nietzsche"]
    good = script.write("debate", "A verdade existe?", cast, sheets=[s, n], use_llm=False)["lines"]
    assert script.check_debate(good, [s, n], cast, "A verdade existe?") == []
    bad = [dict(line) for line in good]
    bad[0] = {**bad[0], "text": "Sócrates, a verdade existe mesmo?"}
    problems = script.check_debate(bad, [s, n], cast, "A verdade existe?")
    assert "line 0: addresses its own speaker" in problems
    assert "line 0: opener does not address the other speaker" in problems
    prompt = script._prompt(script.plan_for("debate", 2), [s, n], cast, "a verdade", "pt-BR", "debate")
    assert "own speaker" in prompt and "other speaker by name" in prompt


def test_nietzsche_naming_friedrich_addresses_himself():
    names = ["Albert Einstein", "Friedrich Nietzsche"]
    said = "Wisdom and power rarely mix, Friedrich."
    assert script.mentions_speaker(said, names[1], first=True)
    assert not script.mentions_speaker(said, names[1])
    assert not script.mentions_speaker("Friedrich, is wisdom power?", names[1])
    lines = [
        {"speaker": 0, "beat": "gancho", "kind": "paraphrase", "text": "Nietzsche, is wisdom power?"},
        {"speaker": 1, "beat": "tese", "kind": "paraphrase", "text": said},
        {"speaker": 0, "beat": "réplica", "kind": "paraphrase", "text": "Wisdom still needs a test."},
        {"speaker": 1, "beat": "virada", "act": "virada", "kind": "paraphrase", "text": "A test can fail."},
        {"speaker": 0, "beat": "fechamento", "kind": "paraphrase", "text": "Keep the test."},
    ]
    problems = script.check_debate(lines, [{}, {}], names, "wisdom")
    assert "line 1: addresses its own speaker" in problems
    assert "line 0: opener does not address the other speaker" not in problems
    lines[0] = {**lines[0], "text": "Friedrich, is wisdom power?"}
    opener = script.check_debate(lines, [{}, {}], names, "wisdom")
    assert "line 0: opener does not address the other speaker" in opener


def test_an_unanswered_quote_is_swapped_not_the_neighbour(monkeypatch):
    einstein = {"quotes": [
        _quote("q1", "Imagination is a decoration.", "Albert_Einstein"),
        _quote("q2", "Keep that break.", "Albert_Einstein"),
        _quote("q3", "A witness can break.", "Albert_Einstein"),
    ]}
    nietzsche = {"quotes": [
        _quote("q1", "Truth needs a witness.", "Friedrich_Nietzsche"),
    ]}
    names = ["Albert Einstein", "Friedrich Nietzsche"]
    calls: list[str] = []

    def fake(prompt):
        calls.append(prompt)
        if "Judge this debate" in prompt:
            return [
                {"i": 1, "answers_previous": True, "reason": "follows", "coherent": False},
                {"i": 2, "answers_previous": True, "reason": "follows", "coherent": False},
                {"i": 3, "answers_previous": True, "reason": "follows", "coherent": False},
                {"i": 4, "answers_previous": False, "reason": "the close ignores the break", "coherent": False},
            ]
        if "Score each candidate quote" in prompt:
            assert "Do not rewrite the previous line" in prompt
            assert "line 3" not in prompt.split("Rewrite only line", 1)[-1] if "Rewrite only line" in prompt else True
            return [
                {"i": 0, "quoteId": "albert_einstein:q1", "score": 1},
                {"i": 0, "quoteId": "albert_einstein:q2", "score": 9},
            ]
        if "Rewrite only line" in prompt:
            raise AssertionError(prompt)
        return [
            {"i": 0, "text": "Nietzsche, is truth a witness?"},
            {"i": 1, "quoteId": "q1"},
            {"i": 2, "quoteId": "q3"},
            {"i": 3, "text": "The break stays small."},
            {"i": 4, "quoteId": "q1"},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write(
        "debate", "truth", names, "en-US", sheets=[einstein, nietzsche], use_llm=True, duration=30,
    )
    assert any("Score each candidate quote" in prompt for prompt in calls)
    assert not any("Rewrite only line 3" in prompt for prompt in calls)
    assert out["lines"][3]["text"] == "The break stays small."
    assert out["lines"][4]["text"] == "Keep that break."
    assert out["lines"][4]["kind"] == "quote" and out["lines"][4]["quote"]["id"] == "q2"
    assert out["scriptQA"]["pass"], out["scriptQA"]


def test_an_unanswered_quote_replans_once_when_no_other_quote_answers(monkeypatch):
    einstein = {"quotes": [_quote("q1", "Imagination is a decoration.", "Albert_Einstein")]}
    nietzsche = {"quotes": [_quote("q1", " ".join(["Truth"] * 32) + ".", "Friedrich_Nietzsche")]}
    names = ["Albert Einstein", "Friedrich Nietzsche"]
    calls: list[str] = []

    def fake(prompt):
        calls.append(prompt)
        if "Rewrite only line" in prompt:
            raise AssertionError(prompt)
        if "Judge this debate" in prompt:
            if "Truth needs a witness" in prompt:
                return [{
                    "i": 2, "answers_previous": False, "reason": "imagination ignores the witness", "coherent": False,
                }]
            return [{"i": 1, "answers_previous": True, "reason": "follows", "coherent": True}]
        if "Write every beat again" in prompt:
            return [
                {"i": 0, "text": "Nietzsche, is truth a witness?"},
                {"i": 1, "text": "A witness needs one look."},
                {"i": 2, "quoteId": "q1"},
                {"i": 3, "text": "Decoration still needs a witness."},
                {"i": 4, "text": "Keep that witness."},
            ]
        return [
            {"i": 0, "text": "Nietzsche, is truth a witness?"},
            {"i": 1, "text": "Truth needs a witness."},
            {"i": 2, "quoteId": "q1"},
            {"i": 3, "text": "Imagination stays small."},
            {"i": 4, "text": "Keep the small."},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write(
        "debate", "truth", names, "en-US", sheets=[einstein, nietzsche], use_llm=True, duration=30,
    )
    assert sum("Write every beat again" in prompt for prompt in calls) == 1
    assert not any("Rewrite only line 3" in prompt for prompt in calls)
    assert out["lines"][2]["text"] == "Imagination is a decoration."
    assert out["lines"][2]["kind"] == "quote"
    assert out["lines"][1]["text"] == "A witness needs one look."
    assert out["scriptQA"]["pass"], out["scriptQA"]


def test_long_lines_are_shortened_before_qa_or_the_failure_names_the_cap(monkeypatch):
    """A writer that comes back long, then longer, must not keep the longer line."""
    long_lines = [
        "Today I would like to discuss several unrelated matters before we begin this session please",
        "Horses wander slowly across empty fields while silent clouds gather above distant hills after sunset finally ends outdoors",
        "Bakers measure daily flour prior sunrise because ovens cool down without constant careful attention inside large metal trays",
        "Children paint tiny bright murals beside rivers until colors fade beneath heavy rainstorms each spring using brushes freely",
        "Markets close early whenever thunder warns merchants about broken stalls near quiet harbor gates later anyway folks",
    ]
    assert script.words(long_lines[0]) > 14 and "?" not in long_lines[0]
    assert "knowledge" not in long_lines[0].lower()
    assert script.words(long_lines[1]) > 16 and script.words(long_lines[4]) > 12
    assert min(script.words(text) for text in long_lines) > 9
    seen = {"rules": False, "rewrite": 0}

    def content_words(text: str) -> list[str]:
        return [part.strip(".,?!") for part in text.split() if len(part.strip(".,?!")) > 4]

    def short_line(index: int, cap: int, previous: str, problem: str) -> str:
        if "different concrete word" in problem:
            banned = ""
            if "word '" in problem:
                banned = problem.split("word '", 1)[1].split("'", 1)[0].lower()
            choices = [word for word in content_words(previous) if word.lower() != banned] or ["knowledge"]
            text = f"Keep {choices[-1]}."
        elif index == 0 or "hook" in problem:
            text = "Ben, is knowledge only a rumor?"
        elif "close" in problem:
            word = (content_words(previous) or ["knowledge"])[-1]
            text = f"Keep {word}."
        else:
            word = (content_words(previous) or ["knowledge"])[-1]
            text = (
                f"{word} still needs proof.",
                f"{word} can fail one test.",
                f"Then {word} stays small.",
                f"Keep {word} honest.",
            )[index % 4]
        parts = text.split()
        if len(parts) > cap:
            text = "Is knowledge real?" if index == 0 and cap >= 3 else " ".join(parts[:cap]).rstrip(",;:") + "."
        return text

    def fake(prompt):
        if "Rewrite only line" in prompt:
            seen["rewrite"] += 1
            assert "Failing rule:" in prompt
            assert "Maximum words:" in prompt
            assert "Previous line:" in prompt
            assert "Next line:" in prompt
            index = int(prompt.split("Rewrite only line ", 1)[1].split(" ", 1)[0])
            cap = int(prompt.split("Maximum words: ", 1)[1].split(".", 1)[0])
            previous = prompt.split("Previous line: ", 1)[1].split("\n", 1)[0]
            problem = prompt.split("Failing rule: ", 1)[1].split("\n", 1)[0]
            current = prompt.split("Current line (", 1)[1].split(":", 1)[1].strip()
            short = short_line(index, cap, previous, problem)
            longer = current + " UNACCEPTABLY LONGER than the line it rewrites"
            return [
                {"i": index, "text": longer, "words": script.words(longer)},
                {"i": index, "text": short, "words": script.words(short)},
            ]
        seen["rules"] = True
        assert "at most 14 words" in prompt and "at most 16 words" in prompt
        assert "at most 9 words" in prompt and "at most 12 words" in prompt
        assert "Spoken word budget" in prompt and "words/second" in prompt
        return [{"i": index, "text": text, "words": script.words(text)} for index, text in enumerate(long_lines)]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write(
        "debate", "knowledge", ["Ada", "Ben"], "en-US",
        sheets=[{"quotes": []}, {"quotes": []}], use_llm=True, duration=30, words_per_s=2.6,
    )
    texts = [line["text"] for line in out["lines"]]
    assert seen["rules"] and seen["rewrite"]
    assert all("UNACCEPTABLY LONGER" not in text for text in texts), texts
    assert all(script.words(text) <= script.words(long_lines[index]) for index, text in enumerate(texts)), texts
    if not out["scriptQA"]["pass"]:
        reason = "; ".join(out["scriptQA"]["problems"])
        assert any(token in reason for token in ("maximum", "words", "too long", "hook", "close")), reason
        with pytest.raises(script.ScriptQAError, match="script QA failed"):
            script.require_passing_script(out, "debate")
    else:
        assert script.words(texts[0]) <= 14 and "?" in texts[0] and "knowledge" in texts[0].lower()
        assert min(script.words(text) for text in texts) <= 9
        assert script.words(texts[-1]) <= 12


def test_debate_qa_fails_loudly_when_rewrites_do_not_land(monkeypatch):
    def fake(prompt):
        if "Rewrite only line" in prompt or "does not answer" in prompt:
            return [{"i": 1, "text": "Cats sleep through the long afternoon."}]
        return [
            {"i": 0, "text": "Is knowledge only opinion?"},
            {"i": 1, "text": "Cats sleep through the long afternoon."},
            {"i": 2, "text": "Birds leave without a map at all."},
            {"i": 3, "text": "The room can stay quiet now."},
            {"i": 4, "text": "Nothing follows from that claim."},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write(
        "debate", "knowledge", ["Ada", "Ben"], "en-US",
        sheets=[{"quotes": []}, {"quotes": []}], use_llm=True, duration=30,
    )
    assert out["scriptQA"]["pass"] is False
    with pytest.raises(script.ScriptQAError, match="script QA failed"):
        script.require_passing_script(out, "debate")


def test_the_coherent_pipe_draft_is_kept(monkeypatch):
    """The real f191b4c draft: Einstein's pipe quote, Nietzsche's reply, Einstein's long close."""
    pipe = "Pipe smoking contributes to a calm and objective judgment in human affairs."
    simple = "Make the theory as simple as possible and then leave the remaining work to the measured facts."
    reply = "A pipe makes your judgment objective? Cute."
    nietzsche_q = "Convictions are more dangerous enemies of truth than lies."
    einstein = {"quotes": [
        _quote("q10", pipe, "Albert_Einstein"),
        _quote("q1", simple, "Albert_Einstein"),
    ]}
    nietzsche = {"quotes": [_quote("q4", nietzsche_q, "Friedrich_Nietzsche")]}
    names = ["Albert Einstein", "Friedrich Nietzsche"]
    calls: list[str] = []

    def fake(prompt):
        calls.append(prompt)
        if "Judge this debate" in prompt:
            return [
                {"i": index, "answers_previous": True, "reason": "the reply follows the argument", "coherent": True}
                for index in range(1, 5)
            ]
        if "Score each candidate quote" in prompt or "Rewrite only line" in prompt or "Write every beat again" in prompt:
            raise AssertionError(prompt)
        return [
            {"i": 0, "text": "Nietzsche, is a pipe the way to an objective judgment?"},
            {"i": 1, "quoteId": "q4"},
            {"i": 2, "quoteId": "q10"},
            {"i": 3, "text": reply},
            {"i": 4, "quoteId": "q1"},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write("debate", "judgment", names, "en-US", sheets=[einstein, nietzsche], use_llm=True, duration=30)
    texts = [line["text"] for line in out["lines"]]
    assert texts[1] == nietzsche_q
    assert texts[2] == pipe and out["lines"][2]["quote"]["id"] == "q10"
    assert texts[3] == reply
    assert texts[4] == simple and out["lines"][4]["kind"] == "quote"
    assert script.words(simple) > 14
    assert out["scriptQA"]["pass"], out["scriptQA"]
    assert not any("does not answer" in problem for problem in out["scriptQA"]["problems"])
    assert not any(problem.startswith("debate: the close is not memorable") for problem in out["scriptQA"]["problems"])
    assert sum("Judge this debate" in prompt for prompt in calls) == 1


def test_pipe_reply_before_the_quote_fails_the_final_script(monkeypatch):
    """The 6cb0930 script: Nietzsche names the pipe, then Einstein says the pipe quote, and QA passed."""
    pipe = "Pipe smoking contributes to a calm and objective judgment in human affairs."
    reply = "Pipe smoke proves judgment, Albert?"
    earlier = [
        {"kind": "paraphrase", "speaker": 0, "text": "Nietzsche, is judgment only a habit?"},
        {"kind": "quote", "speaker": 1, "text": "Convictions are more dangerous enemies of truth than lies."},
        {"kind": "paraphrase", "speaker": 0, "text": "Convictions still need a witness."},
        {"kind": "paraphrase", "speaker": 1, "text": reply},
        {"kind": "quote", "speaker": 0, "text": pipe},
    ]
    flagged = script._unsaid_quote_refs(earlier, "judgment")
    assert any("line 3: refers to a quote spoken later on line 4" in item for item in flagged)
    einstein = {"quotes": [_quote("q10", pipe, "Albert_Einstein")]}
    nietzsche = {"quotes": [_quote("q4", earlier[1]["text"], "Friedrich_Nietzsche")]}
    names = ["Albert Einstein", "Friedrich Nietzsche"]

    def fake(prompt):
        if "Judge this debate" in prompt:
            # The real judge accepted this order. The final check must not.
            return [{"i": 1, "answers_previous": True, "reason": "follows", "coherent": True}]
        if "Prior lines:" in prompt and "This speaker's quotes:" in prompt:
            assert "Pipe smoking contributes" not in prompt
            assert "Do not reorder lines" in prompt or "Ignore the current line" in prompt
            return [{"i": 3, "text": "Judgment needs a witness."}]
        if "Write every beat again" in prompt or "Score each candidate quote" in prompt:
            raise AssertionError(prompt)
        return [
            {"i": 0, "text": earlier[0]["text"]},
            {"i": 1, "quoteId": "q4"},
            {"i": 2, "text": earlier[2]["text"]},
            {"i": 3, "text": reply},
            {"i": 4, "quoteId": "q10"},
            {"i": 5, "text": "A witness can still fail."},
            {"i": 6, "text": "Failure stays small here."},
            {"i": 7, "text": "Keep one small witness."},
            {"i": 8, "text": "The witness stays."},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write("debate", "judgment", names, "en-US", sheets=[einstein, nietzsche], use_llm=True)
    texts = [line["text"] for line in out["lines"]]
    pipe_at = texts.index(pipe)
    assert all("pipe" not in text.lower() for text in texts[:pipe_at])
    assert texts[pipe_at] == pipe and out["lines"][pipe_at]["speaker"] == 0
    assert not any("refers to a quote spoken later" in problem for problem in out["scriptQA"]["problems"]), out["scriptQA"]


def test_a_reply_cannot_name_a_quote_that_is_spoken_later(monkeypatch):
    pipe = "Pipe smoking contributes to a calm and objective judgment in human affairs."
    einstein = {"quotes": [_quote("q10", pipe, "Albert_Einstein")]}
    nietzsche = {"quotes": [_quote("q4", "Convictions are more dangerous enemies of truth than lies.", "Friedrich_Nietzsche")]}
    names = ["Albert Einstein", "Friedrich Nietzsche"]
    order: list[str] = []

    def fake(prompt):
        if "Judge this debate" in prompt:
            assert "already said" in prompt
            if "Pipe smoke proves judgment" in prompt and prompt.index("Pipe smoke proves judgment") < prompt.index(pipe):
                return [{
                    "i": 3, "answers_previous": False,
                    "reason": "pipe is spoken only on a later line", "coherent": False,
                }]
            return [{"i": 1, "answers_previous": True, "reason": "follows", "coherent": True}]
        if "Prior lines:" in prompt or "does not answer the previous line" in prompt or "spoken only later" in prompt:
            order.append("rewrite-early")
            assert "line 3" in prompt
            if "Prior lines:" in prompt:
                assert "Pipe smoking contributes" not in prompt
            return [{"i": 3, "text": "Judgment needs a witness."}]
        if "Write every beat again" in prompt or "Score each candidate quote" in prompt:
            raise AssertionError("reordered the quote ahead of the reply")
        return [
            {"i": 0, "text": "Nietzsche, is judgment only a habit?"},
            {"i": 1, "quoteId": "q4"},
            {"i": 2, "text": "Convictions still need a witness."},
            {"i": 3, "text": "Pipe smoke proves judgment?"},
            {"i": 4, "quoteId": "q10"},
            {"i": 5, "text": "A witness can still fail."},
            {"i": 6, "text": "Failure stays small here."},
            {"i": 7, "text": "Keep one small witness."},
            {"i": 8, "text": "The witness stays."},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write("debate", "judgment", names, "en-US", sheets=[einstein, nietzsche], use_llm=True)
    texts = [line["text"] for line in out["lines"]]
    assert texts[4] == pipe
    assert "pipe" not in texts[3].lower()
    assert texts.index(pipe) > 3
    assert "rewrite-early" in order
    assert not any("refers to a quote spoken later" in problem for problem in out["scriptQA"]["problems"]), out["scriptQA"]


def test_swap_is_refused_when_the_same_speaker_would_talk_twice():
    pipe = "Pipe smoking contributes to a calm and objective judgment in human affairs."
    blocked = [
        {"speaker": 0, "text": "Nietzsche, is judgment only a habit?"},
        {"speaker": 1, "text": "Pipe smoke proves judgment, Albert?"},
        {"speaker": 0, "text": pipe},
    ]
    assert script._swap_reply_before_quote(blocked, 1, 2) is False
    assert blocked[1]["text"].startswith("Pipe smoke")
    pair = [
        {"speaker": 0, "text": "Held."},
        {"speaker": 0, "text": "Pipe smoke proves judgment?"},
        {"speaker": 1, "text": pipe},
    ]
    assert script._swap_reply_before_quote(pair, 1, 2) is True
    assert pair[1]["text"] == pipe
    assert [line["speaker"] for line in pair] == [0, 1, 0]


def test_failed_script_cache_is_dropped_and_a_hit_costs_nothing(tmp_path, monkeypatch):
    from products.animind_core import llm_providers, telemetry

    monkeypatch.setenv("DEEPSEEK_API_KEY", "configured")
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    calls: list[str] = []

    def complete(self, prompt):
        calls.append(prompt)
        return llm_providers.LLMResult([{"i": 0, "text": "Fresh."}], "deepseek", self.model, 10, 5, 0.0001)

    monkeypatch.setattr(llm_providers.DeepSeekProvider, "complete", complete)
    provider = llm_providers.DeepSeekProvider()
    cache = tmp_path / "cache"
    path = llm_providers._cache_path(cache, provider, "the draft", script.SCRIPT_QA_VERSION)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "lines": [{"i": 0, "text": "Old."}],
        "provider": "deepseek",
        "model": provider.model,
        "input_tokens": 1800,
        "output_tokens": 400,
        "cost_usd": 0.0017,
        "cached": False,
    }), encoding="utf-8")
    hit, _failures = llm_providers.generate(
        "the draft", cache, lambda _prompt: [], version=script.SCRIPT_QA_VERSION,
    )
    assert hit is not None and hit.cached is True
    assert hit.cost_usd == 0.0 and hit.input_tokens == 0 and hit.output_tokens == 0
    assert hit.lines[0]["text"] == "Old."
    assert calls == []
    row = telemetry.record(
        "script", provider="deepseek", model=provider.model,
        input_tokens=1800, output_tokens=400, cost_usd=0.0017,
        metadata={"cached": True},
    )
    assert row["costUsd"] == 0.0 and row["inputTokens"] == 0 and row["outputTokens"] == 0
    stored = json.loads(path.read_text())
    stored["qaPass"] = False
    path.write_text(json.dumps(stored), encoding="utf-8")
    again, _failures = llm_providers.generate(
        "the draft", cache, lambda _prompt: [], version=script.SCRIPT_QA_VERSION,
    )
    assert calls == ["the draft"]
    assert again is not None and again.cached is False and again.lines[0]["text"] == "Fresh."
    assert llm_providers._cache_path(cache, provider, "the draft", "5") != path


def test_cached_pipe_reply_before_the_quote_ends_as_a_passing_script(tmp_path, monkeypatch):
    """The b1d33e4 cache: line 3 names the pipe, line 4 says it, and the hit rebilled the original cost."""
    from products.animind_core import llm_providers, telemetry

    pipe = "Pipe smoking contributes to a calm and objective judgment in human affairs."
    reply = "Pipe smoke proves judgment, Albert?"
    draft = [
        {"i": 0, "text": "Nietzsche, is judgment only a habit?"},
        {"i": 1, "quoteId": "q4"},
        {"i": 2, "text": "Convictions still need a witness."},
        {"i": 3, "text": reply},
        {"i": 4, "quoteId": "q10"},
        {"i": 5, "text": "A witness can still fail."},
        {"i": 6, "text": "Failure stays small here."},
        {"i": 7, "text": "Keep one small witness."},
        {"i": 8, "text": "The witness stays."},
    ]
    einstein = {"quotes": [_quote("q10", pipe, "Albert_Einstein")]}
    nietzsche = {"quotes": [_quote("q4", "Convictions are more dangerous enemies of truth than lies.", "Friedrich_Nietzsche")]}
    names = ["Albert Einstein", "Friedrich Nietzsche"]
    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "configured")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(script.knowledge, "ensure", lambda *_a, **_k: ({"mode": "quick", "chunks": []}, None))
    monkeypatch.setattr(script.knowledge, "retrieve", lambda *_a, **_k: [])
    prompt = script._prompt(
        script.plan_for("debate", 2), [einstein, nietzsche], names, "judgment", "en-US", "debate",
    )
    provider = llm_providers.DeepSeekProvider()
    cache = tmp_path / "cache" / "script"
    path = llm_providers._cache_path(cache, provider, prompt, script.SCRIPT_QA_VERSION)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "lines": draft,
        "provider": "deepseek",
        "model": provider.model,
        "input_tokens": 1800,
        "output_tokens": 400,
        "cost_usd": 0.0017,
        "cached": False,
    }), encoding="utf-8")
    calls: list[str] = []

    def complete(self, prompt_text):
        calls.append(prompt_text)
        if "Judge this debate" in prompt_text:
            return llm_providers.LLMResult(
                [{"i": 1, "answers_previous": True, "reason": "follows", "coherent": True}],
                "deepseek", self.model, 900, 80, 0.0005,
            )
        if "Attempt 1 of 2" in prompt_text or "Attempt 2 of 2" in prompt_text:
            assert "Pipe smoking contributes" not in prompt_text
            assert "Prior lines:" in prompt_text and "This speaker's quotes:" in prompt_text
            return llm_providers.LLMResult(
                [{"i": 3, "text": reply}], "deepseek", self.model, 200, 40, 0.0002,
            )
        if prompt_text.startswith("Write beat 3 again"):
            assert "Pipe smoking contributes" not in prompt_text
            assert "Prior lines:" in prompt_text
            return llm_providers.LLMResult(
                [{"i": 3, "text": "Judgment needs a witness."}],
                "deepseek", self.model, 200, 40, 0.0002,
            )
        raise AssertionError(prompt_text[:180])

    monkeypatch.setattr(llm_providers.DeepSeekProvider, "complete", complete)
    out = script.write("debate", "judgment", names, "en-US", sheets=[einstein, nietzsche], use_llm=True)
    texts = [line["text"] for line in out["lines"]]
    assert out["scriptQA"]["pass"], out["scriptQA"]
    pipe_at = texts.index(pipe)
    assert pipe_at == 4 and out["lines"][pipe_at]["speaker"] == 0
    assert "pipe" not in texts[3].lower()
    assert texts[3] == "Judgment needs a witness."
    assert sum("Attempt 1 of 2" in item for item in calls) == 1
    assert sum("Attempt 2 of 2" in item for item in calls) == 1
    assert any(item.startswith("Write beat 3 again") for item in calls)
    assert not any(item.startswith("You write") for item in calls)
    script_rows = [row for row in telemetry.read() if row["operation"] == "script"]
    assert script_rows[-1]["costUsd"] == 0.0
    assert script_rows[-1]["inputTokens"] == 0 and script_rows[-1]["outputTokens"] == 0
    assert script_rows[-1]["metadata"]["cached"] is True
    kept = json.loads(path.read_text())
    assert kept["qaPass"] is True and kept["qaVersion"] == script.SCRIPT_QA_VERSION
    assert kept["cost_usd"] == 0.0
    assert "Pipe smoke proves" not in json.dumps(kept)
    calls.clear()
    again = script.write("debate", "judgment", names, "en-US", sheets=[einstein, nietzsche], use_llm=True)
    assert again["scriptQA"]["pass"], again["scriptQA"]
    assert again["lines"][3]["text"] == "Judgment needs a witness."
    assert calls == []
    second = [row for row in telemetry.read() if row["operation"] == "script"][-1]
    assert second["costUsd"] == 0.0 and second["metadata"]["cached"] is True
    assert second["inputTokens"] == 0 and second["outputTokens"] == 0


def test_judge_tokens_are_logged(tmp_path, monkeypatch):
    from products.animind_core import llm_providers, telemetry

    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))

    def generate(prompt, cache, gemini, *, allow_unavailable_gemini=False, version=""):
        return llm_providers.LLMResult(
            [{"i": 1, "answers_previous": True, "reason": "follows", "coherent": True}],
            "deepseek", "deepseek-flash", 320, 40, 0.000144,
        ), []

    monkeypatch.setattr(llm_providers, "generate", generate)
    judged = script.judge_debate(
        [{"speaker": 0, "text": "Nietzsche, is judgment a habit?"}, {"speaker": 1, "text": "Judgment needs a test."}],
        ["Albert Einstein", "Friedrich Nietzsche"], "judgment", "en-US", allow_unavailable_gemini=True,
    )
    assert judged and judged["coherent"] is True
    rows = [row for row in telemetry.read() if row["operation"] == "script-judge"]
    assert rows and rows[-1]["inputTokens"] == 320 and rows[-1]["outputTokens"] == 40
    assert rows[-1]["costUsd"] == 0.000144


def test_debate_plan_fits_the_measured_word_budget():
    rate = 2.6
    plan = script.plan_for("debate", 2, 30, words_per_s=rate)
    assert len(plan) >= 5
    assert any(beat.act == "virada" or beat.name == "virada" for beat in plan)
    assert sum(beat.max_words for beat in plan) <= script.word_budget(30, rate)


def test_slow_voice_plans_fewer_words_before_tts():
    slow = script.plan_for("solo", 1, duration=30, words_per_s=1.2)
    historical = script.plan_for("solo", 1, duration=30)
    assert sum(beat.max_words for beat in slow) < sum(beat.max_words for beat in historical)
    assert slow[0].name == "gancho" and slow[-1].name == "fechamento"


def test_unanswered_debate_line_is_rewritten_with_the_previous_line(monkeypatch):
    seen: list[str] = []

    def fake(prompt):
        seen.append(prompt)
        if "Judge this debate" in prompt:
            return [
                {"i": 2, "answers_previous": False, "reason": "ignores the previous line", "coherent": False},
                {"i": 4, "answers_previous": False, "reason": "ignores the previous line", "coherent": False},
            ]
        if "does not answer the previous line" in prompt:
            assert "Previous line:" in prompt
            index = int(prompt.split("Rewrite only line ", 1)[1].split(" ", 1)[0])
            previous = prompt.split("Previous line:", 1)[1]
            word = next(part.strip(" .,?!") for part in previous.split() if len(part.strip(" .,?!")) > 4)
            return [{"i": index, "text": f"{word} still needs one concrete test."}]
        return [
            {"i": 0, "text": "Is opinion the whole of knowledge?"},
            {"i": 1, "text": "Cats sleep through the long afternoon."},
            {"i": 2, "text": "Birds leave without drawing a map."},
            {"i": 3, "text": "The audience can leave quietly now."},
            {"i": 4, "text": "Nothing here follows that last claim."},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write(
        "debate", "knowledge", ["Ada", "Ben"], "en-US",
        sheets=[{"quotes": []}, {"quotes": []}], use_llm=True, duration=30,
    )
    problems = out["scriptQA"]["problems"]
    assert not any("line 2: does not answer" in item for item in problems), problems
    assert not any("line 4: does not answer" in item for item in problems), problems
    assert any("Previous line:" in prompt for prompt in seen)
    assert "concrete test" in out["lines"][2]["text"]


def test_english_lesson_passes_qa():
    out = script.write("lesson", "What is truth?", ["Nietzsche"], "en", sheets=[nietzsche()], use_llm=False)
    assert out["scriptQA"]["pass"], out["scriptQA"]
    assert out["lines"][-1]["beat"] == "síntese" and out["lines"][4]["beat"] == "virada"


def _caption(sc, line) -> str:
    text = " ".join(row for page in sc._pages(line) for row in page)
    if line.get("kind") == "quote" and text.startswith("“") and text.endswith("”"):
        text = text[1:-1]
    return text


def test_rendered_subtitles_match_the_writer_lines(monkeypatch):
    import numpy as np

    from products.animind_core import scene

    plan = script.plan_for("solo", 1, 30)
    bank = (
        "Curiosity opens the first question.",
        "Evidence has to survive a rival.",
        "Practice shows where a claim breaks.",
        "Doubt keeps the easy answer honest.",
        "Method matters more than the slogan.",
        "Results should change the next step.",
        "Choice arrives after the check.",
        "Limits belong in the final line.",
    )
    texts = list(bank[:len(plan)])

    def fake(prompt):
        assert "Rewrite only line" not in prompt
        return [{"i": i, "text": text} for i, text in enumerate(texts)]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write(
        "solo", "What is strength?", ["Ada"], "en-US",
        sheets=[{"quotes": []}], use_llm=True, duration=30,
    )
    assert [line["text"] for line in out["lines"]] == [script.tidy(text) for text in texts]
    filler = ("only an opinion", "Life reveals consequences", "Look at ")
    assert not any(phrase in line["text"] for line in out["lines"] for phrase in filler)
    timed = []
    cursor = 2000
    for line in out["lines"]:
        timed.append({**line, "startMs": cursor, "endMs": cursor + 2000})
        cursor += 2300
    sc = scene.Scene(
        template="solo", scenario="studio", rigs=[None], script=timed, envelope=np.ones(800),
        env_fps=100, duration_s=cursor / 1000 + 3, disclaimer="Parody", names=["Ada"], topic=out["topic"],
    )
    assert [_caption(sc, line) for line in sc.script] == [line["text"] for line in out["lines"]]


def test_german_source_is_spoken_in_the_target_language(monkeypatch):
    german = "Was mich nicht umbringt, macht mich stärker."
    sheet = {"quotes": [{
        "id": "q-de",
        "text": german,
        "source": "Götzen-Dämmerung, Sprüche und Pfeile",
        "sourceKind": "primary",
        "url": "https://en.wikiquote.org/wiki/Friedrich_Nietzsche",
    }]}

    def fake(prompt):
        if "Translate this quote" in prompt:
            return [{"i": 0, "text": "What does not kill me makes me stronger."}]
        return [
            {"i": 0, "text": "Strength is not the same thing as noise."},
            {"i": 1, "quoteId": "q-de"},
            {"i": 2, "text": "Strength shows up when the plan fails."},
            {"i": 3, "text": "Keep the test and drop the slogan."},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write("solo", "What is strength?", ["Nietzsche"], "en-US", sheets=[sheet], use_llm=True)
    spoken = out["lines"][1]
    assert "umbringt" not in spoken["text"].lower()
    assert "stronger" in spoken["text"].lower()
    assert spoken["kind"] == "paraphrase"
    assert spoken["basis"]["text"] == german
    assert spoken["basis"]["source"].startswith("Götzen-Dämmerung")
    assert spoken["basis"]["translated"] is True


def test_writer_varies_a_keyword_repeated_on_three_lines(monkeypatch):
    calls = []

    def fake(prompt):
        calls.append(prompt)
        if "Rewrite only line 2" in prompt:
            return [{"i": 2, "text": "Practice is the part that changes."}]
        return [
            {"i": 0, "text": "Knowledge opens the first door."},
            {"i": 1, "text": "Knowledge still needs a witness."},
            {"i": 2, "text": "Knowledge without a test is decoration."},
            {"i": 3, "text": "Leave the audience one check."},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    out = script.write("solo", "What is knowledge?", ["Ada"], "en-US", sheets=[{"quotes": []}], use_llm=True)
    assert "Practice is the part that changes." in out["lines"][2]["text"]
    assert any("Rewrite only line 2" in prompt for prompt in calls)
    assert script._keyword_streak(out["lines"][:3]) is None or "knowledge" not in out["lines"][2]["text"].lower()


def _pipe_sheets():
    pipe = "Pipe smoking contributes to a calm and objective judgment in human affairs."
    einstein = {"found": True, "name": "Albert Einstein", "quotes": [
        _quote("q10", pipe, "Albert_Einstein"),
    ]}
    nietzsche = {"found": True, "name": "Friedrich Nietzsche", "quotes": [
        _quote("q4", "Convictions are more dangerous enemies of truth than lies.", "Friedrich_Nietzsche"),
    ]}
    return pipe, einstein, nietzsche


def _bad_order_draft(pipe_reply: str) -> list[dict]:
    return [
        {"i": 0, "text": "Nietzsche, is judgment only a habit?"},
        {"i": 1, "quoteId": "q4"},
        {"i": 2, "text": "Convictions still need a witness."},
        {"i": 3, "text": pipe_reply},
        {"i": 4, "quoteId": "q10"},
        {"i": 5, "text": "A witness can still fail."},
        {"i": 6, "text": "Failure stays small here."},
        {"i": 7, "text": "Keep one small witness."},
        {"i": 8, "text": "The witness stays."},
    ]


def _good_order_draft() -> list[dict]:
    draft = _bad_order_draft("Judgment needs a witness.")
    draft[3] = {"i": 3, "text": "Judgment needs a witness."}
    return draft


def _drive_render(monkeypatch, tmp_path, *, fix_on_redraft: bool):
    """POST /v1/render's worker: a fresh DeepSeek draft, then the repair calls."""
    import wave

    import numpy as np

    from animation.parametric import renderer as parametric_renderer
    from products.animind_core import llm_providers, render, telemetry

    monkeypatch.setenv("ANIMIND_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "configured")
    monkeypatch.delenv("ANIMIND_FORCE_MOCK", raising=False)
    pipe, einstein, nietzsche = _pipe_sheets()
    reply = "Pipe smoke proves judgment, Albert?"

    def load(name, lang="en-US", fetch=True):  # noqa: ARG001
        return einstein if "einstein" in name.lower() else nietzsche

    monkeypatch.setattr(script.personas, "load", load)
    monkeypatch.setattr(script.knowledge, "ensure", lambda *_a, **_k: ({"mode": "quick", "chunks": []}, None))
    monkeypatch.setattr(script.knowledge, "retrieve", lambda *_a, **_k: [])
    monkeypatch.setattr(script, "measured_rate", lambda *_a, **_k: 20.0)
    calls: list[str] = []
    redrafts = {"n": 0}

    def complete(self, prompt):
        calls.append(prompt)
        if "Judge this debate" in prompt:
            return llm_providers.LLMResult(
                [{"i": 1, "answers_previous": True, "reason": "follows", "coherent": True}],
                "deepseek", self.model, 80, 20, 0.00004,
            )
        if "The draft failed QA" in prompt:
            redrafts["n"] += 1
            lines = _good_order_draft() if fix_on_redraft and redrafts["n"] == 1 else _bad_order_draft(reply)
            return llm_providers.LLMResult(lines, "deepseek", self.model, 400, 80, 0.0002)
        if "Attempt " in prompt or prompt.startswith("Write beat"):
            return llm_providers.LLMResult(
                [{"i": 0, "text": reply}], "deepseek", self.model, 40, 12, 0.00002,
            )
        if prompt.startswith("You write"):
            return llm_providers.LLMResult(
                _bad_order_draft(reply), "deepseek", self.model, 3292, 236, 0.0012,
            )
        return llm_providers.LLMResult(
            [{"i": 0, "text": "Judgment needs a witness."}], "deepseek", self.model, 20, 8, 0.00001,
        )

    monkeypatch.setattr(llm_providers.DeepSeekProvider, "complete", complete)

    def voice(text, pick, mp3, clip, max_s, rate_adj=0):  # noqa: ARG001
        pcm = np.zeros(render.SAMPLE_RATE // 2, np.int16)
        clip.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(clip), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(render.SAMPLE_RATE)
            handle.writeframes(pcm.tobytes())
        return pcm, "+0%"

    monkeypatch.setattr(render, "_voice_line", voice)
    monkeypatch.setattr(render, "fit_measured_duration", lambda lines, speech, target, respeak, **_k: (lines, speech))
    monkeypatch.setattr(render, "line_visemes", lambda *_a, **_k: ([{"t": 0.0, "v": "X"}], "envelope"))
    monkeypatch.setattr(render.frameqa, "check", lambda *_a, **_k: {"pass": True, "badFrames": 0, "warnings": []})

    def encode(self, frame_iter=None, audio_path=None, output_path=None, **_kwargs):  # noqa: ARG001
        from pathlib import Path
        Path(output_path).write_bytes(b"mp4")

    monkeypatch.setattr(parametric_renderer.AnimationRenderer, "render", encode)
    job_id = "order-repair"
    render._JOBS[job_id] = {"id": job_id, "status": "queued", "progress": 0.0}
    render._run(job_id, {
        "characters": [{"name": "Albert Einstein"}, {"name": "Friedrich Nietzsche"}],
        "template": "debate",
        "scenario": "studio",
        "topic": "judgment",
        "language": "en-US",
        "duration": 45,
    }, "http://test")
    return job_id, calls, pipe, telemetry.read()


def test_api_render_repairs_a_fresh_draft_that_names_a_later_quote(tmp_path, monkeypatch):
    from products.animind_core import render, settings

    job_id, calls, pipe, rows = _drive_render(monkeypatch, tmp_path, fix_on_redraft=True)
    job = render._JOBS[job_id]
    assert job["status"] == "done", job.get("error")
    saved = json.loads((settings.data_dir() / "jobs" / job_id / "script.json").read_text(encoding="utf-8"))
    texts = [line["text"] for line in saved["lines"]]
    assert saved["scriptQA"]["pass"], saved["scriptQA"]
    assert "pipe" not in texts[3].lower()
    assert texts[texts.index(pipe) - 1] != "Pipe smoke proves judgment, Albert?"
    assert not (settings.data_dir() / "jobs" / job_id / "script.failed.json").exists()
    repairs = [row for row in rows if row["operation"] == "script-repair"]
    kinds = [row["metadata"]["kind"] for row in repairs]
    assert kinds.count("rewrite") == 2
    assert "regenerate" in kinds
    assert "redraft" in kinds
    assert any(row["inputTokens"] > 0 and row["metadata"]["kind"] == "redraft" for row in repairs)
    assert any("The draft failed QA" in prompt and "refers to a quote spoken later" in prompt for prompt in calls)
    assert sum(1 for row in rows if row["operation"] == "script-judge") >= 1


def test_api_render_keeps_the_failed_script_when_redrafts_do_not_pass(tmp_path, monkeypatch):
    from products.animind_core import render, settings

    job_id, _calls, _pipe, rows = _drive_render(monkeypatch, tmp_path, fix_on_redraft=False)
    job = render._JOBS[job_id]
    assert job["status"] == "failed"
    assert "refers to a quote spoken later" in job["error"]
    failed = json.loads((settings.data_dir() / "jobs" / job_id / "script.failed.json").read_text(encoding="utf-8"))
    assert any("line 3: refers to a quote spoken later on line 4" in item for item in failed["problems"])
    assert failed["lines"][3]["text"].startswith("Pipe smoke")
    redrafts = [row for row in rows if row["operation"] == "script-repair" and row["metadata"]["kind"] == "redraft"]
    assert [row["metadata"]["attempt"] for row in redrafts] == [1, 2]
