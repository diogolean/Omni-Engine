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
from time import perf_counter
from typing import Any

from . import knowledge, languages, llm_providers, personas, settings, telemetry

_LOG = logging.getLogger("animind_core")
WRITER_MODEL = llm_providers.GEMINI_MODEL  # compatibility name for the Gemini fallback
# Bump when a QA rule changes so a cached draft that fails the new rule is not reused.
SCRIPT_QA_VERSION = "6"
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
# Lesson: hook -> the right question (abertura) -> quote -> everyday example that unpacks it (desenvolvimento)
# -> a second, punchier quote that turns the lesson (virada) -> a one-line synthesis that recalls it (fechamento).
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
        Beat("gancho", 0, "paraphrase", 14, 0.55, act="abertura"),
        Beat("pergunta", 0, "paraphrase", 16, 0.45, -2, act="abertura"),
        Beat("citação", 0, "quote", 34, 0.5, -6, act="desenvolvimento"),
        Beat("exemplo", 0, "paraphrase", 20, 0.4, 4, act="desenvolvimento"),
        Beat("virada", 0, "quote", 34, 0.6, -6, optional=True, strong=True, act="virada"),
        Beat("síntese", 0, "paraphrase", 16, 0.0, -2, act="fechamento"),
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
today question minha minhas meus suas seus teus tuas dele dela deles delas palavra palavras word words""".split())
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


def on_thread(text: str, previous: str, topic: str) -> bool:
    """A lesson quote stays on the lesson's thread: it shares an idea with the line before it or with the topic."""
    return bool(link(previous, text, quote=True) or link(topic, text, quote=True))


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


def _unquoted(text: str) -> str:
    return " ".join(str(text or "").translate({ord(c): None for c in QUOTE_MARKS}).split())


def _clean_paraphrase(text: str, max_words: int) -> str:
    text = _unquoted(text)
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


_TOPIC_SKIP = {
    "why", "how", "what", "when", "where", "who", "whom", "whose", "which", "does", "did",
    "get", "gets", "got", "that", "this", "with", "from", "into", "about", "your", "have",
    "been", "porque", "como", "quando", "onde", "para",
}


def _topic_noun(topic: str) -> str:
    """One noun from a topic. Participles such as ``explained`` are not the subject."""
    raw_words = re.findall(r"[A-Za-zÀ-ÿ]+", (topic or "").lower())
    kept: list[str] = []
    for word in raw_words:
        if word in _TOPIC_SKIP or word in _STOP_KW or len(word) < 4:
            continue
        if word.endswith(("ed", "ing")) and not word.endswith(_NOUN_END):
            continue
        kept.append(singular(word) or word)
    if kept:
        return kept[0]
    return keyword(topic, topic) or (raw_words[-1] if raw_words else "topic")


def topic_parts(topic: str) -> tuple[str, str, bool]:
    """``(question/headline, key noun, is_question)`` for "a verdade existe?" -> ("A verdade existe?", "verdade", True)."""
    t = " ".join((topic or "").split()).strip(" .")
    is_q = t.endswith("?")
    head = tidy(t)
    noun = t.rstrip("?").lower() if not is_q and len(t.split()) <= 2 else _topic_noun(t)
    if re.match(r"(?i)^(?:should|can|could|do|does|is|are|will|would)\b", t):
        # Modal English questions end on the proposition's object.  Keeping
        # that noun ("claims"), rather than a longer verb ("verify"), makes
        # the offline exchange discuss the actual issue.
        tail = re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)?", t.rstrip("?"))
        if tail:
            raw_tail = tail[-1].lower()
            noun = singular(raw_tail) or (
                raw_tail[:-1] if len(raw_tail) > 4 and raw_tail.endswith("s")
                and not raw_tail.endswith(("ss", "us", "is")) else raw_tail
            )
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
            "gancho1": ["Today: {the}. Everything you think you know gets tested.", "{The}. Sounds simple. It is not."],
            "tese": ["My thesis is simple: {the} is not accepted. It is examined."],
            "tese1": ["No, {a}. {Kw} earns trust only when evidence can prove it wrong.",
                      "Yes, {a}, when the {kw} is tested against real consequences."],
            "réplica0": ["{Kw} needs a test, {b}. What evidence would prove your position wrong?",
                         "If {kw} explains everything, {b}, it predicts nothing.",
                         "You replace {noun} with {kw}, {b}. What problem does that solve?",
                         "{Kw} is the premise, {b}. Now show the conclusion."],
            "réplica1": ["{Kw} is evidence, {a}, but it is not the whole argument.",
                         "Your test for {kw} ignores the cost of being wrong, {a}.",
                         "{Kw} explains the doubt, {a}. It does not settle the question.",
                         "A definition of {kw} still needs a concrete case, {a}.",
                         "{Kw} matters, {a}. Consequences matter too.",
                         "{Kw}? State the evidence, {a}."],
            "virada0": ["{Kw}? Wait, {b}. You said {kq} yourself. So you believe in something.",
                        "Stop, {b}. {Kw} was your word. Stand by it."],
            "tréplica0": ["{Kw}? But you were the one who said {kq}, {b}. Your own words give you away.",
                          "{Kw}, {b}? A minute ago it was {kq}. Which one do you defend?",
                          "You swap {kq} for {kw}, {b}. That is not an answer. It is an escape."],
            "fechamento1": ["Test {kw}, {a}. Life demands a choice.",
                            "Measure {kw}, {a}. Life reveals consequences.",
                            "{Kw} settles nothing, {a}. Life is the test."],
            "fechamento0": ["{Kw}, {b}? Then let the audience decide. I only asked questions.",
                            "{Kw}? The question stays, {b}. And it is stronger than both of us."],
            "fechamento": ["Think about it before you trust the next certainty about {the}."],
            "argumento": [
                "Look at {kw}: without it, {the} is only an opinion.",
                "{Kw} answers the line before. The rest of {the} follows from it.",
                "If {kw} is the claim, {the} still needs one concrete test.",
            ],
            "pergunta": ["Start with the right question: how do you know something is {bare}?",
                         "Before the answer, a better question: who vouches for what you call {bare}?"],
            "citação": ["The ancients already suspected it: nobody gets {the} as a gift. You look for it."],
            "virada": ["But here is the turn: nobody gets {the} as a gift. You search, question by question.",
                       "Now the turn: what if the answer about {the} hides in the word {kw}?"],
            "exemplo": ["Notice the word {kw}. At a family lunch, everyone uses it. Ask what it means… and see who stays firm.",
                        "An everyday example: someone talks about {kw} with total certainty. Ask calmly: how do you know?",
                        "Take {kw} to your next conversation. Ask why… twice. Certainty fades fast."],
            "síntese": ["Keep this: without {kw}, {the} is only an opinion. And the next question is yours.",
                        "Keep the word {kw}: it is how {the} gets examined.",
                        "The lesson: {the} is not memorized, it is examined. And the next question is yours.",
                        "The lesson: whoever examines {kw} understands {the}. Whoever is sure… stopped learning."],
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
        "gancho1": ["Hoje: {the}. Tudo o que você acha que sabe vai ser testado.", "{The}. Parece simples. Não é."],
        "tese": ["Minha tese é simples: {the} não se aceita. Se examina."],
        "tese1": ["Não, {a}. {Kw} só merece confiança quando a evidência pode refutá-la.",
                  "Sim, {a}, quando {kw} é testada contra consequências reais."],
        "réplica0": ["{Kw} precisa de um teste, {b}. Que evidência provaria sua tese errada?",
                     "Se {kw} explica tudo, {b}, não prevê nada.",
                     "Você troca {noun} por {kw}, {b}. Que problema isso resolve?",
                     "{Kw} é a premissa, {b}. Agora mostre a conclusão."],
        "réplica1": ["{Kw} é evidência, {a}, mas não é o argumento inteiro.",
                     "Seu teste de {kw} ignora o custo de errar, {a}.",
                     "{Kw} explica a dúvida, {a}. Não encerra a questão.",
                     "Uma definição de {kw} ainda precisa de um caso concreto, {a}.",
                     "{Kw} importa, {a}. As consequências também.",
                     "{Kw}? Mostre a evidência, {a}."],
        "virada0": ["{Kw}? Espera, {b}. Você mesmo falou em {kq}. Então acredita em algo.",
                    "Pare, {b}. {Kw} foi palavra sua. Sustente."],
        "tréplica0": ["{Kw}? Mas foi você que falou em {kq}, {b}. Sua frase te entrega.",
                      "{Kw}, {b}? Agora há pouco era {kq}. Qual dos dois você defende?",
                      "Você troca {kq} por {kw}, {b}. Isso não é resposta. É fuga."],
        "fechamento1": ["Teste {kw}, {a}. A vida exige escolha.",
                        "Meça {kw}, {a}. A vida revela consequências.",
                        "{Kw} não encerra nada, {a}. A vida testa."],
        "fechamento0": ["{Kw}, {b}? Então que o público decida. Eu só fiz perguntas.",
                        "{Kw}? A pergunta fica, {b}. E ela é mais forte que nós dois."],
        "fechamento": ["Pense nisso antes de acreditar na próxima certeza sobre {the}."],
            "argumento": [
                "Repare em {kw}: sem isso, {the} vira só opinião.",
                "{Kw} responde a linha anterior. O resto de {the} segue daí.",
                "Se {kw} é a tese, {the} precisa de um teste concreto.",
            ],
        "pergunta": ["Comece pela pergunta certa: como você sabe que algo é {bare}?",
                     "Antes da resposta, uma pergunta melhor: quem garante o que você chama de {bare}?"],
        "citação": ["Os antigos já desconfiavam: ninguém recebe {the} de presente. Se procura."],
        "virada": ["Mas aqui está a virada: ninguém recebe {the} de presente. Se procura, pergunta por pergunta.",
                   "Agora a virada: e se a resposta sobre {the} estiver na palavra {kw}?"],
        "exemplo": ["Repare na palavra {kw}. No almoço de família, todo mundo usa. Pergunte o que significa… e veja quem fica firme.",
                    "Exemplo do dia a dia: alguém fala de {kw} com toda a certeza. Pergunte com calma: como você sabe?",
                    "Leve {kw} para a próxima conversa. Pergunte por quê… duas vezes. A certeza some rápido."],
        "síntese": ["Guarde isto: sem {kw}, {the} vira opinião. E a próxima pergunta é sua.",
                    "Guarde a palavra {kw}: é por ela que {the} se examina.",
                    "Fica a lição: {the} não se decora, se examina. E a próxima pergunta é sua.",
                    "Fica a lição: quem examina {kw} entende {the}. Quem tem certeza… parou de aprender."],
    }


_NEEDS_QUESTION = re.compile(r"Pergunta não é prova|A question is not proof")
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
                   upcoming: list[str] | None = None, objective: str = "entreter",
                   tone: str = "comedia") -> str:
    """A written line for ``beat`` that answers ``before`` (the lines so far), never one already in ``taken``
    and never a mold already in ``molds`` (updated in place). In a debate the line picks up an idea of the
    line right before it (and prefers a mold whose text really shares a word with that line). ``upcoming``:
    the real quotes the next speaker may answer with; a mold one of them can answer comes first (set-up and
    payoff, so the next quote is a reply and not a non sequitur)."""
    molds = set() if molds is None else molds
    head, noun, is_q = topic_parts(topic)
    the = noun_phrase(topic, noun)
    if beat.name == "gancho" and (tone != "comedia" or objective != "entreter"):
        lead = {
            "humor": "Sem piada", "comedia": "Sem piada", "terror": "No escuro",
            "horror": "No escuro", "educacional": "Passo a passo", "sabio": "Pense bem",
            "sábio": "Pense bem", "serio": "Sem atalhos", "sério": "Sem atalhos",
        }.get(tone, "Preste atenção")
        action = {
            "ensinar": "vamos entender", "debater": "vamos confrontar ideias",
            "entreter": "isso vai surpreender", "convencer": "vou provar meu ponto",
        }.get(objective, "isso vai surpreender")
        if lang == "en-US":
            lead = {"No escuro": "In the dark", "Passo a passo": "Step by step",
                    "Pense bem": "Think carefully", "Sem atalhos": "No shortcuts",
                    "Sem piada": "No joke"}.get(lead, "Pay attention")
            action = {"ensinar": "let us understand it", "debater": "let us test both sides",
                      "entreter": "this will surprise you", "convencer": "I will make the case"}.get(
                          objective, "this will surprise you")
        if is_q:
            # Preserve the complete question: reducing it to one keyword made
            # sensible topics read as "Step by step: verify."
            return tidy(f"{head} {action}.")
        return tidy(f"{lead}: {the}. {action}.")
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
    nouny = n > 1 or beat.name in ("exemplo", "síntese")  # "Repare na palavra admiração", not "na palavra começa"
    said = " ".join([noun, *names, filler, *[w for w in picked if w and "~" not in w]])
    kw = ""
    if n == 1 and beat.name in ("virada", "síntese") and own:
        # a lesson's turn and synthesis recall the idea of its real quotes, never a word of a written line
        recent = {personas.fold(w) for idea in picked[-2:] for w in str(idea or "").split("~") if w}
        recall = []
        for t in reversed(own):  # up to four ideas of each quote, best first
            seen: list[str] = []
            while len(seen) < 4:
                k = keyword(t, topic, avoid=" ".join([noun, *names, *seen]), nouny=True)
                if not k or k in seen:
                    break
                seen.append(k)
                recall.append((k, t))
        fresh = lambda kt: personas.fold(kt[0]) in recent  # noqa: E731
        # the turn moves on to a fresh idea of the quote; the synthesis names a noun ("Guarde a palavra alma")
        recall.sort(key=(lambda kt: (fresh(kt), not _nounish(*kt))) if beat.name == "virada"
                    else (lambda kt: (not _nounish(*kt), fresh(kt))))
        kw = recall[0][0] if recall else ""
    if n > 1 and len(before) == 1 and is_q:
        kw = noun  # the thesis answers the proposition, not an incidental verb
    if not kw and prev and (n == 1 or before[-1]["speaker"] != sp):
        # nothing new in the line before -> pick up its clash word again ("Vontade, Sócrates?")
        kw = keyword(prev, topic, avoid=said, nouny=nouny) or (
            keyword(prev, topic, avoid=" ".join([noun, *names, filler]), nouny=True) if n > 1 else "")
    if not kw and other:
        kw = keyword(other[-1], topic, avoid=said, nouny=nouny)
    if not kw and own:
        kw = keyword(own[-1], topic, avoid=said, nouny=nouny)
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
    bare = re.sub(r"^(?:a|o|as|os|the)\s+", "", noun)
    fields = {"Q": head, "q": head[:1].lower() + head[1:], "noun": noun, "bare": bare, "the": the, "The": the[:1].upper() + the[1:],
              "a": a, "b": b, "kw": kw, "Kw": kw[:1].upper() + kw[1:], "kq": kq, "Kq": kq[:1].upper() + kq[1:], "kt": kt, "kn": kn}
    if beat.name == "fechamento" and sp == 1 and upcoming == [] and _kw_is_topical(kw, topic, names):
        return tidy(
            f"{kw} reaches one final verdict, {a}: test it in practice"
            if lang == "en-US"
            else f"{kw} chega a um veredito final, {a}: teste na prática"
        )
    fallback = "réplica0" if sp == 0 else "réplica1"
    keys = [key, *[k for k in bank if k.startswith(beat.name.split(" ")[0]) and k != key], fallback]
    if n > 1:  # a debate line in the other speaker's mold would talk to the wrong person
        keys = [k for k in keys if not _DEBATE_KEY.match(k) or k.endswith(str(sp))] or [key]
    else:  # a lesson or monologue never uses a debate mold (it would talk to a rival who is not there)
        keys = [k for k in keys if not _DEBATE_KEY.match(k)] or [key]
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
            if "{kw}" in mold and ("{the}" in mold or "{The}" in mold) and personas.fold(singular(kw) or kw) in personas.fold(the):
                continue  # "sem verdade, a verdade vira opinião"
            if "{kw}" in mold and personas.fold(kw) in personas.fold(re.sub(r"\{\w+\}", " ", mold)).split():
                continue  # "Repare na palavra palavra"
            if (beat.name in ("exemplo", "síntese") and "{kw}" in mold
                    and (beat.name == "síntese" or not re.search(r"(?:palavra|word) \{kw\}", mold))
                    and not _nounish(kw, " ".join([prev, *own]))):
                continue  # "sem começa, ..." -- only the example's "a palavra X" carries any picked-up word
            if _NEEDS_QUESTION.search(mold) and not prev.rstrip().endswith("?"):
                continue  # "Pergunta não é prova" only answers a question
            text = tidy(_contract(mold.format(**fields), lang))
            if f"{k}:{m}" in molds or personas.fold(text) in taken or words(text) > beat.max_words + 1:
                continue
            candidates.append((f"{k}:{m}", text))
    if prev and n <= 1 and beat.name in {"argumento", "fechamento"}:
        # A monologue reply has to pick up the previous line. A mold that does
        # not is dropped so the safe line, which names that word, is used.
        # Lesson beats (síntese, exemplo, virada) keep their own molds.
        candidates = [c for c in candidates if link(prev, c[1], names=names)]
    elif n > 1 and prev:
        linked = [c for c in candidates if link(prev, c[1], names=names)]
        set_up = [c for c in linked if any(link(c[1], q, quote=True, names=names) for q in upcoming or [])]
        candidates = set_up or linked or candidates
    if candidates:
        molds.add(candidates[0][0])
        return candidates[0][1]
    # A longer duration can consume every suitable mold. Never fall back to a
    # format string that requires an unavailable quote word (the old result
    # was "you were the one who said,"). Keep the exchange connected instead.
    other_name = b if sp == 0 else a
    anchor = kw
    if prev and not link(prev, kw, names=names):
        # The topic noun was already used. Name a word that is actually in the previous line.
        anchor = keyword(prev, "", nouny=False) or kw
    safe = (
        [
            f"{anchor} still needs evidence, {other_name}",
            f"{anchor} has consequences, {other_name}; name one",
            f"{anchor} is the point, {other_name}; what is the test",
        ]
        if lang == "en-US"
        else [
            f"{anchor} ainda precisa de evidência, {other_name}",
            f"{anchor} tem consequências, {other_name}; diga uma",
            f"{anchor} é o ponto, {other_name}; qual é o teste",
        ]
    )
    return tidy(safe[index % len(safe)])


def _contract(text: str, lang: str) -> str:
    """pt-BR contractions a filled mold needs: "fugiu de a verdade" -> "fugiu da verdade"."""
    if lang == "en-US":
        return text
    return re.sub(r"\b([Dd]e|[Ee]m) (a|o|as|os)\b", lambda m: {"d": "d", "e": "n"}[m.group(1)[0].lower()] + m.group(2), text)


# ---------------------------------------------------------------- Gemini writer


EMOTIONS = ("neutro", "alegre", "irritado", "bravo", "surpreso", "triste", "desdenhoso", "ironico", "pensativo")
BEAT_EMOTION = {"gancho": "surpreso", "tese": "pensativo", "réplica": "irritado", "tréplica": "irritado",
                "fechamento": "desdenhoso", "argumento": "pensativo", "exemplo": "alegre", "resumo": "neutro",
                "pergunta": "pensativo", "citação": "pensativo", "síntese": "alegre"}


def emotion_of(draft: dict[str, Any], beat_name: str) -> str:
    """The writer's emotion for a line when it is one of :data:`EMOTIONS`, else the beat's default."""
    e = str(draft.get("emotion") or "").strip().lower()
    return e if e in EMOTIONS else BEAT_EMOTION.get(beat_name.split(" ")[0], "neutro")


def word_budget(duration: int | float | None, words_per_s: float | None) -> int | None:
    """Spoken words that fit the target at the measured pace, after the title and end cards."""
    if duration is None:
        return None
    rate = words_per_s if words_per_s and words_per_s > 0 else WORDS_PER_S
    speech = max(6.0, float(duration) - _CARD_S - 1.0)
    return max(24, int(speech * rate))


def _prompt(plan: list[Beat], sheets: list[dict[str, Any]], names: list[str], topic: str, lang: str, template: str,
            passages: list[list[dict[str, Any]]] | None = None, tone: str = "comedia",
            objective: str = "entreter", duration: int | float | None = None,
            words_per_s: float | None = None) -> str:
    who = []
    for k, (name, sheet) in enumerate(zip(names, sheets)):
        bg = "\n".join(f'    [{p["source"]}] {p["text"][:600]}' for p in (passages[k] if passages else []))
        qs = "\n".join(
            f'    {personas.slug(name)}:{q["id"]}: {q["text"]}' + ("   [on topic]" if topic_score(q, topic) else "")
            for q in _rank(_quotable(sheet), topic)
        ) or "    (no quotes)"
        who.append(f"Speaker {k}: {name}\n  Real quotes from primary sources, best for the topic first (id: text):\n{qs}"
                   + (f"\n  Background passages about {name} (ideas, facts and vocabulary only; NOT quotes):\n{bg}" if bg else ""))
    beats = "\n".join(
        f"  {i}. speaker {b.speaker}, beat '{b.name}'" + (f" (act: {b.act})" if b.act else "") + ", "
        + (f"PICK a quoteId copied from speaker {b.speaker}'s list, prefix included (only quotes of at most {b.max_words} words), do not write text"
           + (" (if no quote answers the previous line, WRITE text of at most 16 words instead)" if b.optional else "")
           if b.kind == "quote" else f"WRITE text of at most {b.max_words} words")
        for i, b in enumerate(plan)
    )
    language = "Brazilian Portuguese" if lang == "pt-BR" else "American English"
    shape = {
        "debate": "a tight, cinematic debate for a 60-second vertical video in four acts: opening hook, clash of short alternating replies that answer each other, a turn in the middle (a real quote that changes the fight), and a strong close",
        "lesson": "a short lesson that progresses: a hook question, the right question, a real quote, one concrete everyday example, a second quote, a one-line synthesis",
        "solo": "a short monologue: hook, thesis, one argument, close",
        "dialogue": "a concise round-table dialogue where every participant responds to the shared topic",
    }[template]
    from . import styles  # noqa: PLC0415
    tone_line = styles.TONES[styles.pick_tone(tone)]["prompt"]
    target = max(10, min(180, round(float(duration or 60))))
    budget = word_budget(duration, words_per_s)
    rate = words_per_s if words_per_s and words_per_s > 0 else WORDS_PER_S
    budget_line = (
        f"Spoken word budget: at most {budget} words ({rate:.2f} words/second for {target}s). "
        "A line over its maximum is rejected."
        if budget else "Keep every line inside its word limit."
    )
    return f"""You write {shape}, in {language}, about: {topic}
Tone: {tone_line}.
Objective: {objective}. Make that objective visible in the hook and close.
Target duration: about {target} seconds, including the title and end cards (about 4 seconds together). {budget_line}

{chr(10).join(who)}

Beats, in order:
{beats}

Rules:
- Quote beats: choose the quoteId whose idea best fits the topic and the previous line (prefer [on topic] quotes); never reuse a quoteId.
- A quoteId belongs to one speaker. Copy it with the speaker prefix (`albert_einstein:q1`). The same q-number on the other speaker is a different quote and is rejected.
- Never reword a famous line of the speaker in a written beat: if you want it, it must be a quote beat with its quoteId.
- Written beats: spoken lines in the character's voice and ideas, reacting to the line before. Short sentences, concrete words,
  no stage directions, no emoji, no quotation marks. Never present your text as a quote and never copy a quote into it.
- Background passages are research notes: use their ideas, facts and vocabulary in written beats, but in your own words.
  Never copy a sentence from them and never present them as something the speaker said.
- If a written line restates one of the speaker's quotes, give that quoteId as basisId; else basisId is null.
- QA, checked on the JSON before the video is made:
  - The hook is a question of at most 14 words and it names the topic.
  - The hook addresses the other speaker by name. No line addresses its own speaker by first name or surname.
  - Every written line is at most 16 words. At least one line is at most 9 words.
  - The close is one punch of at most 12 words.
  - Each speaker uses at least one quoteId from that speaker's own sheet.
  - Put the word count of each line in "words".
- The hook grabs in 3 seconds: a question about the topic, addressed to the other speaker by name.
- Each reply answers the line right before it: it must repeat one content word (or its idea) of the line right before
  it — the word you attack. No parallel monologues. A quote beat picks the quote that answers the line before.
- Do not keep that same content keyword on three consecutive lines. Echo it once, then the next line moves to a different concrete word.
- Speak only in {language}. When a quote is in another language, do not speak the original. Write the translation as text, set basisId to that quote, and leave quoteId null. The original source stays on the basis.
- Each speaker says at least one of his real quotes (quote beats only; never invent or alter a quote).
- The turn ('virada', in the middle) is a quote that flips the fight; the rebuttal turns the opponent's own words.
- The turn (tréplica) uses the opponent's own words against him. Closing: the second-to-last line concedes nothing and
  strikes once; the last line is the strongest quote, a punchline that ends the video.
- Rhythm: written lines of at most 16 words and at least one jab of at most 9 words; vary the lengths; one idea per sentence, sentences of 3 to 9 words, commas and "…" where the voice should breathe; never two
  lines built on the same pattern; address the other speaker by name in replies. Never address the speaker of the line by their own first name or surname.
- In a lesson, the question beat reframes the hook, the example is concrete and everyday and ties to the quote before it,
  and the synthesis closes the loop in one line.

- Every line gets the speaker's emotion while saying it, one of: {", ".join(EMOTIONS)}.

Answer JSON only: {{"lines": [{{"i": 0, "quoteId": null, "basisId": null, "text": "...", "words": 0, "emotion": "neutro"}}, ...]}} with one entry per beat."""


def _cache_path(prompt: str):
    import hashlib  # noqa: PLC0415

    key = hashlib.sha256(f"{WRITER_MODEL}\n{SCRIPT_QA_VERSION}\n{prompt}".encode()).hexdigest()[:24]
    return settings.data_dir() / "cache" / "script" / f"{key}.json"


def _store_writer_cache(prompt: str, lines: list[dict[str, Any]], *, passed: bool) -> None:
    """The Gemini fallback cache follows the same rule: a failed draft is not kept."""
    cached = _cache_path(prompt)
    if not passed:
        cached.unlink(missing_ok=True)
        return
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps({
        "model": WRITER_MODEL, "lines": lines, "qaPass": True, "qaVersion": SCRIPT_QA_VERSION,
    }, ensure_ascii=False), encoding="utf-8")


def _gemini(prompt: str) -> list[dict[str, Any]]:
    """One Gemini text call per distinct prompt; answers are cached on disk (``<data>/cache/script``), so a
    repeated render of the same topic and cast costs nothing. A draft that failed QA is not served again."""
    cached = _cache_path(prompt)
    if cached.is_file():
        raw = json.loads(cached.read_text(encoding="utf-8"))
        version = raw.get("qaVersion")
        if raw.get("qaPass") is False or (version not in (None, "", SCRIPT_QA_VERSION) and version):
            cached.unlink(missing_ok=True)
        else:
            return list(raw.get("lines") or [])
    lines = _gemini_call(prompt)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps({
        "model": WRITER_MODEL, "lines": lines, "qaVersion": SCRIPT_QA_VERSION,
    }, ensure_ascii=False), encoding="utf-8")
    return lines


def _writer_config() -> Any:
    """JSON mode plus a response schema. A bare array is still parsed if the model ignores the schema."""
    from google.genai import types  # noqa: PLC0415

    schema = {
        "type": "OBJECT",
        "properties": {
            "lines": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "i": {"type": "INTEGER"},
                        "quoteId": {"type": "STRING", "nullable": True},
                        "basisId": {"type": "STRING", "nullable": True},
                        "text": {"type": "STRING"},
                        "words": {"type": "INTEGER"},
                        "emotion": {"type": "STRING"},
                    },
                    "required": ["i", "words"],
                },
            }
        },
        "required": ["lines"],
    }
    kwargs: dict[str, Any] = {
        "response_mime_type": "application/json",
        "temperature": 0.8,
        "response_schema": schema,
    }
    try:
        return types.GenerateContentConfig(**kwargs)
    except Exception:  # noqa: BLE001 — older SDKs reject response_schema
        kwargs.pop("response_schema", None)
        return types.GenerateContentConfig(**kwargs)


def _gemini_call(prompt: str) -> list[dict[str, Any]]:
    import os  # noqa: PLC0415

    from production.cost.google_guardrail import (  # noqa: PLC0415
        guarded_generate_content,
        make_guarded_gemini_client,
    )

    client = make_guarded_gemini_client((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())
    response = guarded_generate_content(
        client, model=WRITER_MODEL, contents=prompt,
        config=_writer_config(),
        kind="text", source="animind_core.script.write",
    )
    return llm_providers._json_lines(response.text or "")


# ---------------------------------------------------------------- assembly


# Offline molds plus the title cards: 4 solo beats land near 20s, short of a 30s request.
_BEAT_S = 3.95
_CARD_S = 4.2
DURATION_TOLERANCE_S = 4.5


def duration_tolerance(target: float) -> float:
    """Seconds of slack around a requested duration. 30s allows 25.5–34.5."""
    return max(DURATION_TOLERANCE_S, 0.15 * float(target))


def runtime_seconds(lines: list[dict[str, Any]]) -> float:
    """Timeline the renderer will build: title card, speech, pauses, act gaps, end card."""
    from . import titles  # noqa: PLC0415

    speech = sum(max(0.8, words(str(line.get("text") or "")) / WORDS_PER_S) for line in lines)
    pauses = sum(float(line.get("pauseAfterS") or 0) for line in lines)
    gaps = 0.0
    for index in range(1, len(lines)):
        act = lines[index].get("act")
        if act and act != lines[index - 1].get("act"):
            gaps += titles.ACT_GAP_S
    return titles.TITLE_S + speech + pauses + gaps + titles.END_S


def measured_rate(text: str, seconds: float) -> float:
    """Words per second from one synthesized probe."""
    return max(0.4, words(text) / max(0.3, float(seconds)))


def _solo_count(duration: float, beat_s: float = _BEAT_S) -> int:
    target = max(10.0, min(180.0, float(duration)))
    beat_s = max(1.5, float(beat_s))
    best_n, best_err = 4, abs(_CARD_S + 4 * beat_s - target)
    for count in range(4, 17):
        err = abs(_CARD_S + count * beat_s - target)
        if err < best_err:
            best_n, best_err = count, err
    return best_n


def _trim_plan(plan: list[Beat], duration: float | None, words_per_s: float | None) -> list[Beat]:
    """Fit beat caps to the measured words-per-second budget, including a debate at 2.6 words/second.

    The historical debate caps sum well past a 30-second request, so the estimate landed at 34s.
    """
    if duration is None or not words_per_s or words_per_s <= 0:
        return plan
    budget = max(6.0, float(duration) - _CARD_S - 1.0)
    cap_words = word_budget(duration, words_per_s)
    plan = list(plan)
    floor = 5 if any(beat.act == "virada" or beat.name == "virada" for beat in plan) else 4

    def estimate(beats: list[Beat]) -> float:
        spoken = sum(beat.max_words for beat in beats) / words_per_s
        pauses = sum(beat.pause_s for beat in beats)
        acts = [beat.act for beat in beats if beat.act]
        gaps = 0.7 * sum(1 for i in range(1, len(acts)) if acts[i] != acts[i - 1])
        return spoken + pauses + gaps

    while len(plan) > floor and estimate(plan) > budget + 2:
        middle = [i for i in range(1, len(plan) - 1) if plan[i].kind != "quote"]
        if not middle:
            break
        del plan[middle[len(middle) // 2]]
    if cap_words and sum(beat.max_words for beat in plan) > cap_words:
        scale = cap_words / sum(beat.max_words for beat in plan)
        plan = [replace(beat, max_words=max(6, int(beat.max_words * scale))) for beat in plan]
        while sum(beat.max_words for beat in plan) > cap_words:
            index = max(range(len(plan)), key=lambda i: plan[i].max_words)
            if plan[index].max_words <= 6:
                break
            plan[index] = replace(plan[index], max_words=plan[index].max_words - 1)
    return plan


def plan_for(template: str, n_chars: int, duration: int | float | None = None,
             words_per_s: float | None = None) -> list[Beat]:
    if template == "debate" and n_chars < 2:
        template = "solo"
    if template == "dialogue":
        n_chars = max(1, n_chars)
        count = max(n_chars, min(12, round(float(duration or 45) / 6)))
        return [
            Beat("gancho" if i == 0 else ("fechamento" if i == count - 1 else "réplica"),
                 i % n_chars, "paraphrase", 14 if i in (0, count - 1) else 12,
                 0.35 if i < count - 1 else 0.0)
            for i in range(count)
        ]
    plan = list(PLANS.get(template, PLANS["solo"]))
    if template == "solo" and duration is not None:
        beat_s = _BEAT_S if not words_per_s or words_per_s >= WORDS_PER_S else max(1.5, 10 / words_per_s)
        extra = _solo_count(float(duration), beat_s) - len(plan)
        if extra > 0:
            plan = [*plan[:-1], *[Beat("argumento", 0, "paraphrase", 16, 0.28, 2) for _ in range(extra)], plan[-1]]
    if template == "debate" and duration is not None:
        if float(duration) <= 35.0:
            # Keep the complete four-act arc but drop two confrontation
            # exchanges. Film cards add about six seconds, so five voiced
            # beats land a requested 30-second debate near its target.
            del plan[2:4]
            del plan[3:5]
        # A 60-second debate needs more than the compact nine-beat default.
        # Insert complete A/B exchanges so strict alternation and the central
        # quote turn remain intact. Short requests keep the original plan.
        pairs = min(2, max(0, round((float(duration) - 45.0) / 7.5)))
        exchange = [
            Beat("réplica", speaker, "paraphrase", 14, 0.25, 3, act="confronto")
            for _ in range(pairs)
            for speaker in (0, 1)
        ]
        plan[4:4] = exchange
    return _trim_plan(plan, None if duration is None else float(duration), words_per_s)


def _universal_write(
    template: str,
    topic: str,
    names: list[str],
    lang: str,
    tone: str,
    objective: str,
    duration: int | float | None,
) -> dict[str, Any]:
    """No-fallback writer for an Edge locale without a localized bank.

    It deliberately adds no words in English or Portuguese: the caller's
    localized topic is the complete lexical content, varied only by universal
    punctuation. This is modest copy, but guarantees that TTS and captions stay
    in the selected language without a translation API or Gemini call.
    """
    template = template if template in (*PLANS, "dialogue") else "solo"
    n = len(names) if template == "dialogue" else (2 if template == "debate" and len(names) >= 2 else 1)
    n = max(1, n)
    plan = plan_for(template, n, duration)
    base = " ".join((topic or "AI").split()).strip(" .?!…") or "AI"
    marks = ("?", ".", "…", "!", ".", "?", "!", "…")
    lines = []
    for i, beat in enumerate(plan):
        text = tidy(base + marks[i % len(marks)])
        lines.append({
            "speaker": min(beat.speaker, n - 1),
            "beat": beat.name,
            "maxS": max_seconds(max(beat.max_words, words(text)), beat.rate),
            "pauseAfterS": beat.pause_s,
            "rateAdj": beat.rate,
            "emotion": BEAT_EMOTION.get(beat.name, "neutro"),
            "kind": "paraphrase",
            "text": text,
            **({"replyTo": {"line": i - 1, "idea": base}} if i else {}),
        })
    return {
        "lines": lines,
        "writer": "template-universal",
        "scriptCost": {
            "provider": None, "model": None, "costUsd": 0.0,
            "inputTokens": 0, "outputTokens": 0, "cached": False, "status": "offline",
        },
        "personas": [],
        "topic": topic or "AI",
        "language": lang,
        "tone": tone,
        "objective": objective,
        "targetDurationS": float(duration) if duration is not None else None,
        "quoteLines": 0,
        "scriptQA": {"pass": True, "problems": [], "notes": ["localized bank unavailable; topic-only universal template"]},
    }


_GERMAN_WORD = re.compile(
    r"\b(?:und|der|die|das|nicht|ist|ein|eine|einer|einem|einen|ich|mich|mit|von|vom|zu|zum|zur|"
    r"den|dem|des|sich|auch|auf|für|wird|sind|aber|oder|wenn|keine|kein|dass|macht|haben|hat|"
    r"sein|seine|wird)\b",
    re.I,
)
_GERMAN_MARK = re.compile(r"[äöüÄÖÜß]")


def foreign_quote(text: str, lang: str) -> bool:
    """A sheet quote that must not be spoken as-is in ``lang`` (German source text)."""
    if (lang or "").lower().startswith("de"):
        return False
    raw = text or ""
    if _GERMAN_MARK.search(raw):
        return True
    return len(_GERMAN_WORD.findall(raw)) >= 2


def counts_as_sheet_quote(line: dict[str, Any]) -> bool:
    """A spoken sheet quote, or a translation whose original stays on ``basis``."""
    if line.get("kind") == "quote":
        return True
    basis = line.get("basis") or {}
    return bool(basis.get("translated") and basis.get("id"))


def sheet_quote(sheet: dict[str, Any], name: str, raw_id: Any) -> dict[str, Any] | None:
    """The quote ``raw_id`` names on this speaker's sheet.

    Sheets number quotes ``q1``, ``q2``, … independently. A bare id is read on
    ``name``'s sheet. An id prefixed with another speaker is not his quote.
    """
    token = str(raw_id or "").strip()
    if not token or token.lower() == "null":
        return None
    by_id = {str(q.get("id")): q for q in _quotable(sheet)}
    if ":" in token:
        owner, local = token.split(":", 1)
        if personas.fold(owner) != personas.fold(name):
            return None
        token = local
    return by_id.get(token)


def other_speaker_quote_id(raw_id: Any, name: str) -> bool:
    """True when ``raw_id`` is explicitly namespaced to someone other than ``name``."""
    token = str(raw_id or "").strip()
    if ":" not in token:
        return False
    owner, _local = token.split(":", 1)
    folded = personas.fold(owner)
    return bool(folded) and folded != personas.fold(name)


def owns_sheet_quote(line: dict[str, Any], sheet: dict[str, Any], name: str) -> bool:
    """The line speaks a quote that is on this speaker's sheet, not a shared id from someone else."""
    if line.get("quoteId") and sheet_quote(sheet, name, line.get("quoteId")) is not None:
        return True
    if line.get("kind") == "quote":
        quoted = line.get("quote") or {}
        own = sheet_quote(sheet, name, quoted.get("id"))
        return bool(own and own.get("text") == quoted.get("text") == line.get("text"))
    basis = line.get("basis") or {}
    return bool(basis.get("translated") and sheet_quote(sheet, name, basis.get("id")) is not None)


def _basis_of(quote: dict[str, Any], *, translated: bool = False) -> dict[str, Any]:
    out = {"id": quote["id"], "source": quote.get("source", ""),
           "label": source_label(quote.get("source", "")), "url": quote.get("url", "")}
    if translated:
        out["text"] = quote.get("text", "")
        out["translated"] = True
    return out


def _ask_writer(prompt: str, *, allow_unavailable_gemini: bool, kind: str = "writer",
                attempt: int = 0) -> list[dict[str, Any]]:
    """One more writer call. Every attempt is its own telemetry row, including a miss."""
    started_call = perf_counter()
    result, failures = llm_providers.generate(
        prompt,
        settings.data_dir() / "cache" / "script",
        _gemini,
        allow_unavailable_gemini=allow_unavailable_gemini,
        version=SCRIPT_QA_VERSION,
    )
    telemetry.record(
        "script-repair",
        provider=result.provider if result is not None else None,
        model=result.model if result is not None else None,
        input_tokens=result.input_tokens if result is not None else 0,
        output_tokens=result.output_tokens if result is not None else 0,
        cost_usd=result.cost_usd if result is not None else 0.0,
        latency_ms=(perf_counter() - started_call) * 1000,
        status="ok" if result is not None else "empty",
        metadata={
            "kind": kind,
            "attempt": attempt,
            "cached": bool(result.cached) if result is not None else False,
            "failures": failures[:3],
        },
    )
    return list(result.lines) if result is not None else []


def _draft_index(entry: dict[str, Any]) -> int | None:
    raw = entry.get("i")
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _take_reply(entries: list[dict[str, Any]], index: int, lines: list[dict[str, Any]], topic: str) -> str:
    """The replacement sentence. One returned line is used even when its index is 0."""
    dicts = [entry for entry in entries if isinstance(entry, dict)]
    only = len(dicts) == 1
    current = lines[index]["text"] if 0 <= index < len(lines) else ""
    for entry in dicts:
        numbered = _draft_index(entry)
        if numbered is not None and numbered != index and not only:
            continue
        text = tidy(_unquoted(entry.get("text") or ""))
        if not text or personas.fold(text) == personas.fold(current):
            continue
        if words(text) > 16 or _names_a_later_quote(text, lines, index, topic):
            continue
        return text
    return ""


_UNANSWERED = re.compile(r"line (\d+): does not answer line (\d+)")


QA_REWRITES = 3
_TOO_LONG = re.compile(r"line (\d+): (\d+) words is too long")
_NO_QUOTE = re.compile(r"speaker (\d+): no real quote")


class ScriptQAError(RuntimeError):
    """Debate or dialogue QA still failed after the rewrite loop."""


def require_passing_script(out: dict[str, Any], template: str) -> None:
    """An LLM debate that still fails QA does not render. The problems are the error."""
    if template not in {"debate", "dialogue"}:
        return
    if out.get("writer") not in {"deepseek", "gemini"}:
        return
    qa = out.get("scriptQA") or {}
    if qa.get("pass"):
        return
    problems = qa.get("problems") or ["script QA failed"]
    raise ScriptQAError("script QA failed after rewrites: " + "; ".join(problems))


def structural_problems(lines: list[dict[str, Any]], topic: str, names: list[str],
                        sheets: list[dict[str, Any]], budget: int | None) -> list[str]:
    """Word-count and shape checks on the draft, before the full debate QA."""
    if not lines:
        return ["script: no lines"]
    problems: list[str] = []
    hook = str(lines[0].get("text") or "")
    if words(hook) > HOOK_MAX_WORDS:
        problems.append(f"line 0: hook is {words(hook)} words; maximum {HOOK_MAX_WORDS}")
    if "?" not in hook:
        problems.append("line 0: hook must be a question naming the topic")
    elif topic and not link(topic, hook, names=names):
        problems.append("line 0: hook does not name the topic")
    for index, line in enumerate(lines):
        limit = QUOTE_MAX_WORDS if line.get("kind") == "quote" else 16
        count = words(str(line.get("text") or ""))
        if line.get("kind") == "quote" and not str(line.get("text") or "").strip() and line.get("quoteId"):
            continue
        if count > limit:
            problems.append(f"line {index}: {count} words is too long; maximum {limit}")
    spoken = [words(text) for line in lines if (text := str(line.get("text") or "").strip())]
    if spoken and min(spoken) > 9:
        problems.append("script: no line of at most 9 words")
    close = str(lines[-1].get("text") or "")
    if lines[-1].get("kind") != "quote" and close.strip() and words(close) > 12:
        problems.append(f"line {len(lines) - 1}: close is {words(close)} words; maximum 12")
    for speaker, sheet in enumerate(list(sheets)[:2]):
        if not _eligible(sheet, QUOTE_MAX_WORDS):
            continue
        name = names[speaker] if speaker < len(names) else ""
        has = any(
            line.get("speaker") == speaker and owns_sheet_quote(line, sheet, name)
            for line in lines
        )
        if not has:
            problems.append(f"speaker {speaker}: no real quote of his own sheet")
    if budget is not None:
        total = sum(words(str(line.get("text") or "")) for line in lines)
        if total > budget:
            problems.append(f"script: {total} words exceeds the budget of {budget}")
    return problems


def _draft_rows(drafted: dict[int, dict[str, Any]], plan: list[Beat], names: list[str],
                sheets: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = []
    for index, beat in enumerate(plan):
        entry = drafted.get(index) or {}
        sp = min(beat.speaker, max(0, len(names) - 1)) if names else beat.speaker
        sheet = sheets[sp] if sheets and sp < len(sheets) else {}
        owned = sheet_quote(sheet, names[sp] if names else "", entry.get("quoteId")) if entry.get("quoteId") else None
        # A quote beat with no id on this speaker's sheet is a written line.
        kind = "quote" if beat.kind == "quote" and owned else (
            "paraphrase" if beat.kind == "quote" else beat.kind
        )
        rows.append({
            "speaker": min(beat.speaker, max(0, len(names) - 1)) if names else beat.speaker,
            "beat": beat.name,
            "kind": kind,
            "text": tidy(_unquoted(entry.get("text") or "")),
            "quoteId": entry.get("quoteId"),
        })
    return rows


def _correct_draft_shape(drafted: dict[int, dict[str, Any]], plan: list[Beat], topic: str, names: list[str],
                         sheets: list[dict[str, Any]], budget: int | None, lang: str, *,
                         allow_unavailable_gemini: bool) -> None:
    """One structural pass on the JSON. A longer rewrite is ignored."""
    rows = _draft_rows(drafted, plan, names, sheets)
    problems = structural_problems(rows, topic, names, sheets, budget)
    if not problems:
        return
    targets: list[tuple[int, int, str]] = []
    for problem in problems:
        match = re.match(r"line (\d+):", problem)
        if match:
            index = int(match.group(1))
            cap = 14 if "hook" in problem else 12 if "close" in problem else 16
            targets.append((index, cap, problem))
        elif problem.startswith("script: no line"):
            filled = [
                i for i, row in enumerate(rows)
                if row.get("kind") != "quote" and str(row.get("text") or "").strip()
            ]
            if filled:
                index = max(filled, key=lambda i: words(rows[i].get("text") or ""))
                targets.append((index, 9, problem))
        elif budget is not None and problem.startswith("script:") and "budget" in problem:
            filled = [
                i for i, row in enumerate(rows)
                if row.get("kind") != "quote" and str(row.get("text") or "").strip()
            ]
            if not filled:
                continue
            index = max(filled, key=lambda i: words(rows[i].get("text") or ""))
            total = sum(words(str(row.get("text") or "")) for row in rows)
            overflow = max(1, total - budget)
            cap = max(4, words(rows[index].get("text") or "") - overflow)
            targets.append((index, cap, problem))
    # One line can fail the hook, the close, and the short-line rule. Keep the tightest cap.
    chosen: dict[int, tuple[int, str]] = {}
    for index, cap, problem in targets:
        if not 0 <= index < len(rows):
            continue
        prev = chosen.get(index)
        if prev is None or cap < prev[0]:
            chosen[index] = (cap, problem)
    for index, (cap, problem) in chosen.items():
        if rows[index].get("kind") == "quote":
            continue
        current = rows[index]["text"]
        if not current:
            continue
        previous = rows[index - 1]["text"] if index else ""
        following = rows[index + 1]["text"] if index + 1 < len(rows) else ""
        rewritten = _rewrite_line(
            index, problem, current, lang, topic, names,
            allow_unavailable_gemini=allow_unavailable_gemini,
            max_words=cap, previous=previous, following=following,
        )
        if not rewritten:
            continue
        entry = drafted.setdefault(index, {"i": index})
        entry["text"] = rewritten
        entry["words"] = words(rewritten)
        rows[index]["text"] = rewritten


def _replan_request(base_prompt: str, displaced: list[tuple[int, str, str]], lines: list[dict[str, Any]]) -> str:
    """One fresh debate from the same plan. Patching a line that named the old quote kept saying 'pipe'."""
    discarded = [old for _i, old, new in displaced if personas.fold(old) != personas.fold(new)]
    kept = [
        f'{line["quote"]["id"]}: {line["text"]}'
        for line in lines if line.get("kind") == "quote" and line.get("quote")
    ]
    dropped = "\n".join(f"- {text}" for text in discarded) or "- (none)"
    spoken = "\n".join(f"- {text}" for text in kept) or "- (none)"
    return (
        base_prompt
        + "\n\nThe previous draft referred to quotes that are not spoken. "
        "Write every beat again from the plan. Do not mention a discarded quote.\n"
        f"Discarded quotes:\n{dropped}\n"
        f"Quotes that will be spoken:\n{spoken}\n"
    )


def _name_marks(name: str, *, first: bool = False) -> list[str]:
    """Full name and surname. The first name counts only when the line addresses its own speaker."""
    full = personas.persona_name(name)
    marks = []
    if full:
        marks.append(personas.fold(full))
        bits = full.split()
        if len(bits) >= 2 and len(bits[-1]) >= 4:
            marks.append(personas.fold(bits[-1]))
        if first and len(bits) >= 2 and len(bits[0]) >= 4:
            marks.append(personas.fold(bits[0]))
    return [mark for mark in marks if mark]


def mentions_speaker(text: str, name: str, *, first: bool = False) -> bool:
    """True when ``text`` says ``name`` (or that person's surname) as a word.

    ``first`` also matches the given name. The opener uses the strict form, so a
    first name alone does not count as addressing the other speaker.
    """
    folded = personas.fold(text)
    padded = f" {folded} "
    return any(f" {mark} " in padded for mark in _name_marks(name, first=first))


def _order_failure_request(base_prompt: str, lines: list[dict[str, Any]], problems: list[str], attempt: int) -> str:
    """A new draft. The failure names the reply that spoke a quote too early."""
    detail = "\n".join(f"- {problem}" for problem in problems) or "- a reply names a later quote"
    spoken = "\n".join(f"- line {i}: {line.get('text')}" for i, line in enumerate(lines))
    return (
        base_prompt
        + f"\n\nThe draft failed QA. Attempt {attempt} of 2. Write every beat again from the plan.\n"
        "A reply named a quote that is spoken only on a later line. "
        "Do not mention that quote before the line that speaks it. "
        "Either speak the quote first, or write the reply using only what was already said.\n"
        f"Failures:\n{detail}\n"
        f"Rejected draft:\n{spoken}\n"
    )


def _quote_replan_request(base_prompt: str, lines: list[dict[str, Any]], problems: list[str]) -> str:
    """One fresh debate. The quote that failed to answer stays a quote; the neighbour is not patched."""
    missed = [problem for problem in problems if _UNANSWERED.match(problem)]
    detail = "\n".join(f"- {problem}" for problem in missed) or "- a quote does not answer the line before it"
    spoken = "\n".join(
        f'- line {i} ({line.get("kind")}): {line.get("text")}' for i, line in enumerate(lines)
    )
    return (
        base_prompt
        + "\n\nA spoken quote does not answer the line before it. "
        "Write every beat again from the plan. On a quote beat, pick a quoteId from that speaker's "
        "own list that answers the previous line. Do not keep a quote that ignores the line before it.\n"
        f"Failures:\n{detail}\n"
        f"Previous draft:\n{spoken}\n"
    )


def _score_reply_quotes(index: int, previous: str, candidates: list[dict[str, Any]], name: str, lang: str,
                        topic: str, *, allow_unavailable_gemini: bool) -> dict[str, int]:
    """Ask which of this speaker's quotes answers the previous line. Scores only; the text is not rewritten."""
    if not candidates:
        return {}
    listing = "\n".join(f'- {personas.slug(name)}:{q["id"]}: {q["text"]}' for q in candidates)
    prompt = (
        f"Line {index} is a fixed quote and it does not answer the previous line. "
        "Do not rewrite the previous line or any other line. "
        "Score each candidate quote from this speaker's own sheet for how well it answers the previous line. "
        "A higher score is a better answer.\n"
        f"Speaker: {name}. Topic: {topic}. Language: {lang}.\n"
        f"Previous line: {previous}\n"
        f"Candidates:\n{listing}\n"
        'Return JSON only: {"lines": [{"i": 0, "quoteId": "...", "score": 0}]}.'
    )
    scores: dict[str, int] = {}
    for entry in _ask_writer(prompt, allow_unavailable_gemini=allow_unavailable_gemini):
        if not isinstance(entry, dict):
            continue
        found = sheet_quote({"quotes": candidates}, name, entry.get("quoteId"))
        if found is None:
            continue
        try:
            scores[str(found["id"])] = int(entry.get("score") or 0)
        except (TypeError, ValueError):
            scores[str(found["id"])] = 0
    return scores


def _swap_answering_quote(lines: list[dict[str, Any]], index: int, sheets: list[dict[str, Any]], names: list[str],
                          topic: str, lang: str, *, allow_unavailable_gemini: bool) -> bool:
    """Replace a quote that ignores the previous line with another quote from the same sheet that answers it."""
    line = lines[index]
    speaker = int(line.get("speaker") or 0)
    if speaker >= len(sheets):
        return False
    name = names[speaker] if speaker < len(names) else ""
    previous = lines[index - 1]["text"] if index else ""
    current_id = str((line.get("quote") or {}).get("id") or "")
    used = {
        str((other.get("quote") or {}).get("id") or "")
        for other in lines
        if other is not line and other.get("kind") == "quote" and other.get("speaker") == speaker
    }
    candidates = [
        quote for quote in _eligible(sheets[speaker], QUOTE_MAX_WORDS)
        if quote["id"] != current_id and quote["id"] not in used
    ]
    linking = [quote for quote in candidates if link(previous, quote["text"], quote=True, names=names)]
    if not linking:
        return False
    scores = _score_reply_quotes(
        index, previous, candidates, name, lang, topic, allow_unavailable_gemini=allow_unavailable_gemini,
    )
    best = max(linking, key=lambda quote: (scores.get(str(quote["id"]), 0), chain_score(quote, topic, previous)))
    old_text = str(line.get("text") or "")
    line["kind"] = "quote"
    line["text"] = best["text"]
    line["quote"] = cite(best)
    line.pop("basis", None)
    line["maxS"] = max(float(line.get("maxS") or 0), max_seconds(words(best["text"])))
    line["replyTo"] = {"line": index - 1, "idea": link(previous, best["text"], quote=True, names=names)}
    _regenerate_lines_that_named(lines, index, old_text, best["text"], lang, topic, names,
                                 allow_unavailable_gemini=allow_unavailable_gemini)
    return True


def _regenerate_lines_that_named(lines: list[dict[str, Any]], index: int, old_text: str, new_text: str, lang: str,
                                 topic: str, names: list[str], *, allow_unavailable_gemini: bool) -> None:
    """A later line that used the discarded quote has to be written again against the quote that replaced it."""
    old_stems = set(_stems(old_text))
    if not old_stems:
        return
    for later in range(index + 1, len(lines)):
        line = lines[later]
        if line.get("kind") == "quote":
            continue
        if not (set(_stems(str(line.get("text") or ""))) & old_stems):
            continue
        previous = new_text if later == index + 1 else lines[later - 1]["text"]
        following = lines[later + 1]["text"] if later + 1 < len(lines) else ""
        rewritten = _rewrite_line(
            later,
            "the quote this line refers to was replaced. "
            f"Discarded quote: {old_text}. New quote: {new_text}. "
            "Answer the new quote and do not mention the discarded one.",
            line["text"], lang, topic, names, allow_unavailable_gemini=allow_unavailable_gemini,
            max_words=16, previous=previous, following=following,
        )
        if not rewritten:
            continue
        line["text"] = rewritten
        line["replyTo"] = {"line": later - 1, "idea": link(previous, rewritten, names=names)}


def judge_debate(lines: list[dict[str, Any]], names: list[str], topic: str, lang: str, *,
                 allow_unavailable_gemini: bool) -> dict[str, Any] | None:
    """One cheap call. Keyword overlap is not this judgment.

    The model returns, per line, whether it answers the previous line, plus one coherent flag for the script.
    """
    if len(lines) < 2:
        return {"coherent": True, "lines": []}
    body = "\n".join(
        f'{index}. {(names[line["speaker"]] if line.get("speaker", 0) < len(names) else "speaker")}: {line.get("text")}'
        for index, line in enumerate(lines)
    )
    prompt = (
        "Judge this debate script. Do not rewrite it.\n"
        f"Topic: {topic}. Language: {lang}.\n"
        f"{body}\n"
        "For each line after the first, set answers_previous true only when it responds to the line before it. "
        "A line may refer only to content already said. Naming a quote, a pipe, or any concrete thing that "
        "is spoken only on a later line is not an answer, and coherent must be false.\n"
        "Set coherent true only when the script holds together as one argument and no reply precedes its referent.\n"
        'Return JSON only: {"lines": [{"i": 1, "answers_previous": true, "reason": "...", "coherent": true}]}.'
    )
    result, _failures = llm_providers.generate(
        prompt,
        settings.data_dir() / "cache" / "script",
        _gemini,
        allow_unavailable_gemini=allow_unavailable_gemini,
        version=SCRIPT_QA_VERSION,
    )
    if result is not None:
        telemetry.record(
            "script-judge",
            provider=result.provider,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            status="ok",
            metadata={"lines": len(lines), "topic": topic, "cached": bool(result.cached)},
        )
    entries = [entry for entry in (result.lines if result is not None else []) if isinstance(entry, dict)]
    if not entries:
        return None
    if not any("coherent" in entry or "answers_previous" in entry for entry in entries):
        return None
    flags = [bool(entry["coherent"]) for entry in entries if "coherent" in entry]
    per = [
        entry for entry in entries
        if isinstance(entry.get("i"), int) and entry["i"] >= 1 and "answers_previous" in entry
    ]
    if flags:
        coherent = all(flags)
    else:
        coherent = bool(per) and all(bool(entry.get("answers_previous")) for entry in per)
    return {"coherent": coherent, "lines": per}


def _prior_block(lines: list[dict[str, Any]], index: int) -> str:
    return "\n".join(f"{i}. {lines[i].get('text')}" for i in range(index)) or "(none)"


def _own_sheet_block(sheet: dict[str, Any] | None, name: str) -> str:
    if not sheet:
        return "(none)"
    rows = [f"{personas.slug(name)}:{quote['id']}: {quote['text']}" for quote in _quotable(sheet)[:12]]
    return "\n".join(rows) or "(none)"


def _apply_reply(lines: list[dict[str, Any]], index: int, text: str, names: list[str]) -> None:
    previous = lines[index - 1]["text"] if index else ""
    lines[index]["text"] = text
    lines[index]["kind"] = "paraphrase"
    lines[index].pop("quote", None)
    lines[index]["replyTo"] = {"line": index - 1, "idea": link(previous, text, names=names)}


def _rewrite_from_prior(lines: list[dict[str, Any]], index: int, lang: str, topic: str, names: list[str],
                        sheets: list[dict[str, Any]] | None, *, allow_unavailable_gemini: bool,
                        attempt: int) -> bool:
    """One reply, using only earlier lines and this speaker's sheet. The future quote is not shown."""
    speaker = int(lines[index].get("speaker") or 0)
    name = names[speaker] if speaker < len(names) else "the speaker"
    sheet = sheets[speaker] if sheets and speaker < len(sheets) else None
    prompt = (
        f"Rewrite only line {index} for {name}. Attempt {attempt} of 2.\n"
        "You may use only the prior lines and this speaker's own sheet. "
        "Do not mention anything that has not already been said. Do not reorder lines.\n"
        f"Prior lines:\n{_prior_block(lines, index)}\n"
        f"This speaker's quotes:\n{_own_sheet_block(sheet, name)}\n"
        'Return JSON only: {"lines": [{"i": %d, "text": "...", "words": 0}]}.' % index
    )
    text = _take_reply(
        _ask_writer(
            prompt, allow_unavailable_gemini=allow_unavailable_gemini, kind="rewrite", attempt=attempt,
        ),
        index, lines, topic,
    )
    if not text:
        return False
    _apply_reply(lines, index, text, names)
    return True


def _swap_reply_before_quote(lines: list[dict[str, Any]], index: int, later: int) -> bool:
    """Put the quote first when that still alternates speakers. Otherwise leave the order alone."""
    if later != index + 1 or not 0 <= index < len(lines) or later >= len(lines):
        return False
    speakers = [int(line.get("speaker") or 0) for line in lines]
    speakers[index], speakers[later] = speakers[later], speakers[index]
    if any(speakers[i] == speakers[i - 1] for i in range(1, len(speakers))):
        return False
    lines[index], lines[later] = lines[later], lines[index]
    return True


def _regenerate_beat(lines: list[dict[str, Any]], index: int, lang: str, topic: str, names: list[str],
                     sheets: list[dict[str, Any]] | None, *, allow_unavailable_gemini: bool) -> bool:
    speaker = int(lines[index].get("speaker") or 0)
    name = names[speaker] if speaker < len(names) else "the speaker"
    sheet = sheets[speaker] if sheets and speaker < len(sheets) else None
    prompt = (
        f"Write beat {index} again for {name}. Ignore the current line. "
        "Use only the prior lines and this speaker's own sheet. One sentence of at most 16 words.\n"
        f"Prior lines:\n{_prior_block(lines, index)}\n"
        f"This speaker's quotes:\n{_own_sheet_block(sheet, name)}\n"
        'Return JSON only: {"lines": [{"i": %d, "text": "...", "words": 0}]}.' % index
    )
    text = _take_reply(
        _ask_writer(
            prompt, allow_unavailable_gemini=allow_unavailable_gemini, kind="regenerate", attempt=1,
        ),
        index, lines, topic,
    )
    if not text:
        return False
    _apply_reply(lines, index, text, names)
    return True


def _resolve_forward_reply(lines: list[dict[str, Any]], index: int, later: int, lang: str, topic: str,
                           names: list[str], sheets: list[dict[str, Any]] | None, *,
                           allow_unavailable_gemini: bool) -> bool:
    """Two tries, then a fresh beat. Re-judge after each change. Do not leave the reply before its quote."""
    def clear() -> bool:
        return not any(item.startswith(f"line {index}:") for item in _unsaid_quote_refs(lines, topic))

    for attempt in (1, 2):
        moved = _rewrite_from_prior(
            lines, index, lang, topic, names, sheets, allow_unavailable_gemini=allow_unavailable_gemini,
            attempt=attempt,
        )
        if attempt == 2 and not moved:
            moved = _swap_reply_before_quote(lines, index, later)
        if not moved:
            continue
        judge_debate(lines, names, topic, lang, allow_unavailable_gemini=allow_unavailable_gemini)
        if clear():
            return True
    if _regenerate_beat(
        lines, index, lang, topic, names, sheets, allow_unavailable_gemini=allow_unavailable_gemini,
    ):
        judge_debate(lines, names, topic, lang, allow_unavailable_gemini=allow_unavailable_gemini)
    return clear()


def _repair_unanswered(lines: list[dict[str, Any]], problems: list[str], lang: str, topic: str, names: list[str], *,
                       allow_unavailable_gemini: bool, sheets: list[dict[str, Any]] | None = None) -> tuple[bool, bool]:
    """Rewrite a line whose own rule can be fixed. A quote that does not answer is swapped, not its neighbour."""
    changed = False
    needs_replan = False
    for problem in problems:
        if _NO_QUOTE.match(problem):
            continue
        later_ref = re.match(r"line (\d+): refers to a quote spoken later on line (\d+)", problem)
        if later_ref:
            index = int(later_ref.group(1))
            later = int(later_ref.group(2))
            if _resolve_forward_reply(
                lines, index, later, lang, topic, names, sheets,
                allow_unavailable_gemini=allow_unavailable_gemini,
            ):
                changed = True
            continue
        match = _UNANSWERED.match(problem)
        if not match:
            continue
        index = int(match.group(1))
        if not 0 <= index < len(lines):
            continue
        if lines[index].get("kind") == "quote":
            # The quote text is fixed. Scoring another quote from this sheet, or one replan, is the repair.
            if sheets and _swap_answering_quote(
                lines, index, sheets, names, topic, lang, allow_unavailable_gemini=allow_unavailable_gemini,
            ):
                changed = True
            else:
                needs_replan = True
            continue
        previous = lines[int(match.group(2))]["text"]
        nxt = lines[index + 1]["text"] if index + 1 < len(lines) else ""
        rewritten = _rewrite_line(
            index,
            "this line does not answer the previous line. Repeat one content word from the previous line.",
            lines[index]["text"], lang, topic, names, allow_unavailable_gemini=allow_unavailable_gemini,
            max_words=16, previous=previous, following=nxt,
        )
        if not rewritten or not link(previous, rewritten, names=names):
            continue
        if _names_a_later_quote(rewritten, lines, index, topic):
            continue
        lines[index]["text"] = rewritten
        lines[index]["replyTo"] = {"line": index - 1, "idea": link(previous, rewritten, names=names)}
        changed = True
    for problem in problems:
        long = _TOO_LONG.match(problem)
        if long:
            index = int(long.group(1))
            if not 0 <= index < len(lines) or lines[index].get("kind") == "quote":
                continue
            nxt = lines[index + 1]["text"] if index + 1 < len(lines) else ""
            prev = lines[index - 1]["text"] if index else ""
            rewritten = _rewrite_line(
                index,
                f"this line is {long.group(2)} words. Maximum 16 words.",
                lines[index]["text"], lang, topic, names, allow_unavailable_gemini=allow_unavailable_gemini,
                max_words=16, previous=prev, following=nxt,
            )
            if rewritten and words(rewritten) <= PARAPHRASE_MAX_WORDS + 1:
                lines[index]["text"] = rewritten
                changed = True
            continue
        if problem.startswith("debate: the close is not memorable") and lines:
            prev = lines[-2]["text"] if len(lines) > 1 else ""
            rewritten = _rewrite_line(
                len(lines) - 1,
                "the close is not memorable. Maximum 12 words, one punchy sentence.",
                lines[-1]["text"], lang, topic, names, allow_unavailable_gemini=allow_unavailable_gemini,
                max_words=12, previous=prev, following="",
            )
            if rewritten and words(rewritten) <= 12:
                lines[-1]["text"] = rewritten
                lines[-1]["kind"] = "paraphrase"
                lines[-1].pop("quote", None)
                changed = True
    return changed, needs_replan


def rewrite_for_duration(line: dict[str, Any], *, shorter: bool, words: int, topic: str = "",
                         names: list[str] | None = None, lang: str = "en-US") -> str:
    """Ask for a complete shorter or longer line. Never returns a chopped word."""
    current = str(line.get("text") or "")
    direction = "shorter" if shorter else "longer"
    cap = max(4, int(words))
    if not shorter:
        cap = max(cap, len(current.split()) + 1)
    fresh = _rewrite_line(
        int(line.get("i") or 0),
        f"the spoken cut is the wrong length. Write one {direction} full sentence of about {words} words. "
        "Do not stop in the middle of a word.",
        current, lang, topic, names or [], allow_unavailable_gemini=True,
        max_words=cap, allow_longer=not shorter,
    )
    if not fresh or fresh[-1:] not in ".?!…":
        return ""
    return fresh


def _rewrite_line(index: int, problem: str, current: str, lang: str, topic: str, names: list[str], *,
                  allow_unavailable_gemini: bool, max_words: int = 16, previous: str = "",
                  following: str = "", allow_longer: bool = False) -> str:
    """Ask the writer for one line that meets the failing rule. A longer reply is discarded."""
    language = "Brazilian Portuguese" if lang == "pt-BR" else "American English"
    who = ", ".join(names) or "the speaker"
    cap = max(4, int(max_words))
    prompt = (
        f"Rewrite only line {index} of a spoken script in {language} about {topic}. "
        f"Speakers: {who}.\n"
        f"Failing rule: {problem}\n"
        f"Maximum words: {cap}. A longer line is rejected.\n"
        f"Previous line: {previous or '(none)'}\n"
        f"Next line: {following or '(none)'}\n"
        f"Current line ({words(current)} words): {current}\n"
        "Return JSON only: {\"lines\": [{\"i\": %d, \"text\": \"...\", \"words\": 0}]}. "
        "One complete sentence, no quotation marks, no stage directions."
        % index
    )
    for entry in _ask_writer(prompt, allow_unavailable_gemini=allow_unavailable_gemini):
        if not isinstance(entry, dict):
            continue
        if entry.get("i") not in (index, None):
            continue
        text = tidy(_unquoted(entry.get("text") or ""))
        if not text or personas.fold(text) == personas.fold(current):
            continue
        if words(text) > cap:
            continue
        if current and not allow_longer and words(text) > words(current):
            continue
        return text
    return ""


def _spoken_translation(quote: dict[str, Any], draft: dict[str, Any], lang: str, topic: str, names: list[str], *,
                        allow_unavailable_gemini: bool, use_llm: bool) -> str:
    """Target-language speech for a foreign quote. The original stays on ``basis``."""
    stored = tidy(_clean_paraphrase(quote.get("translation") or "", 24))
    if stored and not foreign_quote(stored, lang):
        return stored
    draft_text = tidy(_clean_paraphrase(draft.get("text") or "", 24))
    if draft_text and not foreign_quote(draft_text, lang) and personas.fold(draft_text) != personas.fold(quote["text"]):
        return draft_text
    if not use_llm:
        return ""
    language = "Brazilian Portuguese" if lang == "pt-BR" else "American English"
    prompt = (
        f"Translate this quote into {language} for speech about {topic}. "
        f"Speakers: {', '.join(names) or 'the speaker'}. "
        "Keep the meaning, one sentence, no quotation marks.\n"
        f"Quote: {quote.get('text')}\n"
        "Return JSON only: {\"lines\": [{\"i\": 0, \"text\": \"...\"}]}."
    )
    for entry in _ask_writer(prompt, allow_unavailable_gemini=allow_unavailable_gemini):
        if not isinstance(entry, dict):
            continue
        text = tidy(_clean_paraphrase(entry.get("text") or "", 24))
        if text and not foreign_quote(text, lang):
            return text
    return ""


def _repair_reason(text: str, sheet: dict[str, Any], passages: list[dict[str, Any]], taken: set[str]) -> str:
    """Why an LLM line cannot be spoken as written. A weak link is not a reason."""
    if not text:
        return ""
    if personas.fold(text) in taken:
        return "this line repeats an earlier line; write a different sentence"
    if _literal_claim(text, sheet):
        return "this line pretends to be a literal quote; say it as ordinary speech"
    if copies_quote(text, sheet):
        return "this line copies a real quote; paraphrase the idea in new words"
    if knowledge.copies_passage(text, passages):
        return "this line copies a research note; use your own words"
    return ""


def _keyword_streak(lines: list[dict[str, Any]]) -> tuple[int, str] | None:
    """Index of a written line that repeats one content word from the previous two lines."""
    stems = [_stems(str(line.get("text") or "")) for line in lines]
    for index in range(2, len(lines)):
        if lines[index].get("kind") == "quote":
            continue
        shared = set(stems[index]) & set(stems[index - 1]) & set(stems[index - 2])
        if not shared:
            continue
        stem = next(iter(shared))
        return index, stems[index][stem]
    return None


def _draft_from_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Writer rows for a script that already passed QA, so the next cache hit is that script."""
    stored: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        entry: dict[str, Any] = {"i": index, "words": words(str(line.get("text") or ""))}
        quote = line.get("quote") or {}
        if line.get("kind") == "quote" and quote.get("id"):
            entry["quoteId"] = quote["id"]
        else:
            entry["text"] = line.get("text") or ""
        stored.append(entry)
    return stored


def write(template: str, topic: str, names: list[str], lang: str = "en-US", *,
          sheets: list[dict[str, Any]] | None = None, use_llm: bool | None = None, mode: str = "quick",
          indexes: list[Any] | None = None, tone: str = "comedia", objective: str = "entreter",
          duration: int | float | None = None, words_per_s: float | None = None) -> dict[str, Any]:
    """Script lines (before TTS) plus the sheets used.

    ``use_llm`` None selects the configured provider when a DeepSeek or Gemini
    key is present. DeepSeek is tried first by default and Gemini is its
    fallback; ``ANIMIND_SCRIPT_PROVIDER`` can reverse that order.

    ``mode`` picks the knowledge index behind written lines (:mod:`knowledge`): ``quick`` (key terms,
    shallow) or ``deep`` (full texts). Retrieved passages only feed ideas; quotes stay verbatim from the sheet."""
    started = perf_counter()
    explicit_llm = use_llm is True
    lang = languages.profile(lang).script
    template = template if template in (*PLANS, "dialogue") else "solo"
    from . import styles  # noqa: PLC0415
    tone, objective = styles.pick_tone(tone), styles.pick_objective(objective)
    if lang not in languages.PROFILES:
        out = _universal_write(template, topic, names, lang, tone, objective, duration)
        telemetry.record(
            "script", latency_ms=(perf_counter() - started) * 1000,
            script_qa=out["scriptQA"], status="offline",
            metadata={"template": template, "language": lang, "test": bool(settings.force_mock())},
        )
        return out
    topic = (topic or "").strip() or ("inteligência artificial" if lang == "pt-BR" else "artificial intelligence")
    n = len(names) if template == "dialogue" else (2 if template == "debate" and len(names) >= 2 else 1)
    n = max(1, n)
    names = [personas.persona_name(names[min(i, len(names) - 1)]) if names else "" for i in range(n)]
    if sheets is None:
        sheets = [personas.load(nm, lang, fetch=settings.personas_online()) for nm in names]
    plan = plan_for(template, n, duration, words_per_s=words_per_s)
    mode = mode if mode in knowledge.MODES else "quick"
    if indexes is None:
        indexes = [knowledge.ensure(nm, lang, mode, sheet=sh) for nm, sh in zip(names, sheets)]
    passages = [knowledge.retrieve(ix, f"{topic} {nm}", k=4 if mode == "deep" else 2) for ix, nm in zip(indexes, names)]
    if use_llm is None:
        use_llm = settings.script_llm_available()

    base_prompt = ""
    drafted: dict[int, dict[str, Any]] = {}
    writer = "template"
    script_cost: dict[str, Any] = {
        "provider": None,
        "model": None,
        "costUsd": 0.0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cached": False,
        "status": "offline",
    }
    if use_llm:
        prompt = _prompt(
            plan, sheets, names, topic, lang, template, passages, tone, objective, duration, words_per_s,
        )
        base_prompt = prompt
        result, failures = llm_providers.generate(
            prompt,
            settings.data_dir() / "cache" / "script",
            _gemini,
            # Explicit use_llm=True remains injectable in unit tests and
            # preserves the former behavior of attempting Gemini without
            # consulting environment availability first.
            allow_unavailable_gemini=explicit_llm,
            version=SCRIPT_QA_VERSION,
        )
        if result is not None:
            for entry in result.lines:
                if isinstance(entry, dict) and isinstance(entry.get("i"), int):
                    drafted[entry["i"]] = entry
            writer = "deepseek" if result.provider == "deepseek" else "gemini"
            script_cost = result.cost()
        elif failures:
            _LOG.warning("script writer fell back to templates: %s", "; ".join(failures))
            script_cost["status"] = "fallback"

    debate = template == "debate" and n > 1
    seed = _seed(topic, names, lang)
    budget = word_budget(duration, words_per_s) if debate else None
    if writer in {"deepseek", "gemini"} and debate:
        _correct_draft_shape(
            drafted, plan, topic, names, sheets, budget, lang, allow_unavailable_gemini=explicit_llm,
        )
    draft_source = drafted
    lines: list[dict[str, Any]] = []
    displaced: list[tuple[int, str, str]] = []
    authored: set[int] = set()
    qa_quote_replan = False
    order_redrafts = 0
    while True:
        for _pass in (0, 1):
            used: set[tuple[int, str]] = set()
            lines = []
            authored = set()
            displaced = []
            molds: set[str] = set()
            for i, beat in enumerate(plan):
                sp = min(beat.speaker, n - 1)
                sheet = sheets[sp]
                by_id = {q["id"]: q for q in _quotable(sheet)}
                speaker_name = names[sp] if sp < len(names) else ""
                draft = draft_source.get(i) or {}
                line: dict[str, Any] = {"speaker": sp, "beat": beat.name, "maxS": max_seconds(beat.max_words, beat.rate),
                                        "pauseAfterS": beat.pause_s, "rateAdj": beat.rate, "emotion": emotion_of(draft, beat.name)}
                if beat.act:
                    line["act"] = beat.act
                previous = lines[-1]["text"] if lines else ""
                answers_prev = (lambda text, quote=False: link(previous, text, quote=quote, names=names)) if debate and lines else None
                # A line the model actually wrote is final. Template molds fill only beats it left empty.
                writer_line = writer in {"deepseek", "gemini"} and i in draft_source
                quote = None
                chosen_quote = None
                if beat.kind == "quote":
                    pool = [q for q in _eligible(sheet, beat.max_words) if (sp, q["id"]) not in used]
                    qid = draft.get("quoteId")
                    ranked = _rank_strong(pool, topic, previous) if beat.strong else _rank(pool, topic, previous)
                    if not writer_line and answers_prev:  # offline: a debate quote answers the line before
                        ranked = sorted(ranked, key=lambda q: not answers_prev(q["text"], True))
                    elif not writer_line and template == "lesson":
                        ranked = sorted(ranked, key=lambda q: not on_thread(q["text"], previous, topic))
                    named_quote = sheet_quote(sheet, speaker_name, qid) if qid else None
                    chosen_quote = named_quote if named_quote in pool else None
                    if writer_line and other_speaker_quote_id(qid, speaker_name):
                        quote = None  # the other speaker's q-number is not this speaker's quote
                    elif writer_line and chosen_quote is not None:
                        quote = chosen_quote  # the writer's quote is not re-picked later
                    else:
                        quote = chosen_quote
                        off_topic = bool(
                            quote is not None and ranked
                            and not (_tokens(topic) & _tokens(quote["text"]))
                            and topic_score(quote, topic) < topic_score(ranked[0], topic)
                        )
                        ignores = bool(
                            answers_prev and quote is not None and ranked
                            and not answers_prev(quote["text"], True) and answers_prev(ranked[0]["text"], True)
                        )
                        if quote is None or (not writer_line and (off_topic or ignores)):
                            quote = next(iter(ranked), None)  # unknown id, or an offline quote that ignores the line before
                    if quote is not None and beat.optional and not (writer_line and chosen_quote is not None):
                        if template == "lesson" and not on_thread(quote["text"], previous, topic):
                            quote = None  # an off-thread quote would be a non sequitur: the turn is written instead
                        elif answers_prev and not answers_prev(quote["text"], True):
                            quote = None  # no quote answers the line before -> a written line, not a non sequitur
                        elif not answers_prev and not qid and not answers(quote, topic, previous):
                            quote = None
                else:
                    # a written line that *is* one of the speaker's real quotes is shown as that quote, with its source
                    pool = [q for q in _quotable(sheet) if (sp, q["id"]) not in used]
                    quote = matching_quote(draft.get("text") or "", pool, beat.max_words + 6)
                translated_from: dict[str, Any] | None = None
                spoken_translation = ""
                if quote is not None and foreign_quote(quote["text"], lang):
                    # Speak the target language. The German (or other) source stays on basis.
                    translated_from = quote
                    spoken_translation = _spoken_translation(
                        quote, draft, lang, topic, names,
                        allow_unavailable_gemini=explicit_llm, use_llm=writer_line,
                    )
                    quote = None
                mismatched: dict[str, Any] | None = None
                if quote is not None and lines and template in {"solo", "dialogue"} and not (writer_line and chosen_quote is quote):
                    fits_line = bool(link(previous, quote["text"], quote=True, names=names))
                    fits_topic = topic_score(quote, topic) > 0 or focus(quote, topic) >= 0.3
                    # A real quote that answers neither the previous line nor the question
                    # becomes a paraphrase. The sheet source stays on ``basis``.
                    # Lessons and debates keep their quote beats; those shapes are checked apart.
                    if not fits_line and not fits_topic:
                        mismatched = quote
                        quote = None
                if quote is not None:
                    used.add((sp, quote["id"]))
                    line.update(kind="quote", text=quote["text"], quote=cite(quote))
                    line["maxS"] = max(line["maxS"], max_seconds(words(quote["text"]), beat.rate))
                else:
                    if beat.kind == "quote":  # a quote beat without a fitting quote gets a short written line
                        beat = replace(beat, max_words=min(beat.max_words, 16))
                    taken = {personas.fold(ln["text"]) for ln in lines}
                    # A model sentence stays whole. Slicing it to the beat cap cut
                    # "we can hold" off the debate line and spoke the stump.
                    raw_draft = draft.get("text") or ""
                    draft_text = tidy(_unquoted(raw_draft) if writer_line else _clean_paraphrase(raw_draft, beat.max_words))
                    locked = False
                    if spoken_translation:
                        text = tidy(_unquoted(spoken_translation))
                        drafted_text = True
                        locked = True
                        authored.add(i)
                    elif writer_line and draft_text:
                        text = draft_text
                        reason = _repair_reason(text, sheet, passages[sp], taken)
                        if reason:
                            rewritten = _rewrite_line(
                                i, reason, text, lang, topic, names, allow_unavailable_gemini=explicit_llm,
                                max_words=16, previous=previous, following="",
                            )
                            if rewritten:
                                text = rewritten
                        drafted_text = True
                        locked = True  # the model's line stays; a failed repair does not inject a mold
                        authored.add(i)
                    else:
                        text = draft_text
                        hook_off_topic = debate and not lines and not link(topic, text, names=names)
                        drafted_text = bool(text)
                        if (not text or personas.fold(text) in taken or copies_quote(text, sheet) or hook_off_topic
                                or _literal_claim(text, sheet)
                                or knowledge.copies_passage(text, passages[sp]) or (answers_prev and not answers_prev(text))):
                            drafted_text = False
                            nxt = plan[i + 1] if i + 1 < len(plan) else None
                            nsp = min(nxt.speaker, n - 1) if nxt else 0
                            upcoming = ([q["text"] for q in _rank([q for q in _eligible(sheets[nsp], nxt.max_words)
                                                                   if (nsp, q["id"]) not in used], topic, "")]
                                        if debate and nxt and nxt.kind == "quote" else None)
                            text = _template_text(beat, lang, topic, names, lines, n, taken, molds, seed, i, upcoming,
                                                  objective, tone)
                    line.update(kind="paraphrase", text=text)
                    if beat.name == "citação":  # a written line is never labelled as a quote
                        line["beat"] = "ideia"
                    basis = sheet_quote(sheet, speaker_name, draft.get("basisId")) if drafted_text else None
                    if translated_from is not None:
                        basis = translated_from
                    elif mismatched is not None:
                        basis = mismatched
                    if basis is not None:
                        line["basis"] = _basis_of(basis, translated=translated_from is not None)
                    if not locked and lines and not link(previous, line["text"], names=names):
                        taken = {personas.fold(ln["text"]) for ln in lines}
                        nxt = plan[i + 1] if i + 1 < len(plan) else None
                        nsp = min(nxt.speaker, n - 1) if nxt else 0
                        upcoming = ([q["text"] for q in _rank([q for q in _eligible(sheets[nsp], nxt.max_words)
                                                               if (nsp, q["id"]) not in used], topic, "")]
                                    if debate and nxt and nxt.kind == "quote" else None)
                        repaired = _template_text(beat, lang, topic, names, lines, n, taken, molds, seed, i, upcoming,
                                                  objective, tone)
                        if link(previous, repaired, names=names):
                            line["text"] = repaired
                if lines:
                    idea = link(previous, line["text"], quote=line["kind"] == "quote", names=names)
                    line["replyTo"] = {"line": len(lines) - 1, "idea": idea}
                if writer_line and beat.kind == "quote":
                    named = sheet_quote(sheet, speaker_name, draft.get("quoteId")) if draft.get("quoteId") else None
                    spoken = line["text"]
                    if named is not None and (line.get("kind") != "quote" or line.get("quote", {}).get("id") != named["id"] or spoken != named["text"]):
                        displaced.append((i, named["text"], spoken))
                lines.append(line)
            if not displaced or _pass == 1 or writer not in {"deepseek", "gemini"} or not debate:
                break
            fresh = _ask_writer(
                _replan_request(prompt, displaced, lines), allow_unavailable_gemini=explicit_llm,
            )
            nxt_source = {
                entry["i"]: entry for entry in fresh
                if isinstance(entry, dict) and isinstance(entry.get("i"), int)
            }
            if not nxt_source:
                break
            _correct_draft_shape(
                nxt_source, plan, topic, names, sheets, budget, lang,
                allow_unavailable_gemini=explicit_llm,
            )
            draft_source = nxt_source
        if writer in {"deepseek", "gemini"} and authored:
            stems = [_stems(str(line.get("text") or "")) for line in lines]
            for index in range(2, len(lines)):
                if index not in authored or lines[index].get("kind") == "quote":
                    continue
                shared = set(stems[index]) & set(stems[index - 1]) & set(stems[index - 2])
                if not shared:
                    continue
                word = stems[index][next(iter(shared))]
                nxt = lines[index + 1]["text"] if index + 1 < len(lines) else ""
                prev = lines[index - 1]["text"] if index else ""
                rewritten = _rewrite_line(
                    index,
                    f"the word {word!r} is already on the two lines before this one; use a different concrete word "
                    "and still repeat one content word from the previous line",
                    lines[index]["text"], lang, topic, names, allow_unavailable_gemini=explicit_llm,
                    max_words=min(16, words(lines[index]["text"])), previous=prev, following=nxt,
                )
                keeps_reply = link(prev, rewritten, names=names) if rewritten else ""
                if rewritten and personas.fold(word) not in personas.fold(rewritten) and (not debate or keeps_reply):
                    lines[index]["text"] = rewritten
                    lines[index]["replyTo"] = {
                        "line": index - 1,
                        "idea": link(lines[index - 1]["text"], rewritten, names=names),
                    }
                break
        if writer in {"deepseek", "gemini"}:
            def _problems_now() -> list[str]:
                found: list[str] = []
                if debate:
                    found += structural_problems(lines, topic, names, sheets, budget)
                found += check(lines, sheets, topic, names)
                if debate:
                    found += check_debate(lines, sheets, names, topic)
                elif template == "lesson":
                    found += check_lesson(lines, sheets, topic)
                unique: list[str] = []
                for item in found:
                    if item not in unique:
                        unique.append(item)
                return unique

            problems_now = _problems_now()
            if debate:
                judged = judge_debate(
                    lines, names, topic, lang, allow_unavailable_gemini=explicit_llm,
                )
                if judged and not judged["coherent"]:
                    for entry in judged["lines"]:
                        if entry.get("answers_previous"):
                            continue
                        index = int(entry["i"])
                        reason = str(entry.get("reason") or "judge")
                        problems_now.append(f"line {index}: does not answer line {index - 1} ({reason})")
            changed, needs_replan = (False, False)
            if problems_now:
                changed, needs_replan = _repair_unanswered(
                    lines, problems_now, lang, topic, names,
                    allow_unavailable_gemini=explicit_llm, sheets=sheets,
                )
            if needs_replan and debate and not qa_quote_replan and base_prompt:
                qa_quote_replan = True
                fresh = _ask_writer(
                    _quote_replan_request(base_prompt, lines, problems_now),
                    allow_unavailable_gemini=explicit_llm, kind="quote-replan", attempt=1,
                )
                nxt_source = {
                    _draft_index(entry): entry for entry in fresh
                    if isinstance(entry, dict) and _draft_index(entry) is not None
                }
                if nxt_source:
                    _correct_draft_shape(
                        nxt_source, plan, topic, names, sheets, budget, lang,
                        allow_unavailable_gemini=explicit_llm,
                    )
                    draft_source = nxt_source
                    continue
            forward = _unsaid_quote_refs(lines, topic) if debate else []
            if forward and order_redrafts < 2 and base_prompt:
                order_redrafts += 1
                fresh = _ask_writer(
                    _order_failure_request(base_prompt, lines, forward, order_redrafts),
                    allow_unavailable_gemini=explicit_llm, kind="redraft", attempt=order_redrafts,
                )
                nxt_source = {
                    _draft_index(entry): entry for entry in fresh
                    if isinstance(entry, dict) and _draft_index(entry) is not None
                }
                # One sentence is not a new debate. Keep the current draft and use the other attempt.
                if len(nxt_source) >= max(5, len(plan) // 2):
                    _correct_draft_shape(
                        nxt_source, plan, topic, names, sheets, budget, lang,
                        allow_unavailable_gemini=explicit_llm,
                    )
                    draft_source = nxt_source
                    continue
                if order_redrafts < 2:
                    continue
        break
    personas_out = [
        {"name": nm, "found": bool(s.get("found")), "wikiquote": s.get("wikiquote"), "wikipedia": s.get("wikipedia"),
         "quotes": len(s.get("quotes") or []), "primaryQuotes": len(_quotable(s)), "license": s.get("license"),
         "mode": ix[0].get("mode", mode), "chunks": len(ix[0].get("chunks") or []),
         "passages": [{"source": p["source"], "url": p["url"], "score": p["score"]} for p in ps]}
        for nm, s, ix, ps in zip(names, sheets, indexes, passages)
    ]
    out = {"lines": lines, "writer": writer, "scriptCost": script_cost, "personas": personas_out, "topic": topic, "tone": tone,
           "objective": objective, "targetDurationS": float(duration) if duration is not None else None,
           "quoteLines": sum(1 for ln in lines if ln["kind"] == "quote")}
    def _problems() -> list[str]:
        found: list[str] = []
        if debate and writer in {"deepseek", "gemini"}:
            found += structural_problems(lines, topic, names, sheets, budget)
        found += check(lines, sheets, topic, names)
        if debate:
            found += check_debate(lines, sheets, names, topic)
        elif template == "lesson":
            found += check_lesson(lines, sheets, topic)
        unique: list[str] = []
        for item in found:
            if item not in unique:
                unique.append(item)
        return unique

    problems = _problems()
    # notes: weaknesses worth a look that do not fail the script (an idea carried three lines in a row)
    notes = _repeated_idea(lines)
    if debate:
        notes += answer_notes(lines, names)
    if duration is not None:
        estimated = runtime_seconds(lines)
        out_duration = round(estimated, 2)
        tolerance = duration_tolerance(float(duration))
        if abs(estimated - float(duration)) > tolerance:
            notes.append(
                f"duration {estimated:.1f}s is outside {float(duration):.0f}s ± {tolerance:.1f}s"
            )
    else:
        out_duration = None
    out["scriptQA"] = {"pass": not problems, "problems": problems, "notes": notes}
    if out_duration is not None:
        out["estimatedDurationS"] = out_duration
    if problems:
        _LOG.warning("%s script QA: %s", template, "; ".join(problems))
    if writer in {"deepseek", "gemini"} and base_prompt:
        provider_id = str(script_cost.get("provider") or ("deepseek" if writer == "deepseek" else "google"))
        model = str(script_cost.get("model") or "")
        kept = _draft_from_lines(lines) if not problems else None
        llm_providers.stamp_script_cache(
            settings.data_dir() / "cache" / "script",
            provider_id, model, base_prompt,
            version=SCRIPT_QA_VERSION, passed=not problems, lines=kept,
        )
        _store_writer_cache(base_prompt, kept or [], passed=not problems)
    cost = out["scriptCost"]
    telemetry.record(
        "script",
        provider=cost.get("provider"),
        model=cost.get("model"),
        input_tokens=cost.get("inputTokens", 0),
        output_tokens=cost.get("outputTokens", 0),
        cost_usd=cost.get("costUsd", 0.0),
        latency_ms=(perf_counter() - started) * 1000,
        script_qa=out["scriptQA"],
        status=str(cost.get("status") or "ok"),
        metadata={
            "template": template, "language": lang, "writer": writer,
            "test": bool(settings.force_mock()), "cached": bool(cost.get("cached")),
        },
    )
    return out


SOURCE_URL = re.compile(r"^https://[a-z]{2,3}(?:\.m)?\.wiki(?:quote|pedia|source)\.org/wiki/\S+$")
# A written line that presents its words as the speaker's literal text ("como eu escrevi: ...", "nas palavras de
# Platão, ...", "palavra por palavra") claims a quote it is not: only ``kind: quote`` lines, copied from the sheet,
# may do that.
LITERAL_CLAIM = re.compile(
    r"\b(?:como (?:eu )?(?:disse|escrevi|escreveu|dizia|digo)\s*:|(?:eu )?escrevi\s*:|está escrito\s*:|"
    r"nas (?:minhas |suas |próprias )?palavras(?: de [A-ZÀ-Ý]\w+)?\s*[:,]|palavra por palavra|textualmente|ipsis litteris|"
    r"\bcito\s*:|citando\s*:|em minhas palavras\s*[:,]|"
    r"as I (?:wrote|said)\s*[:,]|I wrote\s*:|in (?:my|his|her) (?:own )?words\s*[:,]|word for word|to quote\b|I quote\b)",
    re.I)


def _literal_claim(text: str, sheet: dict[str, Any]) -> str:
    """The phrase with which a written line passes itself off as a literal quote, or ""."""
    m = LITERAL_CLAIM.search(text or "")
    if m:
        return m.group(0).strip()
    for q in sheet.get("quotes") or []:  # "Em Ecce Homo: ..." names the work as if reading from it
        work = str(q.get("work") or "").strip()
        if len(work.split()) >= 2 and re.search(re.escape(work) + r"\s*:", text or "", re.I):
            return f"{work}:"
    return ""


def closest_quote(text: str, sheet: dict[str, Any]) -> tuple[dict[str, Any] | None, float]:
    """The sheet quote whose content words ``text`` covers most, and that share (0..1)."""
    toks = _tokens(text)
    best, share = None, 0.0
    for q in sheet.get("quotes") or []:
        qt = _tokens(q["text"])
        sc = len(toks & qt) / max(1, len(qt))
        if sc > share:
            best, share = q, sc
    return best, round(share, 3)


_VERDICT = re.compile(r"^([A-Za-zÀ-ÿ][\wÀ-ÿ']*) reaches one final verdict\b", re.I)


def _kw_is_topical(kw: str, topic: str, names: list[str]) -> bool:
    folded = personas.fold(kw or "")
    if not folded:
        return False
    if folded in personas.fold(topic):
        return True
    return any(folded == personas.fold(name) or folded in personas.fold(name).split() for name in names)


def incoherent(text: str, topic: str = "", names: list[str] | None = None) -> str:
    """A mold that picked up a stray noun, such as ``Child reaches one final verdict``."""
    names = names or []
    match = _VERDICT.match((text or "").strip())
    if not match:
        return ""
    subject = match.group(1)
    if _kw_is_topical(subject, topic, names):
        return ""
    return f"stray subject {subject!r} is not the topic or a speaker"


def check(lines: list[dict[str, Any]], sheets: list[dict[str, Any]], topic: str = "", names: list[str] | None = None) -> list[str]:
    """Quote fidelity of a script (any template).

    * ``quote`` lines: the text and the source are the sheet's own, byte for byte, the quote is a primary source
      and links to Wikiquote/Wikipedia/Wikisource. A quote that is not literal is reported as *paraphrased* (it
      reuses most of a sheet quote's words) or *invented* (nothing like it in the sheet).
    * ``paraphrase`` lines: never presented as literal (no quotation marks, no "como eu escrevi:", no "Em <obra>:"),
      never a real quote copied without its mark, never labelled as a quote beat."""
    errors = []
    for i, line in enumerate(lines):
        sheet = sheets[min(line["speaker"], len(sheets) - 1)]
        by_id = {q["id"]: q for q in sheet.get("quotes") or []}
        if line.get("kind") == "quote":
            q = line.get("quote") or {}
            own = by_id.get(q.get("id"))
            if own is None or own["text"] != q.get("text") or line["text"] != q.get("text"):
                errors.append(f"line {i}: quote is not literally in the sheet")
                near, share = closest_quote(line["text"], sheet)
                if near is not None and share >= 0.5:
                    errors.append(f"line {i}: paraphrased quote shown as literal (sheet {near['id']}: {near['text'][:60]})")
                else:
                    errors.append(f"line {i}: invented quote (nothing like it in the sheet)")
                continue
            if not personas.is_primary(own):
                errors.append(f"line {i}: quote comes from a secondary source")
            if not str(q.get("source") or "").strip():
                errors.append(f"line {i}: quote has no source")
            elif q.get("source") != own.get("source", ""):
                errors.append(f"line {i}: quote source differs from the sheet")
            if not SOURCE_URL.match(str(q.get("url", ""))) or q.get("url") != own.get("url"):
                errors.append(f"line {i}: quote has no source link")
            if q.get("wikisource") and not str(q["wikisource"]).startswith("https://"):
                errors.append(f"line {i}: quote has a bad Wikisource link")
        elif line.get("kind") == "paraphrase":
            if line.get("quote"):
                errors.append(f"line {i}: paraphrase carries a quote")
            if any(c in line["text"] for c in QUOTE_MARKS):
                errors.append(f"line {i}: paraphrase uses quotation marks")
            claim = _literal_claim(line["text"], sheet)
            if claim:
                errors.append(f"line {i}: paraphrase posed as a literal quote ({claim})")
            if copies_quote(line["text"], sheet):
                errors.append(f"line {i}: paraphrase repeats a real quote without marking it")
            if str(line.get("beat", "")).startswith("citação"):
                errors.append(f"line {i}: written line labelled as a quote")
        else:
            errors.append(f"line {i}: unmarked line")
        stray = incoherent(str(line.get("text") or ""), topic, names)
        if stray:
            errors.append(f"line {i}: incoherent ({stray})")
    return errors


PARAPHRASE_MAX_WORDS = 16
QUOTE_MAX_WORDS = 30


HOOK_MAX_WORDS = 14
CLOSE_MAX_WORDS = 16


def strong_hook(text: str) -> bool:
    """A hook that grabs in the first seconds: at most 14 words, and a question or a punch of <= 4 words."""
    parts = [p for p in re.split(r"[.!?…:]+", text) if p.strip()]
    return words(text) <= HOOK_MAX_WORDS and ("?" in text or any(words(p) <= 4 for p in parts))


def memorable_close(line: dict[str, Any]) -> bool:
    """A last line that sticks. A real quote is the close; the word cap is only for a written line."""
    if line.get("kind") == "quote":
        return True
    return words(line["text"]) <= CLOSE_MAX_WORDS


def _repeated_idea(lines: list[dict[str, Any]]) -> list[str]:
    """The same idea carried over three lines in a row is a loop, not an argument."""
    ideas = [personas.fold(str((ln.get("replyTo") or {}).get("idea") or "")) for ln in lines]
    return [f"line {i}: the idea '{ideas[i]}' is picked up three lines in a row"
            for i in range(2, len(ideas)) if ideas[i] and ideas[i] == ideas[i - 1] == ideas[i - 2]]


def check_lesson(lines: list[dict[str, Any]], sheets: list[dict[str, Any]], topic: str = "") -> list[str]:
    """Problems with a lesson's shape: hook -> the right question -> a real quote -> an everyday example built on
    that quote -> a turn (a second real quote on the same thread, when the sheet has one) -> a short synthesis that
    recalls the topic or a quote. Lines stay short and varied."""
    errors: list[str] = []
    if len(lines) < 4:
        return ["lesson: fewer than 4 lines"]
    beats = [str(ln.get("beat", "")) for ln in lines]
    if beats[0] != "gancho":
        errors.append("lesson: does not open on a hook")
    elif not strong_hook(lines[0]["text"]):
        errors.append("lesson: weak hook (more than 14 words, no question, no short punch)")
    if topic and not link(topic, lines[0]["text"]):
        errors.append("lesson: the hook does not name the topic")
    if "pergunta" in beats and not lines[beats.index("pergunta")]["text"].rstrip().endswith("?"):
        errors.append("lesson: the question beat is not a question")
    quotes = [i for i, ln in enumerate(lines) if ln.get("kind") == "quote"]
    eligible = _eligible(sheets[0], 34) if sheets else []
    if eligible and not any(counts_as_sheet_quote(ln) for ln in lines):
        errors.append("lesson: no real quote of the speaker's sheet")
    for i in quotes:  # the line after a quote builds on it (the example), so it counts as the thread too
        after = lines[i + 1]["text"] if i + 1 < len(lines) else ""
        if not (on_thread(lines[i]["text"], lines[i - 1]["text"] if i else "", topic) or link(lines[i]["text"], after)):
            errors.append(f"line {i}: quote is off the lesson's thread")
    if "exemplo" in beats:
        e = beats.index("exemplo")
        if e and lines[e - 1].get("kind") == "quote" and not link(lines[e - 1]["text"], lines[e]["text"]):
            errors.append(f"line {e}: the example does not build on the quote before it")
    if not any(ln.get("act") == "virada" or ln.get("beat") == "virada" for ln in lines):
        errors.append("lesson: no turn")
    last = lines[-1]
    if beats[-1] != "síntese":
        errors.append("lesson: does not close on a synthesis")
    else:
        recalls = link(topic, last["text"]) if topic else ""
        recalls = recalls or any(link(lines[i]["text"], last["text"]) for i in quotes)
        if not recalls:
            errors.append("lesson: the synthesis recalls neither the topic nor a quote")
    if not memorable_close(last):
        errors.append(f"lesson: the close is too long ({words(last['text'])} words)")
    counts = [words(ln["text"]) for ln in lines]
    for i, (ln, w) in enumerate(zip(lines, counts)):
        if w > (QUOTE_MAX_WORDS + 6 if ln.get("kind") == "quote" else PARAPHRASE_MAX_WORDS + 4):
            errors.append(f"line {i}: {w} words is too long")
    if len(set(counts)) < len(counts) // 2:
        errors.append("lesson: line lengths do not vary")
    return errors


def _unsaid_quote_refs(lines: list[dict[str, Any]], topic: str = "") -> list[str]:
    """A reply that names a quote which has not been spoken yet. Checked on the final script."""
    problems = []
    spoken = ""
    topic_stems = set(_stems(topic))
    for index, line in enumerate(lines):
        if index and line.get("kind") != "quote":
            novel_pool = set(_stems(str(line.get("text") or ""))) - set(_stems(spoken)) - topic_stems
            for later in range(index + 1, len(lines)):
                if lines[later].get("kind") != "quote":
                    continue
                if novel_pool & set(_stems(str(lines[later].get("text") or ""))):
                    problems.append(f"line {index}: refers to a quote spoken later on line {later}")
                    break
        spoken = f"{spoken} {line.get('text') or ''}"
    return problems


def _names_a_later_quote(text: str, lines: list[dict[str, Any]], index: int, topic: str = "") -> bool:
    """True when ``text`` uses a word that belongs to a quote which has not been spoken yet."""
    stems = set(_stems(text)) - set(_stems(topic))
    earlier = set(_stems(" ".join(str(line.get("text") or "") for line in lines[:index])))
    for later in lines[index + 1:]:
        if later.get("kind") != "quote":
            continue
        if (stems & set(_stems(str(later.get("text") or "")))) - earlier:
            return True
    return False


def answer_notes(lines: list[dict[str, Any]], names: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Keyword overlap is advisory. A missing shared word is not, by itself, a failed script."""
    notes = []
    for index in range(1, len(lines)):
        previous, line = lines[index - 1], lines[index]
        if link(previous["text"], line["text"], quote=line.get("kind") == "quote", names=names):
            continue
        notes.append(f"line {index}: keyword overlap does not tie it to line {index - 1}")
    return notes


def check_debate(lines: list[dict[str, Any]], sheets: list[dict[str, Any]], names: list[str] | tuple[str, ...] = (),
                 topic: str = "") -> list[str]:
    """Problems with a debate's shape. Whether a line answers the one before it is a judge's call, not a
    shared-word check. Speakers alternate, the arc opens, turns, and closes, and each speaker uses a real quote."""
    errors: list[str] = []
    if len(lines) < 5:
        return ["debate: fewer than 5 lines"]
    for i in range(1, len(lines)):
        prev, line = lines[i - 1], lines[i]
        if line["speaker"] == prev["speaker"]:
            errors.append(f"line {i}: same speaker twice in a row")
    if lines[0].get("beat") != "gancho":
        errors.append("debate: does not open on a hook")
    elif not strong_hook(lines[0]["text"]):
        errors.append("debate: weak hook (more than 14 words, no question, no short punch)")
    if topic and not link(topic, lines[0]["text"], names=names):
        errors.append("debate: the hook does not name the topic")
    if len(names) > 1:
        opener = int(lines[0].get("speaker") or 0)
        other = names[1 if opener == 0 else 0]
        if not mentions_speaker(lines[0]["text"], other):
            errors.append("line 0: opener does not address the other speaker")
    for i, line in enumerate(lines):
        speaker = int(line.get("speaker") or 0)
        if speaker < len(names) and mentions_speaker(line["text"], names[speaker], first=True):
            errors.append(f"line {i}: addresses its own speaker")
    errors += _unsaid_quote_refs(lines, topic)
    if lines[-1].get("beat") != "fechamento":
        errors.append("debate: does not close")
    elif not memorable_close(lines[-1]):
        errors.append(f"debate: the close is not memorable ({words(lines[-1]['text'])} words, no punch)")
    turns = [i for i, ln in enumerate(lines) if ln.get("act") == "virada" or ln.get("beat") == "virada"]
    if not any(len(lines) / 4 <= i <= 3 * len(lines) / 4 for i in turns):
        errors.append("debate: no turn in the middle")
    for sp, sheet in enumerate(sheets[:2]):
        name = names[sp] if sp < len(names) else ""
        if _eligible(sheet, QUOTE_MAX_WORDS - 2) and not any(
                ln.get("speaker") == sp and owns_sheet_quote(ln, sheet, name) for ln in lines):
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
