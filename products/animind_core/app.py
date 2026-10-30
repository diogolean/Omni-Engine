"""FastAPI app. Run: uvicorn products.animind_core.app:app --port 8787"""
from __future__ import annotations

import hmac
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from . import API_VERSION, characters, knowledge, render, settings

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
    return {"ok": True, "version": API_VERSION, "generator": "gemini" if settings.gemini_available() else "mock"}


@app.post("/v1/characters", dependencies=[Depends(require_key)])
def post_characters(body: dict, request: Request) -> dict:
    try:
        return characters.create_character(body, _base(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc)) from exc


@app.post("/v1/render", dependencies=[Depends(require_key)])
def post_render(body: dict, request: Request) -> dict:
    return render.submit(body, _base(request))


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(require_key)])
def get_job(job_id: str) -> dict:
    job = render.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


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
def get_file(path: str) -> FileResponse:
    """Generated art and media. Ids are random; paths are confined to the data dir."""
    root = settings.data_dir().resolve()
    for sub in ("assets", ""):
        target = (root / sub / path).resolve()
        if target.is_relative_to(root) and target.is_file() and target.suffix in {".png", ".json", ".wav", ".mp4", ".mp3"}:
            return FileResponse(target)
    raise HTTPException(404, "not found")
