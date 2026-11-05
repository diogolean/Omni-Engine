"""Per-character knowledge (RAG) behind the script writer, in two modes.

* ``quick``: inference from a few key terms. The Wikipedia lead plus the persona sheet's quotes,
  chunked and indexed in one request; built inline and cached next to the persona sheet.
* ``deep``: a background job that reads full texts (Wikipedia article, the whole Wikiquote page and
  up to ``WIKISOURCE_PAGES`` Wikisource works by the author), chunks and indexes them.

Embeddings are cheap and local: hashed bag of folded word stems and stem bigrams (``DIM`` floats,
L2-normalised), so building an index costs no API calls and retrieval is a dot product.
Passages are background for ideas only; spoken quotes still come verbatim from the persona sheet.

Cache: ``<data>/personas/<lang>/<slug>.<mode>.json`` (chunks + metadata) and ``.<mode>.npy`` (vectors).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from . import personas, settings

_LOG = logging.getLogger(__name__)
MODES = ("quick", "deep")
DIM = 1024
CHUNK_WORDS = 110
CHUNK_OVERLAP = 25
MAX_CHUNKS = {"quick": 40, "deep": 600}
WIKISOURCE_PAGES = 6
INDEX_VERSION = 1
STOP = set("""a o e é de do da dos das em no na nos nas um uma uns umas que se por para com como mas ou ao aos à às
ele ela eles elas seu sua seus suas não mais muito isso este esta esse essa the of and to in is it that for on as
with was are be by this an at or from his her he she they not but which have has had were their its""".split())


# ------------------------------------------------------------------ text -> chunks -> vectors


def _stems(text: str) -> list[str]:
    return [w[:6] for w in re.findall(r"[a-z0-9]+", personas.fold(text)) if len(w) > 2 and w not in STOP]


def _h(tok: str) -> int:
    return int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=4).digest(), "little")


def embed(texts: list[str]) -> np.ndarray:
    """Hashed bag of stems + stem bigrams, sign-hashed, L2-normalised (float32, len(texts) x DIM)."""
    out = np.zeros((len(texts), DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        s = _stems(t)
        for tok in s + [f"{a}_{b}" for a, b in zip(s, s[1:])]:
            h = _h(tok)
            out[i, h % DIM] += 1.0 if (h >> 16) & 1 else -1.0
        n = float(np.linalg.norm(out[i]))
        if n:
            out[i] /= n
    return out


def chunk(text: str, *, source: str, url: str, kind: str, size: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP) -> list[dict[str, Any]]:
    """Word windows over paragraphs (short paragraphs are merged)."""
    words = " ".join(p.strip() for p in re.split(r"\n\s*\n", text) if len(p.split()) >= 5).split()
    out, step = [], max(1, size - overlap)
    for k in range(0, max(1, len(words) - overlap), step):
        piece = " ".join(words[k:k + size])
        if len(piece.split()) >= 20:
            out.append({"text": piece, "source": source, "url": url, "kind": kind})
    return out


# ------------------------------------------------------------------ sources


def _site(lang: str, family: str) -> str:
    return f"{personas.SITES.get(lang, 'pt')}.{family}.org"


def _extract(site: str, title: str, *, intro: bool) -> tuple[str, str] | None:
    params: dict[str, Any] = {"action": "query", "prop": "extracts", "explaintext": 1, "titles": title, "redirects": 1}
    if intro:
        params["exintro"] = 1
    pages = personas._api(site, **params).get("query", {}).get("pages") or []
    page = pages[0] if pages else {}
    text = page.get("extract") or ""
    return (page.get("title", title), text) if text.strip() else None


def _wikipedia_title(name: str, lang: str, sheet: dict[str, Any]) -> str | None:
    if sheet.get("title"):
        return str(sheet["title"])
    hits = personas._api(_site(lang, "wikipedia"), action="opensearch", search=name, limit=1, namespace=0)
    return hits[1][0] if hits and hits[1] else None


def _wikisource_texts(name: str, lang: str, limit: int) -> list[tuple[str, str, str]]:
    """(title, url, plain text) of works by ``name`` on Wikisource (best effort)."""
    site = _site(lang, "wikisource")
    res = personas._api(site, action="query", list="search", srsearch=name, srlimit=limit * 2, srnamespace=0)
    out = []
    for hit in res.get("query", {}).get("search", [])[: limit * 2]:
        page = personas._page(site, hit["title"])
        if not page:
            continue
        text = personas.clean(page[1])
        if len(text.split()) >= 80 and personas.fold(name).split()[-1] in personas.fold(page[1][:4000]):
            out.append((page[0], personas.wiki_url(site, page[0]), text))
        if len(out) >= limit:
            break
    return out


def collect(name: str, lang: str, mode: str, sheet: dict[str, Any]) -> list[dict[str, Any]]:
    """Chunks for one character. Network; each source fails independently."""
    chunks: list[dict[str, Any]] = []
    for q in sheet.get("quotes") or []:  # the persona's own lines are always retrievable
        chunks.append({"text": q["text"], "source": q.get("source", ""), "url": q.get("url", sheet.get("wikiquote", "")),
                       "kind": "quote", "quoteId": q.get("id")})
    wp = _site(lang, "wikipedia")
    try:
        title = _wikipedia_title(name, lang, sheet)
        got = _extract(wp, title, intro=mode == "quick") if title else None
        if got:
            chunks += chunk(got[1], source=f"Wikipedia: {got[0]}", url=personas.wiki_url(wp, got[0]), kind="wikipedia")
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("knowledge %r wikipedia: %s", name, exc)
    if mode == "deep":
        try:
            page = personas.find_page(name, lang)
            if page:
                wq = _site(lang, "wikiquote")
                chunks += chunk(personas.clean(page[1]), source=f"Wikiquote: {page[0]}", url=personas.wiki_url(wq, page[0]), kind="wikiquote")
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("knowledge %r wikiquote: %s", name, exc)
        try:
            for title, url, text in _wikisource_texts(name, lang, WIKISOURCE_PAGES):
                chunks += chunk(text, source=f"Wikisource: {title}", url=url, kind="wikisource")
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("knowledge %r wikisource: %s", name, exc)
    return chunks[: MAX_CHUNKS[mode]]


# ------------------------------------------------------------------ cache


def paths(name: str, lang: str, mode: str) -> tuple[Path, Path]:
    base = personas.cache_path(name, lang).with_suffix("")
    return base.with_name(f"{base.name}.{mode}.json"), base.with_name(f"{base.name}.{mode}.npy")


def load(name: str, lang: str, mode: str) -> tuple[dict[str, Any], np.ndarray] | None:
    meta_p, vec_p = paths(name, lang, mode)
    if not (meta_p.is_file() and vec_p.is_file()):
        return None
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    if meta.get("version") != INDEX_VERSION:
        return None
    return meta, np.load(vec_p)


def build(name: str, lang: str = "pt-BR", mode: str = "quick", *, sheet: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build and cache the index; returns its metadata (without chunk texts)."""
    mode = mode if mode in MODES else "quick"
    sheet = sheet if sheet is not None else personas.load(name, lang, fetch=settings.personas_online())
    chunks = collect(name, lang, mode, sheet) if settings.personas_online() else [
        {"text": q["text"], "source": q.get("source", ""), "url": q.get("url", ""), "kind": "quote", "quoteId": q.get("id")}
        for q in sheet.get("quotes") or []]
    vecs = embed([c["text"] for c in chunks])
    kinds: dict[str, int] = {}
    for c in chunks:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    meta = {"version": INDEX_VERSION, "name": name, "lang": lang, "mode": mode, "builtAt": int(time.time()),
            "chunks": chunks, "counts": kinds, "license": "Wikipedia/Wikiquote/Wikisource, CC BY-SA 4.0 (Wikisource: domínio público na maioria)"}
    meta_p, vec_p = paths(name, lang, mode)
    meta_p.parent.mkdir(parents=True, exist_ok=True)
    np.save(vec_p, vecs)
    meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return summary(meta)


def summary(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: meta[k] for k in ("name", "lang", "mode", "builtAt", "counts")} | {"chunks": len(meta["chunks"])}


def ensure(name: str, lang: str, mode: str, *, sheet: dict[str, Any] | None = None) -> tuple[dict[str, Any], np.ndarray]:
    """Cached index, building it on a miss. ``deep`` falls back to a cached ``quick`` only if the build fails."""
    got = load(name, lang, mode)
    if got is None:
        try:
            build(name, lang, mode, sheet=sheet)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("knowledge %r %s: %s", name, mode, exc)
            if mode == "deep":
                return ensure(name, lang, "quick", sheet=sheet)
        got = load(name, lang, mode) or ({"mode": mode, "chunks": []}, np.zeros((0, DIM), np.float32))
    return got


def retrieve(index: tuple[dict[str, Any], np.ndarray], query: str, k: int = 4, *, exclude_quotes: bool = True) -> list[dict[str, Any]]:
    """Top-k passages for ``query`` (cosine on the hashed vectors); quotes are left to the quote sheet."""
    meta, vecs = index
    if not len(vecs):
        return []
    scores = vecs @ embed([query])[0]
    out = []
    for j in np.argsort(-scores):
        c = meta["chunks"][int(j)]
        if exclude_quotes and c["kind"] == "quote":
            continue
        if scores[j] <= 0:
            break
        out.append({**c, "score": round(float(scores[j]), 3), "id": f"p{int(j)}"})
        if len(out) >= k:
            break
    return out


def copies_passage(text: str, passages: list[dict[str, Any]], n: int = 7) -> bool:
    """True when a written line copies a run of ``n`` words from a passage (passages are not quotes)."""
    t = personas.fold(text).split()
    grams = {" ".join(t[i:i + n]) for i in range(len(t) - n + 1)}
    return any(g in personas.fold(p["text"]) for p in passages for g in grams)


# ------------------------------------------------------------------ deep jobs

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _key(name: str, lang: str) -> str:
    return f"{lang}:{personas.slug(name)}"


def status(name: str, lang: str = "pt-BR") -> dict[str, Any]:
    """Deep index state for a persona: ready (cached), building/failed (job) or missing."""
    name = personas.persona_name(name)
    got = load(name, lang, "deep")
    if got is not None:
        return {"name": name, "lang": lang, "status": "ready", **summary(got[0])}
    with _LOCK:
        job = next((dict(j) for j in _JOBS.values() if j["key"] == _key(name, lang)), None)
    return job or {"name": name, "lang": lang, "status": "missing"}


def _run(job_id: str, name: str, lang: str) -> None:
    try:
        meta = build(name, lang, "deep")
        with _LOCK:
            _JOBS[job_id].update(status="ready", progress=1.0, **meta)
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("deep knowledge %s failed", job_id)
        with _LOCK:
            _JOBS[job_id].update(status="failed", error=str(exc)[:300])


def submit_deep(name: str, lang: str = "pt-BR") -> dict[str, Any]:
    """Start (or join) a background deep build; ready caches return at once."""
    name = personas.persona_name(name)
    if not name:
        raise ValueError("name is required")
    cur = status(name, lang)
    if cur["status"] in {"ready", "building"}:
        return cur
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {"id": job_id, "key": _key(name, lang), "name": name, "lang": lang, "status": "building", "progress": 0.1}
    threading.Thread(target=_run, args=(job_id, name, lang), daemon=True).start()
    return dict(_JOBS[job_id])


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None
