"""POST /v1/render + GET /v1/jobs/{id}: edge-tts voices, Rigly ``render_scene``."""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from . import settings

_LOG = logging.getLogger("animind_core")
PARODY_DISCLAIMER = "Paródia • Gerado por IA"
VOICES = {
    "pt-BR": ("pt-BR-AntonioNeural", "pt-BR-FranciscaNeural"),
    "en-US": ("en-US-GuyNeural", "en-US-JennyNeural"),
}
_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def default_script(template: str, topic: str, n_chars: int, lang: str) -> list[tuple[int, str]]:
    """Deterministic script so the pipeline runs with no LLM key."""
    pt = lang == "pt-BR"
    if template == "debate" and n_chars >= 2:
        return [
            (0, f"Eu defendo que {topic} muda tudo." if pt else f"I say {topic} changes everything."),
            (1, "Discordo. Mostre as provas." if pt else "I disagree. Show me the evidence."),
            (0, "As provas estão em toda parte." if pt else "The evidence is everywhere."),
        ]
    if template == "lesson":
        return [(0, f"Hoje vamos aprender sobre {topic}." if pt else f"Today we learn about {topic}.")]
    return [(0, f"Olá! Vamos falar de {topic}." if pt else f"Hello! Let's talk about {topic}.")]


async def _tts(text: str, voice: str, out: Path) -> None:
    import edge_tts  # noqa: PLC0415

    await edge_tts.Communicate(text, voice).save(str(out))


def _duration_s(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return float(out.stdout.strip())


def _set(job_id: str, **kw: Any) -> None:
    with _LOCK:
        _JOBS[job_id].update(kw)


def _run(job_id: str, req: dict[str, Any], base_url: str) -> None:
    from animation.parametric.scene_render import render_scene  # noqa: PLC0415

    try:
        _set(job_id, status="running", progress=0.05)
        chars = req.get("characters") or []
        if not chars:
            raise ValueError("characters is required")
        template = req.get("template") or "solo"
        lang = req.get("lang") or "pt-BR"
        topic = (req.get("topic") or "inteligência artificial").strip()[:200]
        work = settings.data_dir() / "jobs" / job_id
        work.mkdir(parents=True, exist_ok=True)
        lines = default_script(template, topic, len(chars), lang)
        voices = VOICES.get(lang, VOICES["pt-BR"])
        clips, script, t = [], [], 0.0
        for i, (speaker, text) in enumerate(lines):
            clip = work / f"line_{i}.mp3"
            asyncio.run(_tts(text, voices[speaker % 2], clip))
            dur = _duration_s(clip)
            clips.append(clip)
            script.append({"speaker": speaker, "text": text, "startMs": int(t * 1000), "endMs": int((t + dur) * 1000)})
            t += dur
            _set(job_id, progress=0.1 + 0.4 * (i + 1) / len(lines))
        concat = work / "concat.txt"
        concat.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
        audio = work / "voice.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-ar", "44100", "-ac", "1", str(audio)], check=True, timeout=120)
        ids = [str(c.get("id") or f"c{i}") for i, c in enumerate(chars)]
        spec_chars = []
        for i, c in enumerate(chars[:2]):
            skin = settings.data_dir() / "assets" / "puppets" / ids[i]
            entry = {"id": ids[i], "label": str(c.get("name") or ids[i])[:40], "facing": "right" if i == 0 else "left",
                     "accent": (c.get("palette") or {}).get("accent", "#7EB6D9" if i == 0 else "#E0A458")}
            if (skin / "puppet.json").is_file() and (skin / "puppet.json").read_text().find('"mock"') < 0:
                entry["puppet"] = str(skin)
            spec_chars.append(entry)
        spec = {
            "version": 1, "width": 1080, "height": 1920, "fps": 30, "seed": 7,
            "stage": "duo" if len(spec_chars) >= 2 else "solo", "lip_sync": "envelope",
            "background": {"color": "#141A22"}, "characters": spec_chars,
            "lines": [{"speaker": ids[s["speaker"]], "start": s["startMs"] / 1000, "end": s["endMs"] / 1000, "text": s["text"]} for s in script],
        }
        video = work / "preview.mp4"
        render_scene(spec, audio, video, preview=True, timeout_s=600)
        files = f"{base_url}/v1/files/jobs/{job_id}"
        _set(job_id, status="done", progress=1.0, result={
            "template": template, "scenario": req.get("scenario") or "studio", "characters": chars,
            "script": script, "durationMs": int(t * 1000), "audioUrl": f"{files}/voice.wav",
            "videoUrl": f"{files}/preview.mp4", "disclaimer": PARODY_DISCLAIMER,
        })
    except Exception as exc:  # noqa: BLE001 — surfaced on the job
        _LOG.exception("render job %s failed", job_id)
        _set(job_id, status="failed", error=str(exc)[:500])


def submit(req: dict[str, Any], base_url: str) -> dict[str, Any]:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed")
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {"id": job_id, "status": "queued", "progress": 0.0}
    threading.Thread(target=_run, args=(job_id, req, base_url), daemon=True).start()
    return dict(_JOBS[job_id])


def get(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None
