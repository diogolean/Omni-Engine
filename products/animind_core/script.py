"""Debate, lesson and solo scripts anchored in real quotes.

Every line is one of two kinds:

* ``quote``: the spoken text *is* a quote from the speaker's persona sheet
  (:mod:`products.animind_core.personas`), byte for byte, with its source
  and links in ``line["quote"]``. Only code copies quote text; the writer
  (Gemini) only picks a quote id.
* ``paraphrase``: words written for the character, marked as such. When the
  writer says which quote it paraphrases, ``line["basis"]`` points to it.

Only primary-source quotes (``sourceKind: primary`` in the sheet) are ever
shown as quotes; magazine/anthology/"citado em" entries stay out. A written
line whose text is (normalized) one of the speaker's primary quotes, or a
literal excerpt of one, becomes that quote with its source instead of a
paraphrase. Quote beats prefer quotes that share words with the topic.

Shape and rhythm come from a fixed beat plan (hook, short alternating
replies, rebuttal, closing; or hook, three points, example, summary), each
beat with a word budget that keeps the edge-tts clip under ``maxS``.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any

from . import knowledge, personas, settings

_LOG = logging.getLogger("animind_core")
WRITER_MODEL = "gemini-2.5-flash"
WORDS_PER_S = 2.6  # edge-tts pt-BR/en-US neural voices at the default rate
QUOTE_MARKS = "\"“”«»„‟"


@dataclass(frozen=True)
class Beat:
    name: str
    speaker: int
    kind: str  # "quote" (falls back to "paraphrase" when the sheet has none that fits) or "paraphrase"
    max_words: int
    pause_s: float = 0.2
    rate: int = 0  # edge-tts pace for this beat, on top of the cast voice (quotes a bit slower, jabs a bit faster)
    optional: bool = False  # offline: a quote beat that becomes a written line when no quote answers the line before
    strong: bool = False  # closing quote: short and punchy (a contrast, a turn) beats long and explanatory
    act: str = ""  # debate act: abertura, confronto, virada, fechamento


# Dramatic shape. Debate (speakers alternate, every line answers the one before):
#   abertura   A hook: a provocation aimed at B
#   confronto  B answers with a real quote (his position) -> A attacks its key word -> B jabs back
#   virada     A turns it with a real quote of his own -> B reacts to that quote
#   fechamento A turns B's own words against him -> B concedes nothing -> A lands a real quote (the punchline)
# Each speaker says at least one real quote of his own sheet; written lines are short and of varied length.
# Lesson: hook question -> the right question -> quote -> everyday example -> quote -> synthesis.
PLANS: dict[str, tuple[Beat, ...]] = {
    "debate": (
        Beat("gancho", 0, "paraphrase", 14, 0.45, act="abertura"),
        Beat("tese", 1, "quote", 28, 0.4, -4, act="confronto"),
        Beat("réplica", 0, "paraphrase", 14, 0.2, 6, act="confronto"),
        Beat("réplica", 1, "paraphrase", 11, 0.25, 7, act="confronto"),
        Beat("virada", 0, "quote", 28, 0.6, -5, act="virada"),
        Beat("réplica", 1, "paraphrase", 13, 0.25, 4, act="virada"),
        Beat("tréplica", 0, "paraphrase", 14, 0.35, 2, act="fechamento"),
        Beat("fechamento", 1, "paraphrase", 11, 0.45, 3, act="fechamento"),
        Beat("fechamento", 0, "quote", 28, 0.0, -8, optional=True, strong=True, act="fechamento"),
    ),
    "lesson": (
        Beat("gancho", 0, "paraphrase", 14, 0.55),
        Beat("pergunta", 0, "paraphrase", 18, 0.45, -2),
        Beat("citação", 0, "quote", 34, 0.5, -6),
        Beat("exemplo", 0, "paraphrase", 22, 0.4, 4),
        Beat("citação", 0, "quote", 34, 0.5, -6),
        Beat("síntese", 0, "paraphrase", 18, 0.0, -2),
    ),
    "solo": (
        Beat("gancho", 0, "paraphrase", 14, 0.45),
        Beat("tese", 0, "quote", 30, 0.4, -5),
        Beat("argumento", 0, "paraphrase", 18, 0.3, 3),
        Beat("fechamento", 0, "paraphrase", 14, 0.0, -3),
    ),
}


def words(text: str) -> int:
    return len(text.split())


def max_seconds(max_words: int, rate: int = 0) -> float:
    """Clip budget for ``max_words`` at the beat's pace (a slower quote gets more time)."""
    return round(max_words / (WORDS_PER_S * (1 + rate / 100)) + 0.8, 1)


_SOURCE_TAIL = re.compile(r",?\s*(?:Friedrich\b|Traduzido|tradução|Translated|Alfred\b|NuVisio|Hemus\b|Editora\b)|\s+-\s+\d+\s+páginas")
_PAGE = re.compile(r"\b(?:[Pp]ágina|[Pp]age|p\.)\s*\d+,?\s*")


def source_label(source: str, limit: int = 48) -> str:
    """Short on-screen form of a sheet source: work and passage, without page numbers, translators or
    publishers ("Humano, demasiado humano, página 9, Friedrich Nietzsche, Traduzido por ..." ->
    "Humano, demasiado humano"). The full ``source`` stays in the result and the transcript."""
    parts = []
    for part in re.split(r"\s+·\s+", (source or "").replace("\u200e", "")):
        part = re.split(r"\s+[-–—]\s+", _SOURCE_TAIL.split(part, maxsplit=1)[0], maxsplit=1)[0]
        part = re.sub(r"\s*\([^)]*\)", "", _PAGE.sub("", part)).strip(" ,;:.-–—")
        if part and part not in parts:
            parts.append(part)
    while len(parts) > 1 and len(" · ".join(parts)) > limit:
        parts.pop()
    label = " · ".join(parts)
    return label if len(label) <= limit else label[: limit - 1].rstrip(" ,;:·-") + "…"


def cite(q: dict[str, Any]) -> dict[str, Any]:
    out = {"id": q["id"], "text": q["text"], "source": q.get("source", ""), "label": source_label(q.get("source", "")),
           "url": q.get("url", ""), "site": "Wikiquote"}
    if q.get("wikisource"):
        out["wikisource"] = q["wikisource"]
    return out


_STOP_KW = set("""porque quando sobre também apenas mesmo mesma todos todas outro outra outros nossa nosso nossos
entre depois antes sempre nunca coisas coisa muito muita muitos pouco ainda assim então onde aquele aquela
aqueles isto aquilo estes estas esses essas deve devem pode podem seria seriam sendo sido estar estou está
tenho temos fazer feito cada qual quais tudo nada algum alguma ninguém melhor maior menor primeiro
única único forma espécie existe existem existir você vocês hoje isso esse essa este esta pergunta
about there their which would could should these those other every thing things people being exists
today question minha minhas meus suas seus teus tuas dele dela deles delas""".split())
_STOP_FOLD = {personas.fold(w) for w in _STOP_KW}


def _tokens(text: str) -> set[str]:
    """Content words, cut to a 6-letter stem so "verdade" meets "verdades"."""
    return {w[:6] for w in personas.fold(text).split() if len(w) > 3 and w not in _STOP_FOLD}


def _quotable(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    """Quotes that may appear as "Citação real": primary sources only."""
    return [q for q in sheet.get("quotes") or [] if personas.is_primary(q)]


def _eligible(sheet: dict[str, Any], max_words: int) -> list[dict[str, Any]]:
    return [q for q in _quotable(sheet) if words(q["text"]) <= max_words]


# Related stems (folded, 6 letters), so "a verdade existe?" also meets "palavras falsas" or "convicções".
CONCEPTS: tuple[frozenset[str], ...] = tuple(frozenset(g.split()) for g in (
    "verdad falsa falsas falso falsos mentir menti certez convic conhec saber sabedo ignora engano ilusao realid duvida "
    "duvide duvido acredi crenca "
    "truth true false lies knowle wisdom",
    "vida viver vive morte morrer morrem mortal mata matar life live death dying",
    "moral virtud bondad maldad justic etica pecado culpa remors good evil virtue justice",
    "felici alegri prazer sofrim desejo happin pleasu suffer",
    "deus deuses divino religi alma igreja santos god gods divine soul",
    "potenc vontad forca forte fraco fracos vitori guerra power will strong weak",
    "amor amar paixao odio odiar inimig love hate",
    "arte artist musica poesia poeta poetas beleza belo art music poetry beauty",
    "educac ensino ensina aprend mestre aluno escola teach learn school",
    "liberd livre escrav free freedo slave",
    "intel inteli maquin tecnol artifi robo comput machin",
    "medo coragem covard acovar temer teme temor fear courag coward",
    "acao acoes agir fazer obras action deeds",
    "costum habito tradic habit custom",
))


def _related(tokens: set[str]) -> set[str]:
    out = set(tokens)
    for group in CONCEPTS:
        if tokens & group:
            out |= group
    return out


def topic_score(q: dict[str, Any], topic: str) -> int:
    """Shared stems with the topic, counted twice, plus stems of related concepts."""
    t, words_q = _tokens(topic), _tokens(q["text"])
    return 2 * len(t & words_q) + len((_related(t) - t) & words_q)


def chain_score(q: dict[str, Any], topic: str, previous: str) -> int:
    """Topic fit plus overlap with the line before (a reply that answers what was just said)."""
    return 2 * topic_score(q, topic) + len(_related(_tokens(previous)) & _tokens(q["text"]))


def _rank(quotes: list[dict[str, Any]], topic: str, previous: str = "") -> list[dict[str, Any]]:
    """On-topic (and answering the previous line) first, then shorter (tight pacing), then sheet order."""
    return sorted(quotes, key=lambda q: (-chain_score(q, topic, previous), -focus(q, topic), words(q["text"])))


def answers(q: dict[str, Any], topic: str, previous: str) -> bool:
    """An optional quote beat keeps its quote only when it answers the line before (shares one of its ideas) or is
    really about the topic; a quote that merely brushes it ("Sem música a vida seria um erro") becomes a written line."""
    return bool(_related(_tokens(previous)) & _tokens(q["text"])) or focus(q, topic) >= 0.3


def focus(q: dict[str, Any], topic: str) -> float:
    """Share of a quote's content words that belong to the topic's concepts: between two quotes that both
    touch the topic, the one that is *about* it ("homens do conhecimento") beats one that only brushes it."""
    toks = _tokens(q["text"])
    return round(len(toks & _related(_tokens(topic))) / max(1, len(toks)), 3)


def _stems(text: str) -> dict[str, str]:
    """Content words of a line by a 5-letter folded stem ("falsas" and "falsa" meet), stem -> word as said."""
    out: dict[str, str] = {}
    for raw in re.findall(r"[A-Za-zÀ-ÿ]+", text or ""):
        f = personas.fold(singular(raw) or raw)
        if len(f) > 3 and f not in _STOP_FOLD and personas.fold(raw) not in _STOP_FOLD:
            out.setdefault(f[:5], raw.lower())
    return out


def link(previous: str, text: str, *, quote: bool = False, names: tuple[str, ...] | list[str] = ()) -> str:
    """The idea ``text`` picks up from ``previous`` (a word both say), or "" when it does not answer it.

    A real quote cannot be reworded to repeat a word, so a quote also answers a line on the same idea
    (one :data:`CONCEPTS` group: "vontade" meets "força", "verdade" meets "falsas")."""
    a, b = _stems(previous), _stems(text)
    for nm in (*names, _FILLER_ALL()):  # calling the other by name, or a mold's own words, is not answering him
        for st in _stems(nm):
            a.pop(st, None)
            b.pop(st, None)
    shared = [w for st, w in b.items() if st in a]
    if shared:
        return max(shared, key=len)
    if quote:
        for group in CONCEPTS:
            pa = [w for w in a.values() if personas.fold(w)[:6] in group]
            pb = [w for w in b.values() if personas.fold(w)[:6] in group]
            if pa and pb:
                return f"{pa[0]}~{pb[0]}"
    return ""


_TURN = re.compile(r"\b(?:mas|do que|nem|porém|senão|but|than|nor|rather)\b|[;—]|\s-\s", re.I)


def punch(q: dict[str, Any]) -> int:
    """How well a quote closes a video: a contrast or a turn ("prefiro morrer ... do que ... viver"), short."""
    w = words(q["text"])
    return (2 if _TURN.search(q["text"]) else 0) + (1 if w <= 16 else 0) - (1 if w > 22 else 0)


def _rank_strong(quotes: list[dict[str, Any]], topic: str, previous: str = "") -> list[dict[str, Any]]:
    """Closing quote: answers the line before / the topic, then has a turn, then is short."""
    return sorted(quotes, key=lambda q: (-(chain_score(q, topic, previous) + punch(q)), words(q["text"])))


def matching_quote(text: str, pool: list[dict[str, Any]], max_words: int) -> dict[str, Any] | None:
    """The quote a written line actually is: same words (normalized), or a literal excerpt of >= 6 words
    covering most of it, or the line contains the whole quote. The full quote must fit the beat."""
    t = personas.fold(text)
    if not t:
        return None
    for q in pool:
        f = personas.fold(q["text"])
        if not f or words(q["text"]) > max_words:
            continue
        if t == f or (f in t and len(f.split()) >= 4):
            return q
        if t in f and len(t.split()) >= 6 and len(t.split()) >= 0.6 * len(f.split()):
            return q
    return None


def _clean_paraphrase(text: str, max_words: int) -> str:
    text = " ".join(str(text or "").translate({ord(c): None for c in QUOTE_MARKS}).split())
    w = text.split()
    if len(w) > max_words:
        text = " ".join(w[:max_words]).rstrip(",;:—-") + "…"
    return text


def tidy(text: str) -> str:
    """Clean punctuation for a written line: no space before marks, no "?." or ",.", a capital first
    letter and a final mark ("a verdade existe?." -> "A verdade existe?")."""
    t = " ".join(str(text or "").split())
    t = re.sub(r"\s+([,.;:!?…])", r"\1", t)
    t = re.sub(r"([?!…])[.,;:]+", r"\1", t)
    t = re.sub(r"[,;:]+([.?!…])", r"\1", t)
    t = re.sub(r"(?<!\.)\.\.(?!\.)", ".", t)
    t = re.sub(r"([,;:])(?=[A-Za-zÀ-ÿ])", r"\1 ", t)
    t = t.strip(" ,;:")
    if not t:
        return t
    t = t[0].upper() + t[1:]
    t = re.sub(r"([.?!]\s+)([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), t)  # "…" is a breath, not a stop
    return t if t[-1] in ".?!…" else t + "."


def copies_quote(text: str, sheet: dict[str, Any], run: int = 7) -> bool:
    """True when ``text`` repeats >= ``run`` consecutive words of a sheet quote (an unmarked quote)."""
    t = personas.fold(text).split()
    grams = {" ".join(t[i:i + run]) for i in range(len(t) - run + 1)}
    return any(g in personas.fold(q["text"]) for q in sheet.get("quotes") or [] for g in grams)


# ---------------------------------------------------------------- template writer
#
# Without the LLM (no key, no credit, offline) written lines still answer the line before: each reply
# picks up an idea from the previous line (usually the opponent's real quote), the turn uses the
# opponent's own words against him, and the close lands once. Every beat has several molds; a seed from
# topic and cast picks among them, so scripts vary, and no mold or line is used twice in one script.
# Sentences are short with commas, "?" and "…" where edge-tts should breathe.

_NOUN_END = ("ade", "ção", "são", "ência", "ância", "ismo", "eza", "ura", "mento", "ia", "or", "ude", "agem",
             "ness", "ity", "tion", "ment", "ism")


_VERB_END = ("am", "em", "ar", "er", "ir", "ou", "ava", "iam", "ndo")


def singular(word: str) -> str:
    """Plain pt-BR/en singular of a plural noun or adjective, or "" when unsure."""
    w = word.lower()
    for end, rep in (("ões", "ão"), ("ães", "ão"), ("ais", "al"), ("éis", "el"), ("eis", "el")):
        if w.endswith(end):
            return w[: -len(end)] + rep
    if w.endswith(("as", "os", "des", "tes", "ces", "ges", "ves", "nes", "ies")) and not w.endswith("ies"):
        return w[:-1]
    return "" if w.endswith("s") and not w.endswith(("ss", "us", "is")) else w


_DET = set("a o as os um uma uns umas da do das dos na no nas nos pela pelo pelas pelos minha meu sua seu tua teu nossa nosso "
           "the a an my your his her our".split())


def keyword(text: str, topic: str = "", *, related_only: bool = False, avoid: str = "", nouny: bool = False) -> str:
    """The word a reply picks up from ``text``: topic-related nouns first, then nouns (after an article or
    a noun ending), then longer words; singular, never verbs or function words. ``avoid`` (the topic noun)
    is skipped so a reply names the *new* idea; very long words (mouthfuls on screen and in TTS) rank lower.
    ``nouny``: a word that is neither after an article nor shaped like a noun ranks much lower (a reply says
    "Se tudo é vontade", not "Se tudo é falsa")."""
    near = _related(_tokens(topic))
    skip = {personas.fold(singular(w) or w) for w in re.findall(r"[A-Za-zÀ-ÿ]+", avoid or "")}
    best, score = "", -99.0
    prev = ""
    for raw in re.findall(r"[A-Za-zÀ-ÿ]+(?:-[A-Za-zÀ-ÿ]+)?", text or ""):
        after_det, prev = prev in _DET, raw.lower()
        w = singular(raw)
        if ("-" in raw or not w or raw.lower().endswith("mos") or w in _STOP_KW or raw.lower() in _STOP_KW
                or personas.fold(w) in _STOP_FOLD or len(w) < 4):
            continue
        if personas.fold(w) in skip:
            continue
        stem = personas.fold(w)[:6]
        related = stem in near
        if (len(w) == 4 and not related) or (related_only and not related):
            continue
        sc = (3.0 if related else 0.0) + (2.0 if w.endswith(_NOUN_END) else 0.0) + (2.0 if after_det else 0.0) + min(len(w), 9) / 10
        sc -= 2.5 if w.endswith(_VERB_END) and not w.endswith(_NOUN_END) and not after_det else 0.0
        sc -= 1.5 if len(w) > 12 else 0.0
        sc -= 3.0 if nouny and not after_det and not w.endswith(_NOUN_END) else 0.0
        if sc > score:
            best, score = w, sc
    return best


def topic_parts(topic: str) -> tuple[str, str, bool]:
    """``(question/headline, key noun, is_question)`` for "a verdade existe?" -> ("A verdade existe?", "verdade", True)."""
    t = " ".join((topic or "").split()).strip(" .")
    is_q = t.endswith("?")
    head = tidy(t)
    noun = t.rstrip("?").lower() if not is_q and len(t.split()) <= 4 else (keyword(t, t) or t.rstrip("?").lower())
    return head, noun, is_q


def noun_phrase(topic: str, noun: str) -> str:
    """The noun with the article the topic gives it ("A verdade existe?" -> "a verdade"), so templates
    say "sobre a verdade" and not "sobre verdade"; bare noun when the topic has none."""
    toks = re.findall(r"[A-Za-zÀ-ÿ]+", (topic or "").lower())
    first = noun.split()[0] if noun else ""
    for i, w in enumerate(toks):
        if w == first and i > 0 and toks[i - 1] in ("a", "o", "as", "os", "the"):
            return f"{toks[i - 1]} {noun}"
    return noun


def _bank(lang: str) -> dict[str, list[str]]:
    """Molds per beat (``<beat><speaker>`` for a debate, else ``<beat>``). Fields: {Q}/{q} topic as asked
    (capital / lower first letter), {noun} bare key noun, {the}/{The} noun with its article, {a}/{b} speakers,
    {kw}/{Kw} the idea picked up from the line before (every reply mold names it, so the line answers that
    line), {kq} a word from the opponent's own real quote (the turn uses his words against him), {kt} a topic
    word the other used."""
    if lang == "en-US":
        return {
            "gancho2q": ["{b}, quick answer: {q} No speeches.",
                         "{Q} Yes or no, {b}. No dodging.",
                         "One question, {b}, and nobody leaves without an answer. {Q}",
                         "I bet you cannot answer in one sentence, {b}. {Q}"],
            "gancho2kq": ["{Q} I bet you run off to {kn}, {b}."],
            "gancho2k": ["{The}, {b}. I bet you run off to {kn}."],
            "gancho2": ["{b}, everyone talks about {the}. Nobody knows what it is. Do you?",
                        "{The}, {b}. Defend it in one sentence, if you can."],
            "gancho1q": ["{Q} Sounds easy. It is not.", "{Q} Take three seconds before you answer."],
            "gancho1": ["Today: {the}. Everything you think you know is about to be tested."],
            "tese": ["My thesis is simple: {the} is not accepted. It is examined."],
            "tese1": ["Easy, {a}: {the} belongs to whoever has the courage to decide.",
                      "Simple, {a}: {the} is whatever survives your doubt."],
            "réplica0": ["{Kw}, {b}? Then tell me: who decides what {noun} is?",
                         "Easy, {b}. If everything is {kw}, so is your sentence.",
                         "You swap {noun} for {kw}, {b}. Is that an answer… or an escape?",
                         "{Kw}? Lovely word, {b}. Now define it."],
            "réplica1": ["{Kw}, {a}? Here you go again. A question is not proof.",
                         "You call {kw} what is only habit, {a}.",
                         "{Kw}, {a}? That is fear of admitting nobody knows.",
                         "Talking about {kw} is easy, {a}. Living is hard.",
                         "{Kw}? Old words, {a}. I want action.",
                         "{Kw}? Prove it, {a}.",
                         "{Kw}, {a}? Cowardice with a pretty name."],
            "virada0": ["{Kw}? Wait, {b}. You said {kq} yourself. So you believe in something.",
                        "Stop, {b}. {Kw} was your word. Now stand by it."],
            "tréplica0": ["{Kw}? But you were the one who said {kq}, {b}. Your own words give you away.",
                          "{Kw}, {b}? A minute ago it was {kq}. Which one do you defend?",
                          "You swap {kq} for {kw}, {b}. That is not an answer. It is an escape."],
            "fechamento1": ["{Kw}? Keep asking, {a}. I would rather live.",
                            "Keep your {kw}, {a}. Life does not wait for answers.",
                            "{Kw} is your word, {a}. I bow to no one.",
                            "{Kw}? I would rather live, {a}."],
            "fechamento0": ["{Kw}, {b}? Then let the audience decide. I only asked questions.",
                            "{Kw}? The question stays, {b}. And it is stronger than both of us."],
            "fechamento": ["Think about it before you trust the next certainty about {the}."],
            "argumento": ["Look at {kw}: without it, {the} is only an opinion."],
            "pergunta": ["Start with the right question: how do you know something is {noun}?",
                         "Before the answer, a better question: who vouches for what you call {noun}?"],
            "citação": ["The ancients already suspected it: {the} does not come ready-made. You look for it."],
            "exemplo": ["An everyday example: someone swears they know all about {the}. Ask calmly: how do you know? And why?",
                        "Example: at a family lunch, everyone is sure about {the}. Ask one question… and see who stays firm."],
            "síntese": ["Summary: {the} is not memorized, it is examined. And the next question is yours.",
                        "Keep this: whoever asks about {the} learns. Whoever is already sure… stopped learning."],
        }
    return {
        "gancho2q": ["{b}, responde rápido: {q} Sem discurso.",
                     "{Q} Sim ou não, {b}. Sem fugir.",
                     "Uma pergunta, {b}, e ninguém sai daqui sem resposta. {Q}",
                     "Aposto que você não responde em uma frase, {b}. {Q}"],
        "gancho2kq": ["{Q} Aposto que você vai fugir pro assunto {kn}, {b}."],
        "gancho2k": ["{The}, {b}. Aposto que você vai fugir pro assunto {kn}."],
        "gancho2": ["{b}, todo mundo fala de {the}. Ninguém sabe o que é. Você sabe?",
                    "{The}, {b}. Defenda em uma frase, se conseguir."],
        "gancho1q": ["{Q} Parece fácil. Não é.", "{Q} Pense três segundos antes de responder."],
        "gancho1": ["Hoje: {the}. Tudo o que você acha que sabe vai passar por um teste."],
        "tese": ["Minha tese é simples: {the} não se aceita. Se examina."],
        "tese1": ["Resposta fácil, {a}: {the} é de quem tem coragem de decidir.",
                  "Simples, {a}: {the} é o que resiste à sua dúvida."],
        "réplica0": ["{Kw}, {b}? Então me diga: quem decide o que é {noun}?",
                     "Calma, {b}. Se tudo é {kw}, sua frase também é.",
                     "Você troca {noun} por {kw}, {b}. Isso é resposta… ou fuga?",
                     "{Kw}? Bonita palavra, {b}. Agora defina."],
        "réplica1": ["{Kw}, {a}? Lá vem você. Pergunta não é prova.",
                     "Você chama de {kw} o que é só costume, {a}.",
                     "{Kw}, {a}? Isso é medo de admitir que ninguém sabe.",
                     "Falar de {kw} é fácil, {a}. Difícil é viver.",
                     "{Kw}? Palavra velha, {a}. Eu quero ação.",
                     "{Kw}? Prove, {a}.",
                     "{Kw}, {a}? Covardia com nome bonito."],
        "virada0": ["{Kw}? Espera, {b}. Você mesmo falou em {kq}. Então acredita em algo.",
                    "Pare, {b}. {Kw} foi palavra sua. Agora sustente."],
        "tréplica0": ["{Kw}? Mas foi você que falou em {kq}, {b}. Sua frase te entrega.",
                      "{Kw}, {b}? Agora há pouco era {kq}. Qual dos dois você defende?",
                      "Você troca {kq} por {kw}, {b}. Isso não é resposta. É fuga."],
        "fechamento1": ["{Kw}? Continue perguntando, {a}. Eu prefiro viver.",
                        "Pode ficar com {kw}, {a}. A vida não espera resposta.",
                        "{Kw} é palavra sua, {a}. Eu não me curvo a ninguém.",
                        "{Kw}? Eu prefiro viver, {a}."],
        "fechamento0": ["{Kw}, {b}? Então que o público decida. Eu só fiz perguntas.",
                        "{Kw}? A pergunta fica, {b}. E ela é mais forte que nós dois."],
        "fechamento": ["Pense nisso antes de acreditar na próxima certeza sobre {the}."],
        "argumento": ["Repare em {kw}: sem isso, {the} vira só opinião."],
        "pergunta": ["Comece pela pergunta certa: como você sabe que algo é {noun}?",
                     "Antes da resposta, uma pergunta melhor: quem garante o que você chama de {noun}?"],
        "citação": ["Os antigos já desconfiavam: {the} não vem pronta. Se procura."],
        "exemplo": ["Exemplo do dia a dia: alguém jura que sabe tudo sobre {the}. Pergunte com calma: como você sabe? E por quê?",
                    "Exemplo: no almoço de família, todo mundo tem certeza sobre {the}. Faça uma pergunta só… e veja quem continua firme."],
        "síntese": ["Síntese: {the} não se decora, se examina. E a próxima pergunta é sua.",
                    "Guarde isto: quem pergunta sobre {the} aprende. Quem já tem certeza… parou de aprender."],
    }


_DEBATE_KEY = re.compile(r"^(?:tese|réplica|virada|tréplica|fechamento)[01]$")


def _filler(lang: str) -> str:
    """Words the debate molds themselves say ("frase", "prova", "costume"): a reply never picks one of them up
    as the opponent's idea, except words of a :data:`CONCEPTS` group ("vida", "medo"), which are real ideas."""
    out = set()
    for key, opts in _bank(lang).items():
        if _DEBATE_KEY.match(key) or key.startswith("gancho2"):
            for mold in opts:
                for w in re.findall(r"[A-Za-zÀ-ÿ]+", re.sub(r"\{\w+\}", " ", mold)):
                    if not any(personas.fold(w)[:6] in g for g in CONCEPTS):
                        out.add(w.lower())
    return " ".join(sorted(out))


def _nounish(word: str, text: str) -> bool:
    """``word`` (a :func:`keyword` pick from ``text``) reads as a noun: noun-shaped or right after an article."""
    if not word:
        return False
    if singular(word.lower()).endswith(_NOUN_END):
        return True
    toks = re.findall(r"[\wÀ-ÿ']+", text.lower())
    return any(t in _DET and singular(nx) == singular(word.lower()) for t, nx in zip(toks, toks[1:]))


_FILLER_CACHE: list[str] = []


def _FILLER_ALL() -> str:  # noqa: N802
    if not _FILLER_CACHE:
        _FILLER_CACHE.append(_filler("pt-BR") + " " + _filler("en-US"))
    return _FILLER_CACHE[0]


def _seed(topic: str, names: list[str], lang: str) -> int:
    import hashlib  # noqa: PLC0415

    return int(hashlib.sha1(f"{personas.fold(topic)}|{'|'.join(names)}|{lang}".encode()).hexdigest()[:8], 16)


def _template_text(beat: Beat, lang: str, topic: str, names: list[str], before: list[dict[str, Any]], n: int,
                   taken: set[str], molds: set[str] | None = None, seed: int = 0, index: int = 0,
                   upcoming: list[str] | None = None) -> str:
    """A written line for ``beat`` that answers ``before`` (the lines so far), never one already in ``taken``
    and never a mold already in ``molds`` (updated in place). In a debate the line picks up an idea of the
    line right before it (and prefers a mold whose text really shares a word with that line). ``upcoming``:
    the real quotes the next speaker may answer with; a mold one of them can answer comes first (set-up and
    payoff, so the next quote is a reply and not a non sequitur)."""
    molds = set() if molds is None else molds
    head, noun, is_q = topic_parts(topic)
    the = noun_phrase(topic, noun)
    a = names[0] if names else ""
    b = names[1] if len(names) > 1 else a
    sp = min(beat.speaker, n - 1)
    prev = before[-1]["text"] if before else ""
    own = [ln["text"] for ln in before if ln["speaker"] == sp and ln.get("kind") == "quote"]
    other = [ln["text"] for ln in before if ln["speaker"] != sp]
    other_quotes = [ln["text"] for ln in before if ln["speaker"] != sp and ln.get("kind") == "quote"]
    # ideas already picked up are not picked up again ("Convicção?" once, not three times) unless the line
    # before offers nothing else; names are never an idea
    picked = [(ln.get("replyTo") or {}).get("idea", "") for ln in before]
    filler = _filler(lang) if n > 1 else ""
    said = " ".join([noun, *names, filler, *[w for w in picked if w and "~" not in w]])
    kw = ""
    if prev and (n == 1 or before[-1]["speaker"] != sp):
        # nothing new in the line before -> pick up its clash word again ("Vontade, Sócrates?")
        kw = keyword(prev, topic, avoid=said, nouny=n > 1) or (
            keyword(prev, topic, avoid=" ".join([noun, *names, filler]), nouny=True) if n > 1 else "")
    if not kw and other:
        kw = keyword(other[-1], topic, avoid=said, nouny=n > 1)
    if not kw and own:
        kw = keyword(own[-1], topic, avoid=said, nouny=n > 1)
    kw = kw or noun
    kq = next((k for k in (keyword(t, topic, avoid=" ".join([said, kw]), nouny=True) for t in reversed(other_quotes))
               if _nounish(k, " ".join(other_quotes))), "")
    kt = next((k for k in (keyword(t, topic, related_only=True) for t in reversed(other)) if k), noun)
    # the opener sets up the rival's first quote: when none of his quotes speaks of the topic, the hook
    # names the idea he is going to answer with ("Aposto que você vai fugir para a partida")
    kn = ""
    if beat.name == "gancho" and n > 1 and upcoming and not any(link(topic, q, quote=True) for q in upcoming):
        picks = [k for k in (keyword(q, topic, avoid=" ".join([noun, *names, filler]), nouny=True) for q in upcoming) if k]
        kn = next((k for k in picks if singular(k.lower()).endswith(_NOUN_END)), "") or next(
            (k for k in picks if _nounish(k, " ".join(upcoming))), "")
    bank = _bank(lang)
    if beat.name == "gancho":
        key = ("gancho2" + ("k" if kn else "") if n > 1 else "gancho1") + ("q" if is_q else "")
    elif n > 1 and f"{beat.name}{sp}" in bank:
        key = f"{beat.name}{sp}"
    else:
        key = beat.name if beat.name in bank else ("réplica0" if sp == 0 else "réplica1")
    fields = {"Q": head, "q": head[:1].lower() + head[1:], "noun": noun, "the": the, "The": the[:1].upper() + the[1:],
              "a": a, "b": b, "kw": kw, "Kw": kw[:1].upper() + kw[1:], "kq": kq, "Kq": kq[:1].upper() + kq[1:], "kt": kt, "kn": kn}
    fallback = "réplica0" if sp == 0 else "réplica1"
    keys = [key, *[k for k in bank if k.startswith(beat.name.split(" ")[0]) and k != key], fallback]
    if n > 1:  # a debate line in the other speaker's mold would talk to the wrong person
        keys = [k for k in keys if not _DEBATE_KEY.match(k) or k.endswith(str(sp))] or [key]
    candidates: list[tuple[str, str]] = []
    for k in dict.fromkeys(keys):
        opts = bank[k]
        start = (seed + 7 * index) % len(opts)
        for j in range(len(opts)):
            m = (start + j) % len(opts)
            mold = opts[m]
            if ("{kq}" in mold or "{Kq}" in mold) and not kq:
                continue  # the turn needs a word of the opponent's own quote
            if "{kn}" in mold and not kn:
                continue
            if "{noun}" in mold and personas.fold(kw) == personas.fold(noun):
                continue  # "troca verdade por verdade"
            text = tidy(_contract(mold.format(**fields), lang))
            if f"{k}:{m}" in molds or personas.fold(text) in taken or words(text) > beat.max_words + 1:
                continue
            candidates.append((f"{k}:{m}", text))
    if n > 1 and prev:
        linked = [c for c in candidates if link(prev, c[1], names=names)]
        set_up = [c for c in linked if any(link(c[1], q, quote=True, names=names) for q in upcoming or [])]
        candidates = set_up or linked or candidates
    if candidates:
        molds.add(candidates[0][0])
        return candidates[0][1]
    return tidy(_contract(bank[key][0].format(**fields), lang))


def _contract(text: str, lang: str) -> str:
    """pt-BR contractions a filled mold needs: "fugiu de a verdade" -> "fugiu da verdade"."""
    if lang == "en-US":
        return text
    return re.sub(r"\b([Dd]e|[Ee]m) (a|o|as|os)\b", lambda m: {"d": "d", "e": "n"}[m.group(1)[0].lower()] + m.group(2), text)


# ---------------------------------------------------------------- Gemini writer


EMOTIONS = ("neutro", "alegre", "irritado", "surpreso", "triste", "desdenhoso", "pensativo")
BEAT_EMOTION = {"gancho": "surpreso", "tese": "pensativo", "réplica": "irritado", "tréplica": "irritado",
                "fechamento": "desdenhoso", "argumento": "pensativo", "exemplo": "alegre", "resumo": "neutro",
                "pergunta": "pensativo", "citação": "pensativo", "síntese": "alegre"}


def emotion_of(draft: dict[str, Any], beat_name: str) -> str:
    """The writer's emotion for a line when it is one of :data:`EMOTIONS`, else the beat's default."""
    e = str(draft.get("emotion") or "").strip().lower()
    return e if e in EMOTIONS else BEAT_EMOTION.get(beat_name.split(" ")[0], "neutro")


def _prompt(plan: list[Beat], sheets: list[dict[str, Any]], names: list[str], topic: str, lang: str, template: str,
            passages: list[list[dict[str, Any]]] | None = None, tone: str = "comedia") -> str:
    who = []
    for k, (name, sheet) in enumerate(zip(names, sheets)):
        bg = "\n".join(f'    [{p["source"]}] {p["text"][:600]}' for p in (passages[k] if passages else []))
        qs = "\n".join(
            f'    {q["id"]}: {q["text"]}' + ("   [on topic]" if topic_score(q, topic) else "")
            for q in _rank(_quotable(sheet), topic)
        ) or "    (no quotes)"
        who.append(f"Speaker {k}: {name}\n  Real quotes from primary sources, best for the topic first (id: text):\n{qs}"
                   + (f"\n  Background passages about {name} (ideas, facts and vocabulary only; NOT quotes):\n{bg}" if bg else ""))
    beats = "\n".join(
        f"  {i}. speaker {b.speaker}, beat '{b.name}'" + (f" (act: {b.act})" if b.act else "") + ", "
        + (f"PICK a quoteId of speaker {b.speaker} (only quotes of at most {b.max_words} words), do not write text"
           + (" (if no quote answers the previous line, WRITE text of at most 16 words instead)" if b.optional else "")
           if b.kind == "quote" else f"WRITE text of at most {b.max_words} words")
        for i, b in enumerate(plan)
    )
    language = "Brazilian Portuguese" if lang == "pt-BR" else "American English"
    shape = {
        "debate": "a tight, cinematic debate for a 60-second vertical video in four acts: opening hook, clash of short alternating replies that answer each other, a turn in the middle (a real quote that changes the fight), and a strong close",
        "lesson": "a short lesson that progresses: a hook question, the right question, a real quote, one concrete everyday example, a second quote, a one-line synthesis",
        "solo": "a short monologue: hook, thesis, one argument, close",
    }[template]
    from . import styles  # noqa: PLC0415
    tone_line = styles.TONES[styles.pick_tone(tone)]["prompt"]
    return f"""You write {shape}, in {language}, about: {topic}
Tone: {tone_line}.

{chr(10).join(who)}

Beats, in order:
{beats}

Rules:
- Quote beats: choose the quoteId whose idea best fits the topic and the previous line (prefer [on topic] quotes); never reuse a quoteId.
- Never reword a famous line of the speaker in a written beat: if you want it, it must be a quote beat with its quoteId.
- Written beats: spoken lines in the character's voice and ideas, reacting to the line before. Short sentences, concrete words,
  no stage directions, no emoji, no quotation marks. Never present your text as a quote and never copy a quote into it.
- Background passages are research notes: use their ideas, facts and vocabulary in written beats, but in your own words.
  Never copy a sentence from them and never present them as something the speaker said.
- If a written line restates one of the speaker's quotes, give that quoteId as basisId; else basisId is null.
- The hook grabs in 3 seconds: a provocative question or claim about the topic, aimed at the other speaker when there is one.
- Each reply answers the line right before it: it must repeat one content word (or its idea) of the line right before
  it — the word you attack. No parallel monologues. A quote beat picks the quote that answers the line before.
- Each speaker says at least one of his real quotes (quote beats only; never invent or alter a quote).
- The turn ('virada', in the middle) is a quote that flips the fight; the rebuttal turns the opponent's own words.
- The turn (tréplica) uses the opponent's own words against him. Closing: the second-to-last line concedes nothing and
  strikes once; the last line is the strongest quote, a punchline that ends the video.
- Rhythm: written lines of at most 16 words and at least one jab of at most 9 words; vary the lengths; one idea per sentence, sentences of 3 to 9 words, commas and "…" where the voice should breathe; never two
  lines built on the same pattern; address the other speaker by name in replies.
- In a lesson, the question beat reframes the hook, the example is concrete and everyday and ties to the quote before it,
  and the synthesis closes the loop in one line.

- Every line gets the speaker's emotion while saying it, one of: {", ".join(EMOTIONS)}.

Answer JSON only: {{"lines": [{{"i": 0, "quoteId": null, "basisId": null, "text": "...", "emotion": "neutro"}}, ...]}} with one entry per beat."""


def _cache_path(prompt: str):
    import hashlib  # noqa: PLC0415

    key = hashlib.sha256(f"{WRITER_MODEL}\n{prompt}".encode()).hexdigest()[:24]
    return settings.data_dir() / "cache" / "script" / f"{key}.json"


def _gemini(prompt: str) -> list[dict[str, Any]]:
    """One Gemini text call per distinct prompt; answers are cached on disk (``<data>/cache/script``), so a
    repeated render of the same topic and cast costs nothing."""
    cached = _cache_path(prompt)
    if cached.is_file():
        return list(json.loads(cached.read_text(encoding="utf-8")).get("lines") or [])
    lines = _gemini_call(prompt)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps({"model": WRITER_MODEL, "lines": lines}, ensure_ascii=False), encoding="utf-8")
    return lines


def _gemini_call(prompt: str) -> list[dict[str, Any]]:
    from google.genai import types  # noqa: PLC0415

    from production.cost.google_guardrail import guarded_generate_content, make_guarded_gemini_client  # noqa: PLC0415

    import os  # noqa: PLC0415

    client = make_guarded_gemini_client((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())
    response = guarded_generate_content(
        client, model=WRITER_MODEL, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.8),
        kind="text", source="animind_core.script.write",
    )
    raw = response.text or ""
    data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    return list(data.get("lines") or [])


# ---------------------------------------------------------------- assembly


def plan_for(template: str, n_chars: int) -> list[Beat]:
    if template == "debate" and n_chars < 2:
        template = "solo"
    return list(PLANS.get(template, PLANS["solo"]))


def write(template: str, topic: str, names: list[str], lang: str = "pt-BR", *,
          sheets: list[dict[str, Any]] | None = None, use_llm: bool | None = None, mode: str = "quick",
          indexes: list[Any] | None = None, tone: str = "comedia") -> dict[str, Any]:
    """Script lines (before TTS) plus the sheets used. ``use_llm`` None -> Gemini when a key is set.

    ``mode`` picks the knowledge index behind written lines (:mod:`knowledge`): ``quick`` (key terms,
    shallow) or ``deep`` (full texts). Retrieved passages only feed ideas; quotes stay verbatim from the sheet."""
    template = template if template in PLANS else "solo"
    topic = (topic or "").strip() or ("inteligência artificial" if lang == "pt-BR" else "artificial intelligence")
    n = 2 if template == "debate" and len(names) >= 2 else 1
    names = [personas.persona_name(names[min(i, len(names) - 1)]) if names else "" for i in range(n)]
    if sheets is None:
        sheets = [personas.load(nm, lang, fetch=settings.personas_online()) for nm in names]
    plan = plan_for(template, n)
    mode = mode if mode in knowledge.MODES else "quick"
    if indexes is None:
        indexes = [knowledge.ensure(nm, lang, mode, sheet=sh) for nm, sh in zip(names, sheets)]
    passages = [knowledge.retrieve(ix, f"{topic} {nm}", k=4 if mode == "deep" else 2) for ix, nm in zip(indexes, names)]
    if use_llm is None:
        use_llm = settings.gemini_available()

    drafted: dict[int, dict[str, Any]] = {}
    writer = "template"
    if use_llm:
        try:
            for entry in _gemini(_prompt(plan, sheets, names, topic, lang, template, passages, tone)):
                if isinstance(entry, dict) and isinstance(entry.get("i"), int):
                    drafted[entry["i"]] = entry
            writer = "gemini"
        except Exception as exc:  # noqa: BLE001 — the template writer always works
            _LOG.warning("script writer fell back to templates: %s", exc)

    used: set[tuple[int, str]] = set()
    lines: list[dict[str, Any]] = []
    molds: set[str] = set()
    seed = _seed(topic, names, lang)
    debate = template == "debate" and n > 1
    for i, beat in enumerate(plan):
        sp = min(beat.speaker, n - 1)
        sheet = sheets[sp]
        by_id = {q["id"]: q for q in _quotable(sheet)}
        draft = drafted.get(i) or {}
        line: dict[str, Any] = {"speaker": sp, "beat": beat.name, "maxS": max_seconds(beat.max_words, beat.rate),
                                "pauseAfterS": beat.pause_s, "rateAdj": beat.rate, "emotion": emotion_of(draft, beat.name)}
        if beat.act:
            line["act"] = beat.act
        previous = lines[-1]["text"] if lines else ""
        answers_prev = (lambda text, quote=False: link(previous, text, quote=quote, names=names)) if debate and lines else None
        quote = None
        if beat.kind == "quote":
            pool = [q for q in _eligible(sheet, beat.max_words) if (sp, q["id"]) not in used]
            qid = draft.get("quoteId")
            ranked = _rank_strong(pool, topic, previous) if beat.strong else _rank(pool, topic, previous)
            if answers_prev:  # a debate quote answers the line before: quotes that pick up its idea come first
                ranked = sorted(ranked, key=lambda q: not answers_prev(q["text"], True))
            quote = next((q for q in pool if q["id"] == qid), None)
            if quote is None or (ranked and not (_tokens(topic) & _tokens(quote["text"])) and topic_score(quote, topic) < topic_score(ranked[0], topic)) \
                    or (answers_prev and not answers_prev(quote["text"], True) and ranked and answers_prev(ranked[0]["text"], True)):
                quote = next(iter(ranked), None)  # unknown/reused/off-topic id, or one that ignores the line before
            if quote is not None and beat.optional:
                if answers_prev and not answers_prev(quote["text"], True):
                    quote = None  # no quote answers the line before -> a written line, not a non sequitur
                elif not answers_prev and not qid and not answers(quote, topic, previous):
                    quote = None
        else:
            # a written line that *is* one of the speaker's real quotes is shown as that quote, with its source
            pool = [q for q in _quotable(sheet) if (sp, q["id"]) not in used]
            quote = matching_quote(draft.get("text") or "", pool, beat.max_words + 6)
        if quote is not None:
            used.add((sp, quote["id"]))
            line.update(kind="quote", text=quote["text"], quote=cite(quote))
            line["maxS"] = max(line["maxS"], max_seconds(words(quote["text"]), beat.rate))
        else:
            if beat.kind == "quote":  # a quote beat without a fitting quote gets a short written line
                beat = replace(beat, max_words=min(beat.max_words, 16))
            text = tidy(_clean_paraphrase(draft.get("text") or "", beat.max_words))
            taken = {personas.fold(ln["text"]) for ln in lines}
            hook_off_topic = debate and not lines and not link(topic, text, names=names)  # the rival must answer the topic
            drafted_text = bool(text)
            if (not text or personas.fold(text) in taken or copies_quote(text, sheet) or hook_off_topic
                    or knowledge.copies_passage(text, passages[sp]) or (answers_prev and not answers_prev(text))):
                drafted_text = False
                nxt = plan[i + 1] if i + 1 < len(plan) else None
                nsp = min(nxt.speaker, n - 1) if nxt else 0
                upcoming = ([q["text"] for q in _rank([q for q in _eligible(sheets[nsp], nxt.max_words)
                                                       if (nsp, q["id"]) not in used], topic, "")]
                            if debate and nxt and nxt.kind == "quote" else None)
                text = _template_text(beat, lang, topic, names, lines, n, taken, molds, seed, i, upcoming)
            line.update(kind="paraphrase", text=text)
            basis = by_id.get(draft.get("basisId") or "") if drafted_text else None  # a replaced draft keeps no basis
            if basis is not None:
                line["basis"] = {"id": basis["id"], "source": basis.get("source", ""),
                                 "label": source_label(basis.get("source", "")), "url": basis.get("url", "")}
        if lines:
            idea = link(previous, line["text"], quote=line["kind"] == "quote", names=names)
            line["replyTo"] = {"line": len(lines) - 1, "idea": idea}
        lines.append(line)
    personas_out = [
        {"name": nm, "found": bool(s.get("found")), "wikiquote": s.get("wikiquote"), "wikipedia": s.get("wikipedia"),
         "quotes": len(s.get("quotes") or []), "primaryQuotes": len(_quotable(s)), "license": s.get("license"),
         "mode": ix[0].get("mode", mode), "chunks": len(ix[0].get("chunks") or []),
         "passages": [{"source": p["source"], "url": p["url"], "score": p["score"]} for p in ps]}
        for nm, s, ix, ps in zip(names, sheets, indexes, passages)
    ]
    out = {"lines": lines, "writer": writer, "personas": personas_out, "topic": topic, "tone": tone,
           "quoteLines": sum(1 for ln in lines if ln["kind"] == "quote")}
    if debate:
        problems = check(lines, sheets) + check_debate(lines, sheets, names)
        out["scriptQA"] = {"pass": not problems, "problems": problems}
        if problems:
            _LOG.warning("debate script QA: %s", "; ".join(problems))
    return out


def check(lines: list[dict[str, Any]], sheets: list[dict[str, Any]]) -> list[str]:
    """Problems with a script: quotes not literally in the speaker's sheet, unmarked lines, budgets."""
    errors = []
    for i, line in enumerate(lines):
        sheet = sheets[min(line["speaker"], len(sheets) - 1)]
        texts = {q["id"]: q["text"] for q in sheet.get("quotes") or []}
        if line.get("kind") == "quote":
            q = line.get("quote") or {}
            if texts.get(q.get("id")) != q.get("text") or line["text"] != q.get("text"):
                errors.append(f"line {i}: quote is not literally in the sheet")
            elif not personas.is_primary(next(x for x in sheet["quotes"] if x["id"] == q["id"])):
                errors.append(f"line {i}: quote comes from a secondary source")
            if not str(q.get("url", "")).startswith("https://"):
                errors.append(f"line {i}: quote has no source link")
        elif line.get("kind") == "paraphrase":
            if line.get("quote"):
                errors.append(f"line {i}: paraphrase carries a quote")
            if any(c in line["text"] for c in QUOTE_MARKS):
                errors.append(f"line {i}: paraphrase uses quotation marks")
        else:
            errors.append(f"line {i}: unmarked line")
    return errors


PARAPHRASE_MAX_WORDS = 16
QUOTE_MAX_WORDS = 30


def check_debate(lines: list[dict[str, Any]], sheets: list[dict[str, Any]], names: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Problems with a debate's shape: speakers alternate (no parallel monologues), every line answers the
    one before (a shared idea), the arc opens on a hook, turns in the middle and closes, each speaker says
    at least one real quote of his own sheet when it has one that fits, and lines stay short and varied."""
    errors: list[str] = []
    if len(lines) < 5:
        return ["debate: fewer than 5 lines"]
    for i in range(1, len(lines)):
        prev, line = lines[i - 1], lines[i]
        if line["speaker"] == prev["speaker"]:
            errors.append(f"line {i}: same speaker twice in a row")
        if not link(prev["text"], line["text"], quote=line.get("kind") == "quote", names=names):
            errors.append(f"line {i}: does not answer line {i - 1}")
    if lines[0].get("beat") != "gancho":
        errors.append("debate: does not open on a hook")
    if lines[-1].get("beat") != "fechamento":
        errors.append("debate: does not close")
    turns = [i for i, ln in enumerate(lines) if ln.get("act") == "virada" or ln.get("beat") == "virada"]
    if not any(len(lines) / 4 <= i <= 3 * len(lines) / 4 for i in turns):
        errors.append("debate: no turn in the middle")
    for sp, sheet in enumerate(sheets[:2]):
        if _eligible(sheet, QUOTE_MAX_WORDS - 2) and not any(ln["speaker"] == sp and ln.get("kind") == "quote" for ln in lines):
            errors.append(f"speaker {sp}: no real quote of his own sheet")
    counts = [words(ln["text"]) for ln in lines]
    for i, (ln, w) in enumerate(zip(lines, counts)):
        if w > (QUOTE_MAX_WORDS if ln.get("kind") == "quote" else PARAPHRASE_MAX_WORDS + 1):
            errors.append(f"line {i}: {w} words is too long")
    if min(counts) > 9:
        errors.append("debate: no short line (<= 9 words)")
    if len(set(counts)) < len(counts) // 2:
        errors.append("debate: line lengths do not vary")
    return errors
