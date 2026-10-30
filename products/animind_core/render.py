"""POST /v1/render + GET /v1/jobs/{id}.

Per line: edge-tts voice -> Rhubarb visemes (``animation.parametric.rhubarb``,
envelope fallback) with absolute times. The v2 rigs made by this core are
composited by :mod:`products.animind_core.scene` and encoded by the Rigly
renderer (``animation.parametric.renderer.AnimationRenderer``) into
``jobs/<id>/preview.mp4``. Characters the core did not make (photo rigs, mock
placeholders from the app) are drawn as a neutral silhouette.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import threading
import uuid
import wave
from pathlib import Path
from typing import Any

import numpy as np

from . import characters, rig, scene, settings

_LOG = logging.getLogger("animind_core")
PARODY_DISCLAIMER = "Paródia • Gerado por IA"
VOICES = {
    "pt-BR": ("pt-BR-AntonioNeural", "pt-BR-FranciscaNeural"),
    "en-US": ("en-US-GuyNeural", "en-US-JennyNeural"),
}
SAMPLE_RATE = 24000
GAP_S = 0.3
ENV_FPS = 100
_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_RENDER_LOCK = threading.Lock()


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
        return [
            (0, f"Hoje vamos aprender sobre {topic}." if pt else f"Today we learn about {topic}."),
            (0, "Prestem atenção, porque isso cai na prova!" if pt else "Pay attention, this will be on the test!"),
        ]
    return [
        (0, f"Olá! Vamos falar de {topic}." if pt else f"Hello! Let's talk about {topic}."),
        (0, "Fique comigo até o final." if pt else "Stay with me until the end."),
    ]


def line_view(template: str, speaker: int, index: int) -> str:
    """Debate speakers face each other; solo/lesson turn on every other line."""
    if template == "debate":
        return "right" if speaker == 0 else "left"
    return "front" if index % 2 == 0 else "right"


def role_line(char: dict[str, Any], template: str, topic: str, lang: str) -> str:
    """Lower-third second line: the photo's source when there is one, else the scene and topic."""
    attr = (char.get("photo") or {}).get("attribution") or {}
    if attr.get("author"):
        return f"{'Foto' if lang == 'pt-BR' else 'Photo'}: {attr['author']} · {attr.get('license', '')}".strip(" ·")
    kind = {"debate": ("Debate", "Debate"), "lesson": ("Aula", "Lesson")}.get(template, ("Fala", "Talk"))
    return f"{kind[0] if lang == 'pt-BR' else kind[1]} · {topic}"[:60]


async def _tts(text: str, voice: str, out: Path) -> None:
    import edge_tts  # noqa: PLC0415

    await edge_tts.Communicate(text, voice).save(str(out))


def _to_wav(src: Path, dst: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ar", str(SAMPLE_RATE), "-ac", "1", "-sample_fmt", "s16", str(dst)],
                   check=True, timeout=120)


def _read_pcm(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def envelope(pcm: np.ndarray, fps: int = ENV_FPS) -> np.ndarray:
    """RMS per 1/fps s, normalised to 0..1."""
    hop = SAMPLE_RATE // fps
    n = max(1, int(np.ceil(len(pcm) / hop)))
    padded = np.zeros(n * hop, dtype=np.float32)
    padded[: len(pcm)] = pcm.astype(np.float32) / 32768.0
    env = np.sqrt((padded.reshape(n, hop) ** 2).mean(axis=1))
    return env / max(float(env.max()), 1e-6)


def line_visemes(clip: Path, text: str, offset_s: float) -> tuple[list[dict[str, Any]], str]:
    """Rhubarb cues for one line, shifted to track time. Returns (cues, source)."""
    from animation.parametric import rhubarb  # noqa: PLC0415

    cues = rhubarb.analyze_visemes(clip, dialog_text=text, allow_download=False)
    source = "rhubarb"
    if not cues:
        cues = rhubarb.visemes_from_envelope(envelope(_read_pcm(clip)).tolist(), ENV_FPS)
        source = "envelope"
    return [{"t": round(offset_s + float(t), 3), "v": str(v)} for t, v in cues], source


def _set(job_id: str, **kw: Any) -> None:
    with _LOCK:
        _JOBS[job_id].update(kw)


def _rig_views(char: dict[str, Any], template: str, idx: int, scenario: str = "studio") -> dict[str, scene.RigView] | None:
    saved = characters.load(str(char.get("id") or ""))
    if not saved:
        return None
    skin = characters.puppets_dir() / saved["id"]
    out = {}
    for view in ("front", "left", "right"):
        data = rig.load_view(skin, view)
        out[view] = scene.load_rig_view(data, scene.fit_scale(data["canvas"]["h"], template, idx),
                                         scene.light(scenario, template, idx))
    return out


def _run(job_id: str, req: dict[str, Any], base_url: str) -> None:
    from animation.parametric.renderer import AnimationRenderer  # noqa: PLC0415

    try:
        _set(job_id, status="running", progress=0.05)
        chars = req.get("characters") or []
        if not chars:
            raise ValueError("characters is required")
        template = req.get("template") if req.get("template") in scene.PLACES else "solo"
        scenario = req.get("scenario") if req.get("scenario") in scene.SCENARIOS else "studio"
        lang = req.get("lang") if req.get("lang") in VOICES else "pt-BR"
        topic = (req.get("topic") or "inteligência artificial").strip()[:200]
        work = settings.data_dir() / "jobs" / job_id
        work.mkdir(parents=True, exist_ok=True)
        lines = default_script(template, topic, len(chars), lang)
        voices = VOICES[lang]
        gap = np.zeros(int(GAP_S * SAMPLE_RATE), dtype=np.int16)
        pcm_parts, script, sources, t = [], [], set(), 0.0
        for i, (speaker, text) in enumerate(lines):
            mp3, clip = work / f"line_{i}.mp3", work / f"line_{i}.wav"
            asyncio.run(_tts(text, voices[speaker % 2], mp3))
            _to_wav(mp3, clip)
            pcm = _read_pcm(clip)
            dur = len(pcm) / SAMPLE_RATE
            cues, src = line_visemes(clip, text, t)
            sources.add(src)
            script.append({"speaker": speaker, "text": text, "startMs": int(t * 1000), "endMs": int((t + dur) * 1000),
                           "visemes": cues, "view": line_view(template, speaker, i)})
            pcm_parts += [pcm, gap]
            t += dur + GAP_S
            _set(job_id, progress=0.1 + 0.4 * (i + 1) / len(lines))
        track = np.concatenate(pcm_parts)
        audio = work / "voice.wav"
        with wave.open(str(audio), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(track.tobytes())
        duration = len(track) / SAMPLE_RATE
        n = 2 if template == "debate" else 1
        cast = [chars[min(i, len(chars) - 1)] for i in range(n)]
        rigs = [_rig_views(c, template, i, scenario) for i, c in enumerate(cast)]
        sc = scene.Scene(template=template, scenario=scenario, rigs=rigs, script=script, envelope=envelope(track),
                         env_fps=ENV_FPS, duration_s=duration, disclaimer=PARODY_DISCLAIMER,
                         names=[str(c.get("name") or "") for c in cast], roles=[role_line(c, template, topic, lang) for c in cast])
        video = work / "preview.mp4"
        _set(job_id, progress=0.55)
        with _RENDER_LOCK:
            AnimationRenderer(width=scene.W, height=scene.H, fps=scene.FPS).render(
                frame_iter=sc.frames(), audio_path=audio, output_path=video, duration_s=duration,
                preset="veryfast", crf=20, gop=48, tune="animation", threads=2, deterministic=True,
                encode_timeout_s=900, retain_frames=False,
            )
        files = f"{base_url}/v1/files/jobs/{job_id}"
        _set(job_id, status="done", progress=1.0, result={
            "template": template, "scenario": scenario, "characters": chars,
            "script": script, "durationMs": int(duration * 1000), "audioUrl": f"{files}/voice.wav",
            "videoUrl": f"{files}/preview.mp4", "disclaimer": PARODY_DISCLAIMER,
            # Additive: which viseme source ran, and which characters had a core rig.
            "visemeSource": sorted(sources), "rigged": [r is not None for r in rigs],
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
