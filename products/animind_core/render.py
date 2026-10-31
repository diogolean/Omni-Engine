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
import json
import logging
import shutil
import subprocess
import threading
import uuid
import wave
from pathlib import Path
from typing import Any

import numpy as np

from . import casting, characters, frameqa, rig, scene, settings, styles
from . import script as scripts

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


FAST_PCT = 15  # re-voice a line that overruns its beat budget (on top of the cast pace)


CAMERA_BEATS = ("gancho", "virada", "fechamento", "resumo", "síntese")


def line_view(template: str, speaker: int, index: int, beat: str = "") -> str:
    """Hook, the debate's turn and closing lines address the camera (front view); otherwise debate speakers face
    each other and solo/lesson turn on every other line."""
    if beat in CAMERA_BEATS:
        return "front"
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


async def _tts(text: str, voice: str, out: Path, rate: str = "+0%", pitch: str = "+0Hz") -> None:
    import edge_tts  # noqa: PLC0415

    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(out))


def _voice_line(text: str, pick: dict[str, Any], mp3: Path, clip: Path, max_s: float,
                rate_adj: int = 0) -> tuple[np.ndarray, str]:
    """edge-tts clip as PCM in the cast voice, nudged by the beat's ``rateAdj`` (quotes slower, jabs faster),
    with its dead air tightened (see ``tighten``); re-voiced faster once when it still overruns ``max_s``."""
    args = casting.edge_args(pick, rate_adj)
    pcm = _voice(text, pick["voice"], mp3, clip, args)
    if len(pcm) / SAMPLE_RATE > max_s:
        args = casting.edge_args(pick, max(rate_adj, 0) + FAST_PCT)
        pcm = _voice(text, pick["voice"], mp3, clip, args)
    return pcm, args["rate"]


def _voice(text: str, voice: str, mp3: Path, clip: Path, args: dict[str, str]) -> np.ndarray:
    asyncio.run(_tts(text, voice, mp3, **args))
    _to_wav(mp3, clip)
    pcm = tighten(_read_pcm(clip))
    _write_pcm(clip, pcm)  # visemes are read from the clip, so it must match the tightened audio
    return pcm


SENTENCE_GAP_S = 0.38  # edge-tts leaves ~0.9 s after every "." / "?"; a spoken beat is ~0.3-0.4 s
EDGE_S = 0.06


def tighten(pcm: np.ndarray, gap_s: float = SENTENCE_GAP_S, edge_s: float = EDGE_S) -> np.ndarray:
    """Short multi-sentence lines ("Vontade? Bonita palavra. Agora defina.") sound slow with edge-tts' long
    sentence pauses: every silent run longer than ``gap_s`` is cut to ``gap_s`` and lead/tail silence to
    ``edge_s`` (the script's ``pauseAfterS`` sets the gap between lines). Cuts fall inside silence, so no clicks."""
    if len(pcm) == 0:
        return pcm
    hop = SAMPLE_RATE // 100
    n = len(pcm) // hop
    if n == 0:
        return pcm
    rms = np.sqrt(np.mean(pcm[: n * hop].astype(np.float64).reshape(n, hop) ** 2, axis=1))
    quiet = rms < max(120.0, 0.03 * float(rms.max()))
    keep = np.ones(n, dtype=bool)
    i = 0
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < n and quiet[j]:
            j += 1
        limit = int(round((edge_s if i == 0 or j == n else gap_s) * 100))
        if j - i > limit:
            if i == 0:
                keep[: j - limit] = False  # keep the silence right before speech
            elif j == n:
                keep[i + limit:] = False
            else:
                half = limit // 2
                keep[i + half: j - (limit - half)] = False
        i = j
    mask = np.repeat(keep, hop)
    return np.concatenate([pcm[: n * hop][mask], pcm[n * hop:] if keep[-1] else pcm[:0]])


def _write_pcm(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.astype(np.int16).tobytes())


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
        cues = envelope_visemes(_read_pcm(clip))
        source = "envelope"
    return smooth_visemes([{"t": round(offset_s + float(t), 3), "v": str(v)} for t, v in cues]), source


# Envelope fallback (no Rhubarb binary): one mouth per syllable-sized window, not per 10 ms envelope
# step.  The shared ``rhubarb.visemes_from_envelope`` changes shape every envelope sample, so every
# cue is shorter than a video frame and ``smooth_visemes`` used to absorb them all into the first
# "X": a frozen mouth for the whole line.  Here a window's peak picks the openness band and voiced
# windows rotate through that band, so consecutive syllables never repeat the same mouth.
SYLLABLE_S = 0.09
SILENCE = 0.08
ENVELOPE_BANDS: tuple[tuple[float, tuple[str, ...]], ...] = (
    (0.28, ("B", "F", "B", "G")),
    (0.55, ("C", "B", "E", "G")),
    (1.01, ("D", "C", "E", "B")),
)


def envelope_visemes(pcm: np.ndarray, fps: int = ENV_FPS) -> list[tuple[float, str]]:
    env = envelope(pcm, fps) if len(pcm) else np.zeros(1)
    hop = max(1, round(SYLLABLE_S * fps))
    cues: list[tuple[float, str]] = []
    voiced = 0
    for w in range(int(np.ceil(len(env) / hop))):
        level = float(env[w * hop:(w + 1) * hop].max())
        if level < SILENCE:
            v = "X"
        else:
            shapes = next(sh for ceiling, sh in ENVELOPE_BANDS if level < ceiling)
            v = shapes[voiced % len(shapes)]
            if cues and cues[-1][1] == v:
                v = shapes[(voiced + 1) % len(shapes)]
            voiced += 1
        if not cues or cues[-1][1] != v:
            cues.append((round(w * hop / fps, 3), v))
    return cues or [(0.0, "X")]


# Short syllables do not get the full jaw: an "ah" (D) shorter than 80 ms is drawn as C, a 40 ms blip
# of any open shape as B, and a cue shorter than one frame is absorbed by its neighbour.
OPEN_STEP = {"D": "C", "E": "C", "C": "B", "F": "B", "H": "B"}


def smooth_visemes(cues: list[dict[str, Any]], frame_s: float = 1 / 24) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, c in enumerate(cues):
        dur = (cues[i + 1]["t"] - c["t"]) if i + 1 < len(cues) else 1.0
        v = c["v"]
        if v == "D" and dur < 0.08:
            v = "C"
        if v in OPEN_STEP and dur < 0.045:
            v = OPEN_STEP[v] if v != "B" else v
        if out and dur < frame_s * 0.9 and i + 1 < len(cues):
            continue  # shorter than a frame: the previous mouth holds
        if out and out[-1]["v"] == v:
            continue
        out.append({"t": c["t"], "v": v})
    return out


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
        mode = "deep" if req.get("personaMode") == "deep" else "quick"
        art = styles.pick(req.get("style") or (chars[0].get("artStyle") if chars else None))
        tone = styles.pick_tone(req.get("tone"))
        written = scripts.write(template, topic, [str(c.get("name") or "") for c in chars], lang, mode=mode, tone=tone)
        lines = written["lines"]
        fmt = "9:16" if req.get("format") == "9:16" else "16:9"
        n = 2 if template == "debate" else 1
        cast = [chars[min(i, len(chars) - 1)] for i in range(n)]
        voices = casting.cast(cast, lang, req.get("voices"))
        pcm_parts, script, sources, t = [], [], set(), 0.0
        for i, planned in enumerate(lines):
            speaker, text = planned["speaker"], planned["text"]
            mp3, clip = work / f"line_{i}.mp3", work / f"line_{i}.wav"
            pcm, rate = _voice_line(text, voices[min(speaker, len(voices) - 1)], mp3, clip, planned["maxS"],
                                    int(planned.get("rateAdj", 0)))
            dur = len(pcm) / SAMPLE_RATE
            cues, src = line_visemes(clip, text, t)
            sources.add(src)
            gap_s = planned.get("pauseAfterS", GAP_S)
            script.append({**planned, "startMs": int(t * 1000), "endMs": int((t + dur) * 1000),
                           "visemes": cues, "view": line_view(template, speaker, i, planned.get("beat", "")), "ttsRate": rate})
            pcm_parts += [pcm, np.zeros(int(gap_s * SAMPLE_RATE), dtype=np.int16)]
            t += dur + gap_s
            _set(job_id, progress=0.1 + 0.4 * (i + 1) / len(lines))
        track = np.concatenate(pcm_parts)
        audio = work / "voice.wav"
        _write_pcm(audio, track)
        duration = len(track) / SAMPLE_RATE
        rigs = [_rig_views(c, template, i, scenario) for i, c in enumerate(cast)]
        sc = scene.Scene(template=template, scenario=scenario, rigs=rigs, script=script, envelope=envelope(track),
                         env_fps=ENV_FPS, duration_s=duration, disclaimer=PARODY_DISCLAIMER,
                         names=[str(c.get("name") or "") for c in cast], roles=[role_line(c, template, written["topic"], lang) for c in cast], lang=lang, fmt=fmt, style=art)
        video = work / "preview.mp4"
        _set(job_id, progress=0.55)
        with _RENDER_LOCK:
            AnimationRenderer(width=sc.OW, height=sc.OH, fps=scene.FPS).render(
                frame_iter=sc.frames(), audio_path=audio, output_path=video, duration_s=duration,
                preset="veryfast", crf=20, gop=48, tune="animation", threads=2, deterministic=True,
                encode_timeout_s=900, retain_frames=False,
            )
        # Fail closed: decode the encoded MP4 and verify every measurable mouth, not only the
        # pre-encode stage.  The report stays beside the render for diagnosis.
        # Speech spans let the QA fail a mouth that stays shut (or frozen) while its line is heard.
        trace = {"fps": scene.FPS, "size": [sc.OW, sc.OH], "frames": sc.trace,
                 "speech": [{"start": s["startMs"] / 1000, "end": s["endMs"] / 1000, "speaker": s["speaker"]} for s in script]}
        (work / "frames.json").write_text(json.dumps(trace))
        frame_qa = frameqa.check(video, trace)
        (work / "frame_qa.json").write_text(json.dumps(frame_qa, indent=2))
        if not frame_qa["pass"]:
            raise RuntimeError(
                f"frame mouth QA failed: {frame_qa['badFrames']} bad frames; "
                f"{frame_qa['examples'][:3]}"
            )
        files = f"{base_url}/v1/files/jobs/{job_id}"
        _set(job_id, status="done", progress=1.0, result={
            "template": template, "scenario": scenario, "characters": chars,
            "script": script, "durationMs": int(duration * 1000), "audioUrl": f"{files}/voice.wav",
            "videoUrl": f"{files}/preview.mp4", "disclaimer": PARODY_DISCLAIMER,
            # Additive: which viseme source ran, and which characters had a core rig.
            "visemeSource": sorted(sources), "rigged": [r is not None for r in rigs],
            # Additive: who wrote the script and the persona sheets behind the quotes.
            "format": fmt, "width": sc.OW, "height": sc.OH,
            "voices": [{k: v[k] for k in ("voice", "pitchHz", "ratePct", "auto", "traits")} for v in voices],
            "writer": written["writer"], "topic": written["topic"], "personas": written["personas"],
            "personaMode": mode,
            **({"scriptQA": written["scriptQA"]} if "scriptQA" in written else {}),
            "style": art,
            "tone": tone,
            "frameQa": frame_qa,
            "frameQaUrl": f"{files}/frame_qa.json",
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
