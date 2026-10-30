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
from dataclasses import dataclass
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


PLANS: dict[str, tuple[Beat, ...]] = {
    "debate": (
        Beat("gancho", 0, "paraphrase", 14, 0.35),
        Beat("tese", 0, "quote", 30, 0.25),
        Beat("réplica", 1, "quote", 30, 0.2),
        Beat("réplica", 0, "paraphrase", 16, 0.15),
        Beat("réplica", 1, "quote", 20, 0.15),
        Beat("tréplica", 0, "quote", 30, 0.45),
        Beat("fechamento", 1, "paraphrase", 16, 0.3),
        Beat("fechamento", 0, "paraphrase", 14, 0.0),
    ),
    "lesson": (
        Beat("gancho", 0, "paraphrase", 14, 0.4),
        Beat("ponto 1", 0, "quote", 34, 0.3),
        Beat("ponto 2", 0, "paraphrase", 20, 0.3),
        Beat("ponto 3", 0, "quote", 34, 0.3),
        Beat("exemplo", 0, "paraphrase", 22, 0.35),
        Beat("resumo", 0, "paraphrase", 18, 0.0),
    ),
    "solo": (
        Beat("gancho", 0, "paraphrase", 14, 0.3),
        Beat("tese", 0, "quote", 30, 0.3),
        Beat("argumento", 0, "paraphrase", 18, 0.3),
        Beat("fechamento", 0, "paraphrase", 14, 0.0),
    ),
}


def words(text: str) -> int:
    return len(text.split())


def max_seconds(max_words: int) -> float:
    return round(max_words / WORDS_PER_S + 0.8, 1)


def cite(q: dict[str, Any]) -> dict[str, Any]:
    out = {"id": q["id"], "text": q["text"], "source": q.get("source", ""), "url": q.get("url", ""), "site": "Wikiquote"}
    if q.get("wikisource"):
        out["wikisource"] = q["wikisource"]
    return out


def _tokens(text: str) -> set[str]:
    """Content words, cut to a 6-letter stem so "verdade" meets "verdades"."""
    return {w[:6] for w in personas.fold(text).split() if len(w) > 3}


def _quotable(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    """Quotes that may appear as "Citação real": primary sources only."""
    return [q for q in sheet.get("quotes") or [] if personas.is_primary(q)]


def _eligible(sheet: dict[str, Any], max_words: int) -> list[dict[str, Any]]:
    return [q for q in _quotable(sheet) if words(q["text"]) <= max_words]


def topic_score(q: dict[str, Any], topic: str) -> int:
    return len(_tokens(topic) & _tokens(q["text"]))


def _rank(quotes: list[dict[str, Any]], topic: str) -> list[dict[str, Any]]:
    """On-topic first, then shorter (tight pacing), then sheet order."""
    return sorted(quotes, key=lambda q: (-topic_score(q, topic), words(q["text"])))


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


def copies_quote(text: str, sheet: dict[str, Any], run: int = 7) -> bool:
    """True when ``text`` repeats >= ``run`` consecutive words of a sheet quote (an unmarked quote)."""
    t = personas.fold(text).split()
    grams = {" ".join(t[i:i + run]) for i in range(len(t) - run + 1)}
    return any(g in personas.fold(q["text"]) for q in sheet.get("quotes") or [] for g in grams)


# ---------------------------------------------------------------- template writer


def _templates(lang: str, topic: str, names: list[str]) -> dict[str, list[str]]:
    a = names[0] if names else ""
    b = names[1] if len(names) > 1 else ""
    if lang == "en-US":
        return {
            "gancho": [f"{topic}: what if everything you believe about it is wrong?"],
            "réplica": [f"Nice line. But it does not answer the question about {topic}.", "Then prove it. Words are cheap, examples are not."],
            "fechamento": [f"Keep the slogans, {a}. I keep the doubt.", f"Then let the audience decide, {b}."],
            "ponto 2": [f"Second point: {topic} only makes sense when you test it yourself."],
            "exemplo": [f"Example: take one belief about {topic} and ask why, three times in a row."],
            "resumo": [f"Summary: question, test, and only then believe. That is {topic}."],
            "argumento": [f"Look at {topic} up close and the easy answers fall apart."],
        }
    return {
        "gancho": [f"{topic.capitalize()}: e se tudo o que você acha disso estiver errado?"],
        "réplica": [f"Bonita frase. Mas ela não responde nada sobre {topic}.", "Então prove. Frase de efeito é barata, exemplo não."],
        "fechamento": [f"Fique com os slogans, {a}. Eu fico com a dúvida.", f"Então que o público decida, {b}."],
        "ponto 2": [f"Segundo ponto: {topic} só faz sentido quando você mesmo testa."],
        "exemplo": [f"Exemplo: pegue uma certeza sobre {topic} e pergunte por quê, três vezes seguidas."],
        "resumo": [f"Resumo: perguntar, testar e só então acreditar. Isso é {topic}."],
        "argumento": [f"Olhe {topic} de perto e as respostas fáceis desmoronam."],
    }


def _template_text(beat: Beat, i: int, lang: str, topic: str, names: list[str]) -> str:
    bank = _templates(lang, topic, names)
    options = bank.get(beat.name) or bank["réplica"]
    if beat.name == "fechamento":
        return options[0 if beat.speaker == 1 else 1] if len(names) > 1 else options[0]
    return options[i % len(options)]


# ---------------------------------------------------------------- Gemini writer


def _prompt(plan: list[Beat], sheets: list[dict[str, Any]], names: list[str], topic: str, lang: str, template: str,
            passages: list[list[dict[str, Any]]] | None = None) -> str:
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
           if b.kind == "quote" else f"WRITE text of at most {b.max_words} words")
        for i, b in enumerate(plan)
    )
    language = "Brazilian Portuguese" if lang == "pt-BR" else "American English"
    shape = {
        "debate": "a tight debate for a 60-second vertical video: hook, short alternating replies that answer each other, a rebuttal, a punchy close",
        "lesson": "a short lesson: a hook question, three points, one concrete example, a one-line summary",
        "solo": "a short monologue: hook, thesis, one argument, close",
    }[template]
    return f"""You write {shape}, in {language}, about: {topic}

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
- Closing: the second-to-last line concedes nothing and strikes once; the last line is a punchline that ends the video.
- In a lesson, each point opens with "Primeiro", "Segundo", "Terceiro" (or First/Second/Third) in written beats; the example is concrete and everyday.

Answer JSON only: {{"lines": [{{"i": 0, "quoteId": null, "basisId": null, "text": "..."}}, ...]}} with one entry per beat."""


def _gemini(prompt: str) -> list[dict[str, Any]]:
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
          indexes: list[Any] | None = None) -> dict[str, Any]:
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
            for entry in _gemini(_prompt(plan, sheets, names, topic, lang, template, passages)):
                if isinstance(entry, dict) and isinstance(entry.get("i"), int):
                    drafted[entry["i"]] = entry
            writer = "gemini"
        except Exception as exc:  # noqa: BLE001 — the template writer always works
            _LOG.warning("script writer fell back to templates: %s", exc)

    used: set[tuple[int, str]] = set()
    lines: list[dict[str, Any]] = []
    for i, beat in enumerate(plan):
        sp = min(beat.speaker, n - 1)
        sheet = sheets[sp]
        by_id = {q["id"]: q for q in _quotable(sheet)}
        draft = drafted.get(i) or {}
        line: dict[str, Any] = {"speaker": sp, "beat": beat.name, "maxS": max_seconds(beat.max_words), "pauseAfterS": beat.pause_s}
        quote = None
        if beat.kind == "quote":
            pool = [q for q in _eligible(sheet, beat.max_words) if (sp, q["id"]) not in used]
            qid = draft.get("quoteId")
            ranked = _rank(pool, topic)
            quote = next((q for q in pool if q["id"] == qid), None)
            if quote is None or (ranked and topic_score(quote, topic) == 0 < topic_score(ranked[0], topic)):
                quote = next(iter(ranked), None)  # unknown/reused id, or off-topic while an on-topic quote fits
        else:
            # a written line that *is* one of the speaker's real quotes is shown as that quote, with its source
            pool = [q for q in _quotable(sheet) if (sp, q["id"]) not in used]
            quote = matching_quote(draft.get("text") or "", pool, beat.max_words + 6)
        if quote is not None:
            used.add((sp, quote["id"]))
            line.update(kind="quote", text=quote["text"], quote=cite(quote))
            line["maxS"] = max(line["maxS"], max_seconds(words(quote["text"])))
        else:
            text = _clean_paraphrase(draft.get("text") or "", beat.max_words)
            if not text or copies_quote(text, sheet) or knowledge.copies_passage(text, passages[sp]):
                text = _template_text(beat, i, lang, topic, names)
            line.update(kind="paraphrase", text=text)
            basis = by_id.get(draft.get("basisId") or "")
            if basis is not None:
                line["basis"] = {"id": basis["id"], "source": basis.get("source", ""), "url": basis.get("url", "")}
        lines.append(line)
    personas_out = [
        {"name": nm, "found": bool(s.get("found")), "wikiquote": s.get("wikiquote"), "wikipedia": s.get("wikipedia"),
         "quotes": len(s.get("quotes") or []), "primaryQuotes": len(_quotable(s)), "license": s.get("license"),
         "mode": ix[0].get("mode", mode), "chunks": len(ix[0].get("chunks") or []),
         "passages": [{"source": p["source"], "url": p["url"], "score": p["score"]} for p in ps]}
        for nm, s, ix, ps in zip(names, sheets, indexes, passages)
    ]
    return {"lines": lines, "writer": writer, "personas": personas_out, "topic": topic}


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
