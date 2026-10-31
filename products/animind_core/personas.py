"""Persona sheets: real, sourced quotes for a character name.

A sheet ("ficha") is built from Wikiquote (``pt`` for pt-BR, ``en`` for
en-US) and cached as ``ANIMIND_DATA_DIR/personas/<lang>/<slug>.yaml``
(outside git). Only sections with the person's own words are read:
"Atribuídas", "Sobre", "Disputed", "Misattributed", "Quotes about" and the like
are skipped. Each quote keeps its text exactly as cleaned from the page
wikitext, the work it comes from and links to Wikiquote, Wikipedia and,
when the page cites it, Wikisource. The script writer may only put these
strings on screen as quotes (see :mod:`products.animind_core.script`).

Each quote also gets ``sourceKind``: ``primary`` when its source names a work
by or about the person (book, dialogue, letter, speech: a title, a page, a §
or a section named after the work) and ``secondary`` when it only comes from a
magazine, website, anthology or "citado em" chain, from an index or register
page ("Sachregister", "Índice") rather than the work, or has no work at all.
Entries flagged as attributed, disputed or misattributed are dropped. Primary
quotes rank first and only they may be shown as a real quote.

A name with no matching biography page gets ``found: false`` (cached for a
week) and every line of that character is a marked paraphrase.
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from . import settings

_LOG = logging.getLogger("animind_core")
UA = "AnimindCore/1.0 (github.com/diogolean/animind; persona quotes)"
SITES = {"pt-BR": "pt", "en-US": "en"}
MAX_QUOTES = 24
SHEET_VERSION = 3  # bump when parsing/ranking changes; older cached sheets are rebuilt
MIN_WORDS, MAX_WORDS = 4, 45
NEGATIVE_TTL_S = 7 * 24 * 3600
SKIP_SECTION = re.compile(
    r"atribu|sobre|disputad|duvidos|erroneamente|equivocad|incorret|misattrib|disputed|about|quotes about|"
    r"ver também|see also|ligações externas|external links|referências|references|fontes|cast|elenco|dialog",
    re.I,
)
PERSON_PAGE = re.compile(r"\{\{\s*(info/biografia|autor|wikipedia|pessoa|biografia)|\[\[\s*(categoria|category)\s*:", re.I)
GENERIC_SECTION = re.compile(r"^(cita[cç][õo]es|frases|quotes|sourced|verificad)", re.I)
DISPUTED = re.compile(r"atribu[ií]d|disputad|apócrif|apocryph|misattrib|erroneamente|duvidos|sem fonte|unsourced|não confirmad", re.I)
SECONDARY = re.compile(
    r"revista|magazine|jornal|newspaper|\bfolha\b|nova escola|citad[oa]s? (em|in|por)|como citad|quoted in|cited in|as quoted|"
    r"handbook|epigram|antologia|anthology|dicionário de citações|dictionary of quotations|frases para|pensador|"
    r"\bsite\b|\bblog\b|www\.|\.com\b|times\b|internet",
    re.I,
)
# back-of-book apparatus: an index/register page lists where a phrase occurs, it is not the work itself
INDEX = re.compile(
    r"sachregister|namenregister|personenregister|wortregister|\bregister\b|\bregistro remissivo|\bindex\b|"
    r"\b[íi]ndice\b|inhaltsverzeichnis|verzeichnis|concord[aâ]ncia|concordance|\bsum[áa]rio\b|table of contents",
    re.I,
)
DISAMBIG = re.compile(r"\{\{\s*(desambigua|disambig)", re.I)


def fold(text: str) -> str:
    """Lowercase ASCII with single spaces, for name matching."""
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).split())


def slug(text: str) -> str:
    return (fold(text).replace(" ", "_") or "persona")[:48]


# ------------------------------------------------------------------ wikitext


def clean(markup: str) -> str:
    """Wikitext -> plain text, as read on the page."""
    s = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", markup, flags=re.S)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    for _ in range(3):
        s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\]", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("'''", "").replace("''", "").replace("&nbsp;", " ")
    s = " ".join(s.split())
    s = re.sub(r"^[-–—:\s]+", "", s)
    return s.strip()


def _unquote(text: str) -> str:
    text = text.strip()
    pairs = [('"', '"'), ("“", "”"), ("«", "»"), ("„", "“")]
    for a, b in pairs:
        if text.startswith(a) and text.count(a if a != b else a) >= 1:
            inner = text[len(a):]
            # "text". / "text" — closing quote at the end, maybe before a final dot
            m = re.fullmatch(rf"(.*){re.escape(b)}\s*([.!?]?)", inner, re.S)
            if m and b not in m.group(1):
                return m.group(1).strip()
    return text


def _wikisource(markup: str, lang: str) -> str | None:
    m = re.search(r"\[\[\s*s:([^|\]]+)", markup)
    if not m:
        return None
    title = m.group(1).split("#")[0].strip()
    return f"https://{lang}.wikisource.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"


def parse_quotes(wikitext: str, *, site_lang: str = "pt", page_url: str = "") -> list[dict[str, Any]]:
    """Top-level bullet quotes with their source line, skipping sections not in the person's voice."""
    out: list[dict[str, Any]] = []
    section, group, skip = "", "", False
    cur: dict[str, Any] | None = None
    subs: list[str] = []

    def flush() -> None:
        nonlocal cur, subs
        if cur is not None:
            src_lines = [s for s in subs if not re.match(r"\s*(variante|variant|original|nota|note)\b", clean(s), re.I)]
            source = re.sub(r"^(fonte|source)\s*:\s*", "", clean(src_lines[-1] if src_lines else ""), flags=re.I)
            if len(source) > 110:
                source = source[:110].rsplit(",", 1)[0].rstrip(" -,") + "…"
            grp = "" if GENERIC_SECTION.search(group) or fold(source).startswith(fold(group) or "\0") else group
            raw_src = " ".join(src_lines)
            joined = " ".join(clean(s) for s in src_lines)
            secondary = bool(SECONDARY.search(source) or SECONDARY.search(joined) or INDEX.search(source) or INDEX.search(joined))
            # the section names the work ("Apologia de Sócrates") unless the source already does or is secondary
            sec_words = {w for w in fold(section).split() if len(w) > 3 and w.isalpha()}
            sec = section if section and not secondary and not (sec_words & set(fold(source).split())) else ""
            cur["source"] = " · ".join(p for p in dict.fromkeys((grp, sec, source)) if p)
            cur["work"] = section or group
            if DISPUTED.search(" ".join(clean(s) for s in subs)):
                cur = None  # attributed / disputed / misattributed: not the person's words
            else:
                has_work = bool(sec or re.search(r"''|\d|§", raw_src) or (grp and source and source != grp))
                cur["sourceKind"] = "secondary" if secondary or not has_work else "primary"
            ws = _wikisource(" ".join(subs) + " " + cur.pop("_raw"), site_lang) if cur is not None else None
            if ws:
                cur["wikisource"] = ws
            words = len(cur["text"].split()) if cur is not None else 0
            if cur is not None and MIN_WORDS <= words <= MAX_WORDS and "[" not in cur["text"] and "{" not in cur["text"]:
                out.append(cur)
        cur, subs = None, []

    for raw in wikitext.splitlines():
        line = raw.rstrip()
        h = re.match(r"^(=+)\s*(.*?)\s*\1\s*$", line)
        if h:
            flush()
            level, title = len(h.group(1)), clean(h.group(2))
            if level <= 2:
                group, section = (title if not SKIP_SECTION.search(title) else ""), ""
                skip = bool(SKIP_SECTION.search(title))
            else:
                section = title
                skip = bool(SKIP_SECTION.search(title)) or (skip and level > 2 and not group and bool(SKIP_SECTION.search(section)))
            if level >= 3 and re.search(r"verificad|sourced", title, re.I):
                section = ""
            continue
        if skip:
            continue
        if re.match(r"^\*(?!\*)", line):
            body = line[1:].strip()
            text = _unquote(clean(body))
            if body.startswith("''") and len(text.split()) < 10:
                if cur is not None and not subs:
                    subs.append(body)  # a source line written with a single star
                continue
            flush()
            cur = {"text": text, "_raw": body}
        elif cur is not None and re.match(r"^(\*\*|:)", line):
            subs.append(line.lstrip("*: ").strip())
        elif line.strip() and cur is not None and not line.startswith(("[[", "{{")):
            flush()
    flush()
    seen, unique = set(), []
    for q in out:
        key = fold(q["text"])
        if key and key not in seen:
            seen.add(key)
            q["url"] = page_url
            unique.append(q)
    return unique


def pick(quotes: list[dict[str, Any]], n: int = MAX_QUOTES) -> list[dict[str, Any]]:
    """Keep the n best for speech, ids q1..qn in rank order.

    Primary sources first, then speakable length (6–20 words, then up to 30), taking works in turn
    (one quote per work per round) so one long section does not fill the sheet.
    """
    def tier(q: dict[str, Any]) -> tuple[int, int]:
        w = len(q["text"].split())
        return (0 if q.get("sourceKind", "primary") == "primary" else 1, 0 if 6 <= w <= 20 else 1 if w <= 30 else 2)

    ranked: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
    seen_per: dict[tuple[tuple[int, int], str], int] = {}
    for i, q in enumerate(quotes):
        t = tier(q)
        k = (t, q.get("work", ""))
        r = seen_per.get(k, 0)
        seen_per[k] = r + 1
        ranked.append(((t[0], t[1], r, i), q))
    chosen = [q for _, q in sorted(ranked, key=lambda x: x[0])[:n]]
    return [{"id": f"q{k + 1}", **q} for k, q in enumerate(chosen)]


# ------------------------------------------------------------------ fetching


def _api(site: str, **params: Any) -> dict[str, Any]:
    url = f"https://{site}/w/api.php?" + urllib.parse.urlencode({"format": "json", "formatversion": 2, **params})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 — fixed https host
        return json.load(r)


def _page(site: str, title: str) -> tuple[str, str] | None:
    data = _api(site, action="parse", page=title, prop="wikitext", redirects=1)
    if "parse" not in data:
        return None
    return data["parse"]["title"], data["parse"]["wikitext"]


def find_page(name: str, lang: str) -> tuple[str, str] | None:
    """(title, wikitext) of the person's Wikiquote page, or None. Only accepts close name matches."""
    site = f"{SITES.get(lang, 'pt')}.wikiquote.org"
    want = fold(name)
    if len(want) < 3:
        return None
    candidates = [name.strip()]
    try:
        hits = _api(site, action="opensearch", search=name.strip(), limit=5, namespace=0)
        candidates += [t for t in hits[1] if t not in candidates]
    except Exception:  # noqa: BLE001 — offline: try the exact title only
        pass
    for title in candidates:
        got = fold(title)
        if not (got == want or (want in got.split() or f" {want}" in f" {got}") or got in want.split() or want.endswith(got) or got.endswith(want)):
            continue
        page = _page(site, title)
        if page and PERSON_PAGE.search(page[1]) and not DISAMBIG.search(page[1]):
            return page
    return None


def wiki_url(site: str, title: str) -> str:
    return f"https://{site}/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"


def build(name: str, lang: str = "pt-BR") -> dict[str, Any]:
    """Fetch a fresh sheet (network)."""
    sl = SITES.get(lang, "pt")
    sheet: dict[str, Any] = {"name": name, "lang": lang, "found": False, "version": SHEET_VERSION, "fetchedAt": int(time.time()), "quotes": []}
    page = find_page(name, lang)
    if not page:
        return sheet
    title, wikitext = page
    url = wiki_url(f"{sl}.wikiquote.org", title)
    quotes = pick(parse_quotes(wikitext, site_lang=sl, page_url=url))
    sheet.update(found=bool(quotes), title=title, wikiquote=url, wikipedia=wiki_url(f"{sl}.wikipedia.org", title), quotes=quotes,
                 license="Textos da Wikiquote, CC BY-SA 4.0")
    return sheet


def persona_name(name: str) -> str:
    """The person in a character name: "Sócrates, filósofo de toga" -> "Sócrates"."""
    head = re.split(r"\s*(?:[,(:;]|\s[-–—]\s)", (name or "").strip(), maxsplit=1)[0]
    return " ".join(head.split())[:60]


def is_primary(q: dict[str, Any]) -> bool:
    """A quote that may be shown as a real quote (sheets cached before ``sourceKind`` are judged by their source)."""
    src = q.get("source", "") or ""
    if INDEX.search(src):  # an index/register page, even on a sheet cached as primary
        return False
    kind = q.get("sourceKind")
    if kind:
        return kind == "primary"
    return bool(src) and not SECONDARY.search(src) and not DISPUTED.search(src)


def cache_path(name: str, lang: str) -> Path:
    return settings.data_dir() / "personas" / lang / f"{slug(name)}.yaml"


def load(name: str, lang: str = "pt-BR", *, fetch: bool = True) -> dict[str, Any]:
    """Cached sheet for ``name``; fetches on a miss (or an expired negative)."""
    path = cache_path(name, lang)
    if path.is_file():
        sheet = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        fresh = sheet.get("version") == SHEET_VERSION
        if (fresh and sheet.get("found")) or not fetch or (fresh and time.time() - float(sheet.get("fetchedAt") or 0) < NEGATIVE_TTL_S):
            return sheet
    if not fetch:
        return {"name": name, "lang": lang, "found": False, "quotes": []}
    try:
        sheet = build(name, lang)
    except Exception as exc:  # noqa: BLE001 — offline: paraphrase only, do not cache
        _LOG.warning("persona %r: %s", name, exc)
        return {"name": name, "lang": lang, "found": False, "quotes": []}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(sheet, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")
    return sheet
