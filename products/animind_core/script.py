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


# Dramatic shape. Debate: hook (provocation) -> thesis (quote) -> reply that answers it (quote) -> two quick
# jabs -> the turn (the challenger uses the opponent's own words) -> punchline -> closing real quote.
# Lesson: hook question -> the right question -> quote -> everyday example -> quote -> synthesis.
PLANS: dict[str, tuple[Beat, ...]] = {
    "debate": (
        Beat("gancho", 0, "paraphrase", 14, 0.5),
        Beat("tese", 0, "quote", 30, 0.45, -5),
        Beat("réplica", 1, "quote", 30, 0.3, -3),
        Beat("réplica", 0, "paraphrase", 16, 0.2, 6),
        Beat("réplica", 1, "quote", 20, 0.25, 4, optional=True),
        Beat("tréplica", 0, "paraphrase", 17, 0.6, 2),
        Beat("fechamento", 1, "paraphrase", 12, 0.45, 4),
        Beat("fechamento", 0, "quote", 24, 0.0, -8, optional=True, strong=True),
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
today question""".split())
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


def keyword(text: str, topic: str = "", *, related_only: bool = False, avoid: str = "") -> str:
    """The word a reply picks up from ``text``: topic-related nouns first, then nouns (after an article or
    a noun ending), then longer words; singular, never verbs or function words. ``avoid`` (the topic noun)
    is skipped so a reply names the *new* idea; very long words (mouthfuls on screen and in TTS) rank lower."""
    near = _related(_tokens(topic))
    skip = {personas.fold(singular(w) or w) for w in re.findall(r"[A-Za-zÀ-ÿ]+", avoid or "")}
    best, score = "", -99.0
    prev = ""
    for raw in re.findall(r"[A-Za-zÀ-ÿ]+(?:-[A-Za-zÀ-ÿ]+)?", text or ""):
        after_det, prev = prev in _DET, raw.lower()
        w = singular(raw)
        if "-" in raw or not w or raw.lower().endswith("mos") or w in _STOP_KW or personas.fold(w) in _STOP_FOLD or len(w) < 4:
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
    """Molds per beat. Fields: {Q} topic as asked, {noun} bare key noun, {the}/{The} noun with its article,
    {a}/{b} speakers, {kw}/{Kw} the idea picked up from the line before, {kt} a topic word the other used."""
    if lang == "en-US":
        return {
            "gancho2q": ["{Q} A simple question, {b}. I want an answer, not a speech.",
                         "{Q} Today, {b}, nobody leaves without answering.",
                         "One question, {b}, and no dodging. {Q}"],
            "gancho2": ["Today the subject is {the}, {b}. And I only brought questions.",
                        "{The}. Everyone is sure about it, {b}. Let us see if it survives one question."],
            "gancho1q": ["{Q} Sounds easy. It is not.", "{Q} Take three seconds before you answer."],
            "gancho1": ["Today: {the}. Everything you think you know is about to be tested."],
            "tese": ["My thesis is simple: {the} is not accepted. It is examined."],
            "réplica0": ["{Kw}, {b}? Then tell me: who decides what {noun} is?",
                         "Easy, {b}. If everything is {kw}, so is your sentence. Why should I believe it?",
                         "You swap {noun} for {kw}, {b}. Is that an answer… or an escape?",
                         "{Kw}? Lovely word, {b}. Now define it."],
            "réplica1": ["Questions, questions, {a}. Do you ever risk an answer?",
                         "You call {kw} what is only habit, {a}.",
                         "{Kw}, {a}? That is fear of admitting nobody knows.",
                         "There you go again, {a}. A pretty question is not proof."],
            "tréplicaq": ["Notice, {b}: you just stated something as certain. You used the very thing you wanted to destroy.",
                          "So you are certain of that, {b}? You, who doubt everything?"],
            "tréplica": ["Notice, {b}: you just stated something as certain. You used the very thing you wanted to destroy.",
                         "So you admit it, {b}: doubting {the} is already looking for an answer.",
                         "See, {b}? Many words, and no answer about {the}. I am still waiting."],
            "fechamento1": ["Keep asking, {a}. I would rather live.",
                            "Keep your questions, {a}. Life does not wait for answers.",
                            "Talk about {kt} all you want, {a}. I bow to no one."],
            "fechamento0": ["Then let the audience decide, {b}. I only asked questions.",
                            "The question stays, {b}. And it is stronger than both of us."],
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
        "gancho2q": ["{Q} Pergunta simples, {b}. Quero resposta, não discurso.",
                     "{Q} Hoje, {b}, ninguém sai daqui sem responder.",
                     "Uma pergunta, {b}, e sem fugir. {Q}"],
        "gancho2": ["Hoje o assunto é {the}, {b}. E eu só trouxe perguntas.",
                    "{The}. Todo mundo tem certeza, {b}. Vamos ver se resiste a uma pergunta."],
        "gancho1q": ["{Q} Parece fácil. Não é.", "{Q} Pense três segundos antes de responder."],
        "gancho1": ["Hoje: {the}. Tudo o que você acha que sabe vai passar por um teste."],
        "tese": ["Minha tese é simples: {the} não se aceita. Se examina."],
        "réplica0": ["{Kw}, {b}? Então me diga: quem decide o que é {noun}?",
                     "Calma, {b}. Se tudo é {kw}, sua frase também é. Por que eu acreditaria nela?",
                     "Você troca {noun} por {kw}, {b}. Isso é resposta… ou fuga?",
                     "{Kw}? Bonita palavra, {b}. Agora defina."],
        "réplica1": ["Perguntas, perguntas, {a}. Você nunca arrisca uma resposta?",
                     "Você chama de {kw} o que é só costume, {a}.",
                     "{Kw}, {a}? Isso é medo de admitir que ninguém sabe.",
                     "Lá vem você de novo, {a}. Pergunta bonita não é prova."],
        "tréplicaq": ["Repare, {b}: você acabou de afirmar algo como certo. Usou justamente o que queria derrubar.",
                      "Então disso você tem certeza, {b}? Logo você, que duvida de tudo?"],
        "tréplica": ["Repare, {b}: você acabou de afirmar algo como certo. Usou justamente o que queria derrubar.",
                     "Então você admite, {b}: duvidar de {the} já é procurar uma resposta.",
                     "Viu só, {b}? Muita palavra e nenhuma resposta sobre {the}. Eu continuo esperando."],
        "fechamento1": ["Continue perguntando, {a}. Eu prefiro viver.",
                        "Fique com suas perguntas, {a}. A vida não espera resposta.",
                        "Fale de {kt} o quanto quiser, {a}. Eu não me curvo a ninguém."],
        "fechamento0": ["Então que o público decida, {b}. Eu só fiz perguntas.",
                        "Fica a pergunta, {b}. E ela é mais forte que nós dois."],
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


def _seed(topic: str, names: list[str], lang: str) -> int:
    import hashlib  # noqa: PLC0415

    return int(hashlib.sha1(f"{personas.fold(topic)}|{'|'.join(names)}|{lang}".encode()).hexdigest()[:8], 16)


def _template_text(beat: Beat, lang: str, topic: str, names: list[str], before: list[dict[str, Any]], n: int,
                   taken: set[str], molds: set[str] | None = None, seed: int = 0, index: int = 0) -> str:
    """A written line for ``beat`` that answers ``before`` (the lines so far), never one already in ``taken``
    and never a mold already in ``molds`` (updated in place)."""
    molds = set() if molds is None else molds
    head, noun, is_q = topic_parts(topic)
    the = noun_phrase(topic, noun)
    a = names[0] if names else ""
    b = names[1] if len(names) > 1 else a
    sp = min(beat.speaker, n - 1)
    prev = before[-1]["text"] if before else ""
    own = [ln["text"] for ln in before if ln["speaker"] == sp and ln.get("kind") == "quote"]
    other = [ln["text"] for ln in before if ln["speaker"] != sp]
    # words already said in written lines are not picked up again ("Convicção?" once, not three times)
    said = " ".join([noun] + [ln["text"] for ln in before if ln.get("kind") != "quote"])
    kw = keyword(prev, topic, avoid=said) if prev and (n == 1 or before[-1]["speaker"] != sp) else ""
    if not kw and other:
        kw = keyword(other[-1], topic, avoid=said)
    if not kw and own:
        kw = keyword(own[-1], topic, avoid=said)
    kw = kw or noun
    kt = next((k for k in (keyword(t, topic, related_only=True) for t in reversed(other)) if k), noun)
    bank = _bank(lang)
    if beat.name == "gancho":
        key = ("gancho2" if n > 1 else "gancho1") + ("q" if is_q else "")
    elif beat.name in ("réplica", "fechamento") and n > 1:
        key = f"{beat.name}{sp}"
    elif beat.name == "tréplica" and before and before[-1].get("kind") == "quote" and before[-1]["speaker"] != sp:
        key = "tréplicaq"  # the opponent just stated something as true: turn his own certainty against him
    else:
        key = beat.name if beat.name in bank else ("réplica0" if sp == 0 else "réplica1")
    fields = {"Q": head, "noun": noun, "the": the, "The": the[:1].upper() + the[1:], "a": a, "b": b, "kw": kw,
              "Kw": kw[:1].upper() + kw[1:], "kt": kt}
    fallback = "réplica0" if sp == 0 else "réplica1"
    for k in dict.fromkeys((key, *[k for k in bank if k.startswith(beat.name.split(" ")[0])], fallback)):
        opts = bank[k]
        start = (seed + 7 * index) % len(opts)
        for j in range(len(opts)):
            m = (start + j) % len(opts)
            text = tidy(_contract(opts[m].format(**fields), lang))
            if f"{k}:{m}" not in molds and personas.fold(text) not in taken and words(text) <= beat.max_words + 1:
                molds.add(f"{k}:{m}")
                return text
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
        f"  {i}. speaker {b.speaker}, beat '{b.name}', "
        + (f"PICK a quoteId of speaker {b.speaker} (only quotes of at most {b.max_words} words), do not write text"
           + (" (if no quote answers the previous line, WRITE text of at most 16 words instead)" if b.optional else "")
           if b.kind == "quote" else f"WRITE text of at most {b.max_words} words")
        for i, b in enumerate(plan)
    )
    language = "Brazilian Portuguese" if lang == "pt-BR" else "American English"
    shape = {
        "debate": "a tight debate for a 60-second vertical video: hook, short alternating replies that answer each other, a rebuttal, a punchy close",
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
- Each reply answers the line right before it (name the idea you attack). The rebuttal turns the opponent's own words.
- The turn (tréplica) uses the opponent's own words against him. Closing: the second-to-last line concedes nothing and
  strikes once; the last line is the strongest quote, a punchline that ends the video.
- Rhythm: one idea per sentence, sentences of 3 to 9 words, commas and "…" where the voice should breathe; never two
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
    for i, beat in enumerate(plan):
        sp = min(beat.speaker, n - 1)
        sheet = sheets[sp]
        by_id = {q["id"]: q for q in _quotable(sheet)}
        draft = drafted.get(i) or {}
        line: dict[str, Any] = {"speaker": sp, "beat": beat.name, "maxS": max_seconds(beat.max_words, beat.rate),
                                "pauseAfterS": beat.pause_s, "rateAdj": beat.rate, "emotion": emotion_of(draft, beat.name)}
        quote = None
        if beat.kind == "quote":
            pool = [q for q in _eligible(sheet, beat.max_words) if (sp, q["id"]) not in used]
            qid = draft.get("quoteId")
            previous = lines[-1]["text"] if lines else ""
            ranked = _rank_strong(pool, topic, previous) if beat.strong else _rank(pool, topic, previous)
            quote = next((q for q in pool if q["id"] == qid), None)
            if quote is None or (ranked and not (_tokens(topic) & _tokens(quote["text"])) and topic_score(quote, topic) < topic_score(ranked[0], topic)):
                quote = next(iter(ranked), None)  # unknown/reused id, or off-topic while an on-topic quote fits
                if quote is not None and beat.optional and not qid and not answers(quote, topic, previous):
                    quote = None  # offline: no quote answers the line before -> a written line, not a non sequitur
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
            if (not text or personas.fold(text) in taken or copies_quote(text, sheet)
                    or knowledge.copies_passage(text, passages[sp])):
                text = _template_text(beat, lang, topic, names, lines, n, taken, molds, seed, i)
            line.update(kind="paraphrase", text=text)
            basis = by_id.get(draft.get("basisId") or "")
            if basis is not None:
                line["basis"] = {"id": basis["id"], "source": basis.get("source", ""),
                                 "label": source_label(basis.get("source", "")), "url": basis.get("url", "")}
        lines.append(line)
    personas_out = [
        {"name": nm, "found": bool(s.get("found")), "wikiquote": s.get("wikiquote"), "wikipedia": s.get("wikipedia"),
         "quotes": len(s.get("quotes") or []), "primaryQuotes": len(_quotable(s)), "license": s.get("license"),
         "mode": ix[0].get("mode", mode), "chunks": len(ix[0].get("chunks") or []),
         "passages": [{"source": p["source"], "url": p["url"], "score": p["score"]} for p in ps]}
        for nm, s, ix, ps in zip(names, sheets, indexes, passages)
    ]
    return {"lines": lines, "writer": writer, "personas": personas_out, "topic": topic, "tone": tone,
            "quoteLines": sum(1 for ln in lines if ln["kind"] == "quote")}


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
