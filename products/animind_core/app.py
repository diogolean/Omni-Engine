"""FastAPI app. Run: uvicorn products.animind_core.app:app --port 8787"""
from __future__ import annotations

import hmac
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from . import (
    API_VERSION,
    avatar_request,
    casting,
    characters,
    debate,
    illustrated_scene,
    knowledge,
    languages,
    owner,
    personas,
    portrait,
    preview,
    render,
    scene,
    settings,
    stick,
    styles,
)
from .spend.contract import publish as publish_usage
from .spend.http import protect
from .spend.plan import build as build_plan

app = FastAPI(title="Animind core", version="1.0.0")


def require_key(request: Request) -> None:
    key = settings.api_key()
    if not key:
        raise HTTPException(503, "CORE_API_KEY is not configured")
    got = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or request.headers.get("x-api-key", "")
    if not hmac.compare_digest(got, key):
        raise HTTPException(401, "invalid API key")


def _base(request: Request) -> str:
    return (os.getenv("CORE_PUBLIC_URL") or str(request.base_url)).rstrip("/")


@app.get("/v1/health")
def health() -> dict:
    gen = "gemini" if settings.gemini_available() else "mock"
    out = {"ok": True, "version": API_VERSION, "generator": gen,
           # what this process can render: a stale server (older checkout) is visible before a render falls back
           "scenarios": list(scene.SCENARIOS), "templates": list(scene.PLACES),
           "futureModes": [illustrated_scene.capability()]}
    if settings.gemini_dead_reason():  # a 402 / depleted credit seen in this process: art falls back to mock
        out["generatorNote"] = f"Gemini indisponível: {settings.gemini_dead_reason()[:200]}"
    return out


@app.post("/v1/plan", dependencies=[Depends(require_key)])
def post_plan(body: dict, request: Request) -> dict:
    """Local planner. It does not call a model. Provider calls stay inside the guard."""
    def run() -> dict:
        try:
            return build_plan(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    return protect(request, body, "plan", run)


@app.post("/v1/characters/{character_id}/eyes", dependencies=[Depends(require_key)])
def post_character_eyes(character_id: str) -> dict:
    """Probe a saved rig. Writes only eye_control.json and never calls an image provider."""
    from .eye_control import upgrade_character  # noqa: PLC0415

    try:
        return upgrade_character(character_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "unknown character") from exc


@app.post("/v1/characters/{character_id}/front", dependencies=[Depends(require_key)])
def post_character_front(character_id: str, request: Request) -> dict:
    """Redraw a character's front view (checked for frontality, one retry within the Gemini cap)."""
    def run() -> dict:
        try:
            saved = characters.regenerate_front(character_id, _base(request))
        except NotImplementedError as exc:
            raise HTTPException(501, str(exc)) from exc
        if saved is None:
            raise HTTPException(404, "unknown character")
        return saved

    return protect(request, {"characterId": character_id}, "character", run)


@app.post("/v1/characters/{character_id}/rerig", dependencies=[Depends(require_key)])
def post_character_rerig(character_id: str, request: Request) -> dict:
    """Rebuild a saved character on the current layered rig from its stored shots (no image API cost)."""
    try:
        saved = characters.rerig(character_id, _base(request))
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc)) from exc
    if saved is None:
        raise HTTPException(404, "unknown character")
    return saved


@app.post("/v1/characters", dependencies=[Depends(require_key)])
def post_characters(body: dict, request: Request) -> dict:
    def run() -> dict:
        try:
            return characters.create_character(body, _base(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except NotImplementedError as exc:
            raise HTTPException(501, str(exc)) from exc

    try:
        return protect(request, body, "character", run)
    except portrait.PortraitRefused as exc:
        raise HTTPException(409, exc.decision) from exc


@app.post("/v1/portraits/plan", dependencies=[Depends(require_key)])
def post_portrait_plan(body: dict, request: Request) -> dict:
    """Resolution contract. It does not generate. Usage cost stays null."""
    result = protect(request, body, "portrait", lambda: portrait.plan(body))
    result["usage"] = publish_usage(result.get("usage"))
    return result


@app.post("/v1/avatars/plan", dependencies=[Depends(require_key)])
def post_avatar_plan(body: dict, request: Request) -> dict:
    """Infer one or two people. Does not generate or start a job."""
    def run() -> dict:
        return avatar_request.infer(
            str(body.get("prompt") or ""),
            language=str(body.get("language") or body.get("lang") or "pt"),
            requested_resolution=str(body.get("requestedResolution") or "1k"),
        )

    result = protect(request, body, "avatar-plan", run)
    result["usage"] = publish_usage(result.get("usage"))
    return result


@app.post("/v1/avatars", dependencies=[Depends(require_key)])
def post_avatar(body: dict, request: Request) -> dict:
    """Background job after a logged-in click. No user, no spend."""
    try:
        user = avatar_request.accept_user(request.headers.get("x-animind-user"))
    except ValueError as exc:
        raise HTTPException(400, "user") from exc
    if not user:
        raise HTTPException(403, "login")

    def run() -> dict:
        return avatar_request.generate_avatar(
            body, user, login=request.headers.get("x-animind-user"),
        )

    try:
        result = protect(request, body, "avatar", run)
    except avatar_request.AvatarRefused as exc:
        raise HTTPException(403, exc.code) from exc
    result["usage"] = publish_usage(result.get("usage"), job_id=result.get("id"))
    if result.get("id") and user:
        result["usage"] = avatar_request.keep_usage(user, result["id"], result["usage"])
    return result


@app.get("/v1/avatars/{job_id}", dependencies=[Depends(require_key)])
def get_avatar(job_id: str, request: Request) -> dict:
    try:
        user = avatar_request.accept_user(request.headers.get("x-animind-user"))
    except ValueError as exc:
        raise HTTPException(400, "user") from exc
    found = avatar_request.get(job_id, user)
    if found is None:
        raise HTTPException(404, "avatar not found")
    return found


@app.post("/v1/render", dependencies=[Depends(require_key)])
def post_render(body: dict, request: Request) -> dict:
    try:
        user = owner.accept_user(request.headers.get("x-animind-user"))
    except ValueError as exc:
        raise HTTPException(400, "user") from exc
    payload = dict(body)
    payload.pop("personaUser", None)
    payload.pop("owner", None)
    if not user:
        raise HTTPException(404, "job not found")
    payload["personaUser"] = user
    payload["owner"] = user
    result = protect(request, body, "render", lambda: render.submit(payload, _base(request)))
    if result.get("id"):
        result["usage"] = render.keep_usage(
            user, str(result["id"]), publish_usage(result.get("usage"), job_id=str(result["id"])),
        )
    return result


@app.post("/v1/debates", dependencies=[Depends(require_key)])
def post_debate(body: dict, request: Request) -> dict:
    """A fixture debate script. Paid generation stays off, so the writer is never a live model."""
    try:
        user = owner.accept_user(request.headers.get("x-animind-user"))
    except ValueError as exc:
        raise HTTPException(400, "user") from exc
    payload = dict(body)
    payload.pop("owner", None)
    try:
        return debate.create(payload, user)
    except debate.SheetDenied as exc:
        raise HTTPException(404, "not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, "debate") from exc


@app.get("/v1/debates/{debate_id}", dependencies=[Depends(require_key)])
def get_debate(debate_id: str, request: Request) -> dict:
    try:
        user = owner.accept_user(request.headers.get("x-animind-user"))
    except ValueError as exc:
        raise HTTPException(400, "user") from exc
    found = debate.get(debate_id, user)
    if found is None:
        raise HTTPException(404, "debate not found")
    return found


@app.post("/v1/preview", dependencies=[Depends(require_key)])
def post_preview(body: dict, request: Request) -> dict:
    """PNG preview the app requests before the full character rig exists."""
    def run() -> dict:
        try:
            return preview.build_preview(body, _base(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    return protect(request, body, "preview", run)


@app.post("/v1/scenarios", dependencies=[Depends(require_key)])
def post_scenarios(body: dict) -> dict:
    """Map a cast or a scenery prompt onto a built-in plate id."""
    try:
        return preview.resolve_scenario(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(require_key)])
def get_job(job_id: str, request: Request) -> dict:
    try:
        user = owner.accept_user(request.headers.get("x-animind-user"))
    except ValueError as exc:
        raise HTTPException(400, "user") from exc
    if not user:
        raise HTTPException(404, "job not found")
    job = render.get(job_id, user)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.get("/v1/styles")
def get_styles() -> dict:
    """Animation styles accepted as ``artStyle`` (characters) and ``style`` (render)."""
    return {"styles": styles.catalog(), "default": styles.DEFAULT}


@app.get("/v1/tones")
def get_tones() -> dict:
    """Script tones accepted as ``tone`` (render)."""
    return {"tones": styles.tone_catalog(), "default": styles.DEFAULT_TONE}


@app.get("/v1/objectives")
def get_objectives() -> dict:
    """Script objectives accepted as ``objective`` (render)."""
    return {"objectives": styles.objective_catalog(), "default": styles.DEFAULT_OBJECTIVE}


@app.get("/v1/languages")
def get_languages() -> dict:
    """Built-in locale profiles; any BCP-47 tag is accepted by render."""
    return {
        "languages": [
            {"language": p.tag, "script": p.script, "rhubarbRecognizer": p.rhubarb}
            for p in languages.PROFILES.values()
        ],
        "default": "en-US",
        "openBcp47": True,
    }


@app.get("/v1/voices")
def get_voices(lang: str | None = None) -> dict:
    """All Edge locales when unfiltered, or gendered voices for one locale."""
    language = languages.normalize(lang) if lang else None
    try:
        voices = casting.voice_catalog(language)
    except ValueError as exc:
        raise HTTPException(503 if lang is None else 400, str(exc)) from exc
    locale_names = {}
    for voice in voices:
        locale_names.setdefault(voice["language"], voice["languageName"])
    return {
        "language": language,
        "languages": [
            {"language": tag, "name": name}
            for tag, name in sorted(locale_names.items())
        ],
        "voices": voices,
    }


@app.get("/v1/stick-presets")
def get_stick_presets() -> dict:
    """Reusable procedural presets; palette and accessory remain overridable."""
    return {
        "presets": [
            {"id": key, **value} for key, value in stick.PRESETS.items()
        ],
        "default": "teacher",
        "customizable": ["palette.skin", "palette.outfit", "clothing.color", "accessory"],
    }


@app.get("/v1/personas/sheet", dependencies=[Depends(require_key)])
def get_sheet(name: str, request: Request, lang: str = "pt-BR") -> dict:
    """The persona's quote sheet. A quote is verified only when its text is on the source page."""
    nm = personas.persona_name(name)
    if not nm:
        raise HTTPException(400, "name required")
    try:
        user = personas.accept_user(request.headers.get("x-animind-user"))
    except ValueError as exc:
        raise HTTPException(400, "user") from exc
    with personas.user_scope(user):
        sheet = personas.load(nm, lang, fetch=settings.personas_online())
    if not sheet.get("found"):
        raise HTTPException(404, "not found")
    keep = ("id", "text", "source", "work", "sourceKind", "url", "wikisource", "verification", "sourceSite")
    body = {
        "name": nm,
        "lang": lang,
        "found": True,
        "wikiquote": sheet.get("wikiquote"),
        "license": sheet.get("license"),
        "quotes": [{k: q[k] for k in keep if k in q} for q in sheet.get("quotes") or []],
    }
    if "fixture" in sheet:
        body["fixture"] = sheet["fixture"]
    return body


@app.post("/v1/personas/deep", dependencies=[Depends(require_key)])
def post_deep(body: dict) -> dict:
    """Start (or join) the background deep-knowledge build for one persona."""
    try:
        return knowledge.submit_deep(str(body.get("name") or ""), body.get("lang") or "pt-BR")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/v1/personas/deep", dependencies=[Depends(require_key)])
def get_deep(name: str, lang: str = "pt-BR") -> dict:
    """Deep index state for a persona: ready, building, failed or missing."""
    return knowledge.status(name, lang)


@app.get("/v1/personas/jobs/{job_id}", dependencies=[Depends(require_key)])
def get_deep_job(job_id: str) -> dict:
    job = knowledge.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.get("/v1/files/{path:path}")
def get_file(path: str, request: Request) -> FileResponse:
    """Generated media. Files under a user hash are readable only by that user."""
    try:
        user = owner.accept_user(request.headers.get("x-animind-user"))
    except ValueError as exc:
        raise HTTPException(400, "user") from exc
    target = owner.readable_file(path, user)
    if target is None:
        raise HTTPException(404, "not found")
    return FileResponse(target)
