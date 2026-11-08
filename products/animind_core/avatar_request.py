"""Public ``generate_avatar`` facade. It does not call ``avatar.create_avatar``.

There is no ``generate_avatar`` in the factory. This plans one or two people
from the prompt and, when the click is logged in, records a background job.
Paid generation stays off, so the job never spends. A supplied plate is cut
with the existing portrait key; that cut is real, the missing model image is not.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from typing import Any

from PIL import Image

from . import portrait, settings

_USER_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_NAME = re.compile(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w'’.-]+(?:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w'’.-]+){0,2}\b")
_ONE = ("monologue", "monólogo", "monologo", "presentation", "apresentação", "apresentacao", "speech", "discurso", "palestra")
_TWO = ("debate",)
_INANIMATE = frozenset("""
cadeira mesa mar praia palco cenario cenário fundo floresta cidade livro copo carro casa
janela lua sol ceu céu montanha rio objeto plateia publico público
chair table sea beach stage background scenery forest city book cup car house window
moon sun sky mountain river object audience set
""".split())
_SKIP = frozenset(
    "uma um uns umas the an de do da dos das em no na nos nas entre and or vs sobre about with para for".split()
)
_STYLES = ("ghibli", "cartoon", "stick", "southpark", "anime", "retro", "realista", "cyberpunk", "aquarela")
_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}


class AvatarRefused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def accept_user(header: str | None) -> str | None:
    """Same opaque id as the job owner: email refused, blank is logged out."""
    raw = (header or "").strip()
    if not raw:
        return None
    if "@" in raw or not _USER_ID.match(raw):
        raise ValueError("user")
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def infer(prompt: str, *, language: str = "pt", requested_resolution: str = "1k") -> dict[str, Any]:
    """Structured plan. Missing people stay missing; scenery is not a persona."""
    text = " ".join((prompt or "").split())
    folded = text.casefold()
    one = _has(folded, _ONE)
    two = _has(folded, _TWO)
    if one and two:
        intent, needed = None, None
    elif two:
        intent, needed = "debate", 2
    elif one:
        intent, needed = "monologue", 1
    else:
        intent, needed = None, None
    lang = "pt" if language.strip().lower() in {"pt", "pt-br", "pt-br"} else (
        "en" if language.strip().lower() in {"en", "en-us"} else None
    )
    names = _people(text)
    reasons: list[str] = []
    if lang is None:
        reasons.append("language")
    if intent is None:
        reasons.append("intent")
    elif needed == 2 and len(names) < 2:
        reasons.append("missing_persona")
    elif needed == 1 and len(names) != 1:
        reasons.append("persona_count")
    elif needed == 1 and not names:
        reasons.append("missing_persona")
    style = _style(folded)
    resolution = portrait.plan({"requestedResolution": requested_resolution})
    return {
        "prompt": text[:400],
        "language": lang,
        "intent": intent,
        "personas": names,
        "needed": needed,
        "style": style,
        "resolution": resolution,
        "needsChoice": bool(reasons),
        "reasons": reasons,
        "consume": False,
        "liveDebate": False,
        "fixture": "plan",
    }


def generate_avatar(
    body: dict[str, Any],
    user: str | None,
    *,
    login: str | None = None,
    plates: dict[str, Image.Image] | None = None,
) -> dict[str, Any]:
    """Authorized click records one background job. No login, no job, no spend."""
    if not user:
        raise AvatarRefused("login")
    plan = infer(
        str(body.get("prompt") or ""),
        language=str(body.get("language") or body.get("lang") or "pt"),
        requested_resolution=str(body.get("requestedResolution") or body.get("resolution") or "1k"),
    )
    plan["entitlement"] = "server" if portrait.server_premium(login) else "absent"
    if plan["needsChoice"]:
        return {"status": "needs_choice", "plan": _public_plan(plan), "charged": False, "providerCalls": 0}
    key = _idempotency(user, plan)
    with _LOCK:
        existing = _JOBS.get(key)
        if existing is not None:
            existing["retries"] = int(existing.get("retries") or 0) + 1
            return _public(existing)
        job_id = hashlib.sha256(key.encode()).hexdigest()[:12]
        job = {
            "id": job_id,
            "status": "blocked",
            "progress": 0.0,
            "execution": "background",
            "plan": plan,
            "charged": False,
            "providerCalls": 0,
            "retries": 0,
            "liveImage": False,
            "cutout": "not_run",
            "_owner": user,
            "_key": key,
            "participants": [
                {"name": name, "status": "blocked", "idempotency": _participant_key(key, name)}
                for name in plan["personas"]
            ],
        }
        if plates:
            _cut_fixtures(job, plates)
        _JOBS[key] = job
        _save(job)
        return _public(job)


def get(job_id: str, user: str | None) -> dict[str, Any] | None:
    with _LOCK:
        for job in _JOBS.values():
            if job["id"] == job_id and job.get("_owner") == user:
                return _public(job)
    return None


def _cut_fixtures(job: dict[str, Any], plates: dict[str, Image.Image]) -> None:
    """Real cutout of a labeled plate. Still not a model image."""
    root = settings.data_dir() / "users" / job["_owner"] / "avatars" / job["id"]
    ready = 0
    for person in job["participants"]:
        plate = plates.get(person["name"])
        if plate is None:
            person["status"] = "blocked"
            continue
        result = portrait.extract(plate, root / person["idempotency"][:8])
        person["status"] = "ready" if result["ready"] else "rejected"
        person["qaReady"] = result["ready"]
        person["cutout"] = "real"
        person["liveImage"] = False
        if result["ready"]:
            ready += 1
    job["cutout"] = "real"
    job["fixture"] = "plate"
    if ready == len(job["participants"]):
        job["status"] = "ready"
        job["progress"] = 1.0
    elif ready == 0:
        job["status"] = "rejected"
    else:
        job["status"] = "partial"
        job["progress"] = ready / len(job["participants"])
    job["providerCalls"] = 0
    job["charged"] = False


def _people(text: str) -> list[str]:
    found: list[str] = []
    for match in _NAME.finditer(text):
        parts = []
        for word in match.group(0).split():
            if word.casefold() in _INANIMATE or word.casefold() in _SKIP:
                continue
            if word.casefold() in _ONE or word.casefold() in _TWO or word.casefold() in _STYLES:
                continue
            parts.append(word)
        name = " ".join(parts).strip(" .,-")
        if name and name not in found:
            found.append(name)
    return found[:3]


def _style(folded: str) -> str | None:
    for style_id in _STYLES:
        if re.search(rf"(?<!\w){style_id}(?!\w)", folded):
            return style_id
    return None


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) for word in words)


def _idempotency(user: str, plan: dict[str, Any]) -> str:
    payload = {
        "user": user,
        "intent": plan["intent"],
        "personas": plan["personas"],
        "style": plan["style"],
        "resolution": plan["resolution"]["productPreset"],
        "language": plan["language"],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _participant_key(plan_key: str, name: str) -> str:
    return hashlib.sha256(f"{plan_key}:{name}".encode()).hexdigest()[:16]


def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if key != "prompt"}


def _public(job: dict[str, Any]) -> dict[str, Any]:
    out = {key: value for key, value in job.items() if not str(key).startswith("_")}
    out["plan"] = _public_plan(job["plan"])
    return out


def _save(job: dict[str, Any]) -> None:
    path = settings.data_dir() / "users" / job["_owner"] / "avatars" / job["id"] / "job.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_public(job), ensure_ascii=False), encoding="utf-8")
