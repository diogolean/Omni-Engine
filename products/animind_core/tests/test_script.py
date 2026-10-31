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


LESSON_BEATS = ["gancho", "pergunta", "citação", "exemplo", "citação", "síntese"]


def test_lesson_progresses_question_quote_example_synthesis():
    beats = [line["beat"] for line in script.write("lesson", "IA", ["S"], sheets=[sheet()], use_llm=False)["lines"]]
    assert beats == LESSON_BEATS


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
            {"i": 3, "text": "Uma fala escrita, sem citação."},
            {"i": 4, "text": "Curta e boa.", "basisId": real["id"]},
        ]

    monkeypatch.setattr(script, "_gemini", fake)
    other = {"quotes": []}
    lines = script.write("debate", "IA", ["Sócrates", "B"], sheets=[s, other], use_llm=True)["lines"]
    assert script.check(lines, [s, other]) == []
    assert not any(c in lines[0]["text"] for c in script.QUOTE_MARKS)
    assert lines[1]["kind"] == "quote" and lines[1]["text"] in {q["text"] for q in s["quotes"]}
    assert lines[2]["speaker"] == 1 and lines[2]["kind"] == "paraphrase"
    assert lines[3]["kind"] == "paraphrase"
    assert lines[4]["kind"] == "paraphrase" and "basis" not in lines[4]  # basis must be the speaker's own quote


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
    monkeypatch.setattr(script, "_gemini", lambda prompt: [{"i": 1, "quoteId": sorted(secondary)[0]}, {"i": 5, "quoteId": sorted(secondary)[-1]}])
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
    # Nietzsche's quote beats take two other quotes; then the writer puts his real line in a written beat
    # (fechamento, speaker 1) with other casing/punctuation, and a 7-word excerpt of Sócrates in another
    monkeypatch.setattr(script, "_gemini", lambda prompt: [
        {"i": 2, "quoteId": ids["As convicçõe"]}, {"i": 4, "quoteId": ids["Sem música a"]},
        {"i": 6, "text": "o que NÃO me mata, me fortalece!"},
        {"i": 3, "text": "Prefiro morrer tendo falado a minha maneira"}])
    lines = script.write("debate", "a verdade", ["Sócrates", "Nietzsche"], sheets=[s, n], use_llm=True)["lines"]
    assert script.check(lines, [s, n]) == []
    close = lines[6]
    assert close["speaker"] == 1 and close["kind"] == "quote"
    assert close["text"] == "O que não me mata me fortalece."  # the sheet's literal text, not the writer's
    assert "Crepúsculo dos Ídolos" in close["quote"]["source"] and close["quote"]["url"].startswith("https://")
    # 7 of 15 words is too little to call it the quote: it stays a marked paraphrase and is not an unmarked copy
    assert lines[3]["kind"] == "paraphrase" and not script.copies_quote(lines[3]["text"], s)
    texts = [line["text"] for line in lines if line["kind"] == "quote"]
    assert len(texts) == len(set(texts))  # never the same quote twice


def test_quote_beats_prefer_the_topic_and_pacing_stays_tight(monkeypatch):
    s, n = socrates(), nietzsche()
    off_topic = next(q["id"] for q in n["quotes"] if q["text"].startswith("Sem música"))
    monkeypatch.setattr(script, "_gemini", lambda prompt: [{"i": 2, "quoteId": off_topic}])
    out = script.write("debate", "a verdade", ["Sócrates", "Nietzsche"], sheets=[s, n], use_llm=True)
    lines = out["lines"]
    # the index-page quote ("Sachregister") also matches the topic but is not primary
    assert lines[2]["kind"] == "quote" and lines[2]["text"] == "As convicções são inimigas mais perigosas da verdade do que as mentiras."
    assert "Humano, Demasiado Humano" in lines[2]["quote"]["source"]
    assert all("Sachregister" not in line["quote"]["source"] for line in lines if line["kind"] == "quote")
    assert [line["beat"] for line in lines][0] == "gancho" and lines[-1]["beat"] == "fechamento"
    assert all(a["speaker"] != b["speaker"] for a, b in zip(lines[1:-1], lines[2:-1]))  # alternating after the hook
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


def test_offline_lesson_has_two_real_quotes():
    out = script.write("lesson", "a verdade existe?", ["Sócrates"], sheets=[socrates()], use_llm=False)
    lines = out["lines"]
    assert [line["beat"] for line in lines] == LESSON_BEATS
    assert out["quoteLines"] >= 2 and all(line["kind"] == "quote" for line in lines if line["beat"] == "citação")
    assert lines[1]["text"].endswith("?")  # the lesson opens on a question, not on "Segundo:" without a "Primeiro"
    assert not any(t in line["text"] for line in lines for t in ("Segundo", "sobre verdade"))


def test_offline_debate_has_a_turn_and_closes_on_the_strongest_quote():
    out = script.write("debate", "A verdade existe?", ["Sócrates", "Nietzsche"], sheets=[socrates(), nietzsche()], use_llm=False)
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
        lines = script.write(template, topic, names, sheets=sheets, use_llm=False)["lines"]
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
