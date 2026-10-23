# -*- coding: utf-8 -*-
"""Sequence reels through ffmpeg, not a per-frame Python loop.

MoviePy's ``_make_frame`` resized and graded every frame in one Python
thread. A 95 s reel spent about 18 minutes there. This path pre-scales each
still once, runs zoompan with the same eased zoom and pan, burns one ASS
caption track and the logo, renders beats in parallel, and concats with
``-c copy``. Audio is mixed once (amix, duck, loudnorm, 48 kHz) and muxed
onto that video. Any ffmpeg failure raises so the caller can fall back to
MoviePy.

``REEL_RENDERER=ffmpeg`` is the default. ``moviepy`` keeps the old path.
``REEL_RENDER_WORKERS`` overrides the pool (default ``min(4, cpu)``).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logger = logging.getLogger(__name__)

_FPS = 30
_W = 1080
_H = 1920

# Same four camera moves as ``_MOTION_PROFILES`` / ``_PAN_DIRS``.
_PROFILES = (
    {"zoom_start": 1.00, "zoom_end": 1.42, "pan_mul": 1.65, "reverse_dir": False},
    {"zoom_start": 1.48, "zoom_end": 1.02, "pan_mul": 1.00, "reverse_dir": True},
    {"zoom_start": 1.10, "zoom_end": 1.32, "pan_mul": 4.60, "reverse_dir": False},
    {"zoom_start": 1.18, "zoom_end": 1.40, "pan_mul": 2.30, "reverse_dir": True},
)
_PAN_DIRS = (
    (1, 1), (-1, 1), (1, -1), (-1, -1),
    (1, 0), (-1, 0), (0, 1), (0, -1),
)
_PAN_X = 60.0
_PAN_Y = 42.0


def sequence_renderer() -> str:
    """``ffmpeg`` unless ``REEL_RENDERER`` asks for MoviePy."""
    raw = (os.getenv("REEL_RENDERER") or "ffmpeg").strip().lower()
    if raw in {"moviepy", "py", "python"}:
        return "moviepy"
    return "ffmpeg"


def render_workers() -> int:
    raw = (os.getenv("REEL_RENDER_WORKERS") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return max(1, min(4, os.cpu_count() or 1))


def ffmpeg_exe() -> str:
    """ffmpeg for ``REEL_RENDERER=ffmpeg``.

    Order: ``FFMPEG_BINARY``, ``PATH``, then the imageio-ffmpeg binary.
    A miss raises. MoviePy is only used when the flag asks for it.
    """
    override = (os.getenv("FFMPEG_BINARY") or "").strip()
    if override:
        if Path(override).is_file():
            return override
        raise RuntimeError(f"FFMPEG_BINARY is not a file: {override}")
    found = shutil.which("ffmpeg")
    if found:
        return found
    bundled = _imageio_ffmpeg()
    if bundled:
        return bundled
    raise RuntimeError(
        "REEL_RENDERER=ffmpeg but ffmpeg is not on PATH, "
        "FFMPEG_BINARY is unset, and imageio-ffmpeg has no binary"
    )


def ffprobe_exe() -> str:
    """ffprobe for duration checks. ``FFPROBE_BINARY``, PATH, then a sibling."""
    override = (os.getenv("FFPROBE_BINARY") or "").strip()
    if override:
        if Path(override).is_file():
            return override
        raise RuntimeError(f"FFPROBE_BINARY is not a file: {override}")
    found = shutil.which("ffprobe")
    if found:
        return found
    try:
        sibling = Path(ffmpeg_exe()).with_name("ffprobe")
    except RuntimeError:
        sibling = Path("")
    if sibling.is_file():
        return str(sibling)
    raise RuntimeError(
        "ffprobe is not on PATH and FFPROBE_BINARY is unset"
    )


def _imageio_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
    except ImportError:
        return ""
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return ""
    if exe and Path(exe).is_file():
        return str(exe)
    return ""


def attempt_ffmpeg(render):
    """Run the ffmpeg renderer.

    ``None`` means the flag asked for MoviePy. When the renderer is ffmpeg,
    a missing binary or a render error raises. It does not fall back.
    """
    if sequence_renderer() != "ffmpeg":
        return None
    ffmpeg_exe()
    return render()


def media_duration_s(path: Path) -> float:
    """Duration from ffprobe, so the ffmpeg path does not import MoviePy."""
    exe = ffprobe_exe()
    proc = subprocess.run(
        [
            exe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        raise RuntimeError((proc.stderr or "ffprobe failed").strip()[-200:])
    return float(proc.stdout.strip())


def ffmpeg_has_libass() -> bool:
    """True when this ffmpeg can burn ASS. The Oracle image must say yes."""
    try:
        exe = ffmpeg_exe()
    except RuntimeError:
        return False
    try:
        proc = subprocess.run(
            [exe, "-hide_banner", "-filters"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    text = (proc.stdout or "") + (proc.stderr or "")
    return " ass " in f" {text} " or "\nass " in text or " ass " in text


def ken_burns_filter(
    act_index: int,
    frames: int,
    fps: int = _FPS,
    *,
    flicker: bool = False,
) -> str:
    """Pre-scale 2×, then one eased zoompan. No per-frame Python resize.

    The ease is the engine curve: 70% cubic ease-out plus 30% linear, so
    the camera still has speed at the cut. ``on`` is the output frame, so
    zoom and pan share one smooth value and the picture does not step.
    """
    n = max(2, int(frames))
    rate = max(1, int(fps))
    profile = _PROFILES[int(act_index) % len(_PROFILES)]
    dx, dy = _PAN_DIRS[int(act_index) % len(_PAN_DIRS)]
    if profile["reverse_dir"]:
        dx, dy = -dx, -dy
    z0 = float(profile["zoom_start"])
    z1 = float(profile["zoom_end"])
    mul = float(profile["pan_mul"])
    # Input is 2160×3840. A 60 px move on the 1080 canvas is 120 px there.
    pan_x = dx * _PAN_X * mul * 2.0
    pan_y = dy * _PAN_Y * mul * 2.0
    ease = f"(0.70*(1-pow(1-on/{n - 1},3))+0.30*on/{n - 1})"
    zoom = f"({z0}+({z1}-{z0})*{ease})"
    x = f"(iw-iw/zoom)/2+({pan_x})*{ease}"
    y = f"(ih-ih/zoom)/2+({pan_y})*{ease}"
    chain = (
        f"scale=2160:3840:force_original_aspect_ratio=increase:flags=bilinear,"
        f"crop=2160:3840,setsar=1,"
        f"zoompan=z='{zoom}':x='{x}':y='{y}':d={n}:s={_W}x{_H}:fps={rate},"
        f"fps={rate},trim=end_frame={n},setpts=PTS-STARTPTS"
    )
    if flicker:
        chain += f",eq=brightness='0.04*sin(2*PI*t*1.7+{int(act_index)})':eval=frame"
    # Limited-range 4:2:0. zoompan on a JPEG otherwise tags the file yuvj420p.
    chain += ",format=yuv420p"
    return chain


def _ass_time(seconds: float) -> str:
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    minutes = int(total % 3600 // 60)
    sec = total % 60
    return f"{hours}:{minutes:02d}:{sec:05.2f}"


def _ass_text(text: str) -> str:
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


def phrase_cues(
    word_timings: list[tuple[str, float, float]] | None,
    *,
    words_per_phrase: int = 4,
    hook_text: str = "",
    hook_until_s: float = 0.0,
    cta_text: str = "",
    cta_start_s: float = -1.0,
    end_s: float = 0.0,
) -> list[tuple[str, float, float, str]]:
    """Hook line, 4-word phrases, and the CTA, on one timeline."""
    cues: list[tuple[str, float, float, str]] = []
    if hook_text and hook_until_s > 0.05:
        cues.append(("Hook", 0.05, float(hook_until_s), hook_text.strip()))
    group = max(1, min(6, int(words_per_phrase or 4)))
    words = [
        (str(word), float(start), float(end))
        for word, start, end in (word_timings or [])
        if str(word).strip() and float(end) > float(start)
    ]
    index = 0
    while index < len(words):
        chunk = words[index:index + group]
        index += group
        text = " ".join(word for word, _s, _e in chunk)
        start = chunk[0][1]
        end = chunk[-1][2]
        if cta_start_s >= 0 and start >= cta_start_s - 0.05:
            break
        cues.append(("Sub", start, max(start + 0.2, end), text))
    if cta_text and cta_start_s >= 0 and end_s > cta_start_s:
        cues.append(("Cta", float(cta_start_s), float(end_s), cta_text.strip()))
    return cues


def ass_document(
    cues: list[tuple[str, float, float, str]],
    *,
    scale: float = 1.0,
) -> str:
    """One ASS script. libass burns it; there is no per-frame caption blend.

    ``scale`` is 1.5 for Ancient Knowledge. Other channels stay at 64 and 80.
    """
    style = (
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding"
    )
    factor = float(scale or 1.0)
    sub = max(24, int(round(64 * factor)))
    hook = max(32, int(round(80 * factor)))
    sub_style = (
        f"Style: Sub,DejaVu Sans,{sub},&H0000E6FF,&H00000000,&H80000000,"
        "1,0,0,0,100,100,0,0,3,14,1,2,40,40,280,1"
    )
    hook_style = (
        f"Style: Hook,DejaVu Sans,{hook},&H00FFFFFF,&H00000000,&H80000000,"
        "1,0,0,0,100,100,0,0,3,14,1,8,40,40,220,1"
    )
    cta_style = (
        f"Style: Cta,DejaVu Sans,{sub},&H0000E6FF,&H00000000,&H80000000,"
        "1,0,0,0,100,100,0,0,3,14,1,2,40,40,280,1"
    )
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
{style}
{sub_style}
{hook_style}
{cta_style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for style, start, end, text in cues:
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{_ass_text(text)}\n"
        )
    return "".join(lines)


def _grade(grain: float, vignette: float) -> str:
    """Subtle grade only. Zero, the Ancient Knowledge default, adds nothing."""
    parts: list[str] = []
    if vignette and float(vignette) > 0.01:
        # A full PI/5 vignette is what made frames murky. Scale it down.
        angle = min(0.35, 0.15 * float(vignette))
        parts.append(f"vignette=angle={angle:.3f}")
    if grain and float(grain) > 0.5:
        amount = max(1, min(6, int(round(float(grain) / 8.0))))
        parts.append(f"noise=alls={amount}:allf=t")
    return ",".join(parts)


def _encode(duration_s: float, preset: str) -> list[str]:
    from production.media.reel_sequence_engine import sequence_encode_params

    enc = sequence_encode_params(duration_s, preset)
    return [
        "-c:v", "libx264",
        "-preset", enc["preset"],
        "-crf", enc["crf"],
        "-maxrate", enc["maxrate"],
        "-bufsize", enc["bufsize"],
        "-pix_fmt", "yuv420p",
        "-color_range", "tv",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-r", str(_FPS),
        "-fps_mode", "cfr",
        "-video_track_timescale", "90000",
    ]


def _ffmpeg_timeout_s() -> float:
    try:
        return max(30.0, float(os.environ.get("REEL_FFMPEG_TIMEOUT_S", "600")))
    except ValueError:
        return 600.0


def _run(cmd: list[str]) -> None:
    # s23d: one beat's ffmpeg sat at near-zero CPU for 27 minutes and held the
    # whole reel. A stuck encode now fails the beat instead of the machine.
    # capture_output leaves stdin as a pipe. ffmpeg reads that pipe and waits.
    # This build's flag is `-stdin` (a boolean), not `-nostdin`, so the pipe
    # is closed here instead of passing a flag the binary rejects.
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=_ffmpeg_timeout_s(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg timed out after {exc.timeout:.0f}s") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise RuntimeError(tail or f"ffmpeg exit {proc.returncode}")


def _beat_ass(
    path: Path,
    cues: list[tuple[str, float, float, str]],
    t0: float,
    dur: float,
    *,
    scale: float = 1.0,
) -> None:
    local: list[tuple[str, float, float, str]] = []
    end = t0 + dur
    for style, start, stop, text in cues:
        if stop <= t0 or start >= end:
            continue
        local.append((style, max(0.0, start - t0), min(dur, stop - t0), text))
    path.write_text(ass_document(local, scale=scale), encoding="utf-8")


def _render_beat(
    index: int,
    image: Path,
    video: Path | None,
    dur: float,
    ass_path: Path,
    logo: Path | None,
    grade: str,
    encode: list[str],
    out: Path,
    *,
    flicker: bool,
    fps: int = _FPS,
) -> None:
    rate = max(1, int(fps))
    frames = max(2, int(round(dur * rate)))
    exe = ffmpeg_exe()
    cmd = [exe, "-y", "-v", "error"]
    if video is not None and video.is_file():
        cmd += ["-i", str(video)]
        video_chain = (
            f"scale={_W}:{_H}:force_original_aspect_ratio=increase,crop={_W}:{_H},"
            f"fps={rate},tpad=stop_mode=clone:stop=-1,trim=duration={dur:.3f},"
            f"setpts=PTS-STARTPTS,format=yuv420p"
        )
    else:
        cmd += ["-loop", "1", "-i", str(image)]
        video_chain = ken_burns_filter(index, frames, rate, flicker=flicker)
    logo_index = None
    if logo is not None and logo.is_file():
        logo_index = 1
        cmd += ["-i", str(logo)]
    chain = f"[0:v]{video_chain}"
    if grade:
        chain += f",{grade}"
    chain += "[base]"
    if logo_index is not None:
        chain += (
            f";[base][{logo_index}:v]overlay=(W-w)/2:H-h-90:format=auto[marked]"
        )
        labelled = "marked"
    else:
        labelled = "base"
    # ass= needs a filename. Colons in the path are escaped for the filter.
    ass_arg = str(ass_path).replace("\\", "/").replace(":", "\\:")
    chain += f";[{labelled}]ass='{ass_arg}',format=yuv420p[out]"
    cmd += [
        "-filter_complex", chain,
        "-map", "[out]",
        "-an",
        "-threads", "2",
        *encode,
        str(out),
    ]
    _run(cmd)


def _audio_filter(
    stems: list[dict],
    *,
    duck_until: float,
    duck_ratio: float,
) -> str:
    """Mix named audio inputs. The silent video is never one of them.

    Each stem is ``{"index", "role", "volume"}``. ``index`` is the ffmpeg
    ``-i`` position assigned by the muxer, so a new stem cannot reuse ``[0:a]``.
    ``bed`` and ``sfx`` duck under the voice. ``boom`` and ``impact`` do not.
    """
    parts: list[str] = []
    labels: list[str] = []
    until = f"{max(0.0, float(duck_until)):.3f}"
    ratio = max(0.0, min(1.0, float(duck_ratio)))
    for order, stem in enumerate(stems):
        index = int(stem["index"])
        role = str(stem.get("role") or "bed")
        volume = max(0.0, float(stem.get("volume") or 0.0))
        label = f"s{order}"
        if role in {"bed", "sfx"}:
            ducked = f"{volume * ratio:.4f}"
            open_bed = f"{volume:.4f}"
            vol = (
                f"volume='if(lt(t,{until}),{ducked},{open_bed})':eval=frame"
            )
            parts.append(f"[{index}:a]aresample=48000,{vol},apad[{label}]")
        else:
            parts.append(
                f"[{index}:a]volume={volume:.3f},aresample=48000,apad[{label}]"
            )
        labels.append(f"[{label}]")
    if not labels:
        return ""
    if len(labels) == 1:
        mix = f"{labels[0]}loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]"
    else:
        mix = (
            f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:"
            f"dropout_transition=0:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11,"
            f"aresample=48000[a]"
        )
    parts.append(mix)
    return ";".join(parts)


def _write_wav(path: Path, samples, sample_rate: int = 48000) -> None:
    """Stereo 16-bit wav. ``samples`` is a mono float array in -1..1."""
    import wave

    import numpy as np

    data = np.asarray(samples, dtype=np.float32)
    data = np.clip(data, -1.0, 1.0)
    stereo = np.column_stack([data, data])
    pcm = (stereo * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())


def _drone_wav(path: Path, duration: float, *, profile: str, volume: float) -> None:
    """The same mystery/warrior pad the MoviePy path synthesizes."""
    import numpy as np

    sr = 48000
    n = int(max(0.5, float(duration)) * sr)
    t = np.linspace(0.0, max(0.5, float(duration)), n, dtype=np.float32)
    swell = 0.85 + 0.15 * np.sin(2 * np.pi * 0.05 * t)
    if (profile or "").lower() == "warrior":
        drone = (
            0.42 * np.sin(2 * np.pi * 40.0 * t)
            + 0.28 * np.sin(2 * np.pi * 55.0 * t)
            + 0.16 * np.sin(2 * np.pi * 82.5 * t)
            + 0.08 * np.sin(2 * np.pi * 110.0 * t) * swell
        )
        vol = max(0.28, min(0.38, float(volume)))
    else:
        drone = (
            0.40 * np.sin(2 * np.pi * 55.0 * t)
            + 0.22 * np.sin(2 * np.pi * 82.5 * t)
            + 0.14 * np.sin(2 * np.pi * 110.0 * t)
        )
        vol = max(0.08, float(volume))
    combined = drone * swell
    peak = float(np.max(np.abs(combined))) or 1.0
    _write_wav(path, combined / peak * vol, sr)


def _boom_wav(path: Path, duration: float, cuts: list[float]) -> None:
    """400 ms, 65 Hz booms, 50 ms before each cut. One stem for the whole reel."""
    import numpy as np

    sr = 48000
    n = int(max(0.5, float(duration)) * sr)
    mix = np.zeros(n, dtype=np.float32)
    boom_n = int(0.40 * sr)
    bt = np.linspace(0.0, 0.40, boom_n, dtype=np.float32)
    boom = np.sin(2.0 * np.pi * 65.0 * bt) * np.exp(-bt * 18.0) * 0.12
    for cut in cuts:
        start = int(max(0.0, float(cut) - 0.05) * sr)
        end = min(n, start + boom_n)
        if end > start:
            mix[start:end] += boom[: end - start]
    _write_wav(path, mix, sr)


def output_has_audio(path: Path) -> bool | None:
    """True when ffprobe sees an audio stream. None when ffprobe is missing."""
    try:
        exe = ffprobe_exe()
    except RuntimeError:
        return None
    proc = subprocess.run(
        [
            exe, "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return "audio" in (proc.stdout or "")


def compile_ffmpeg_reel(
    *,
    image_paths: list[Path],
    act_durations: list[float],
    output_path: Path,
    word_timings: list[tuple[str, float, float]] | None = None,
    hook_text: str = "",
    enable_hook_text: bool = True,
    cta_text: str = "",
    cta_start_s: float = -1.0,
    act_video_paths: list[Path | None] | None = None,
    logo_path: Path | None = None,
    fps: int = _FPS,
    enable_flicker: bool = False,
    grain_intensity: float = 0.0,
    vignette_strength: float = 0.0,
    ffmpeg_preset: str = "veryfast",
    voice_audio: Path | None = None,
    ambient_audio: Path | None = None,
    voice_volume_gain: float = 1.0,
    ambient_volume: float = 0.32,
    ambient_duck_ratio: float = 0.70,
    ambient_duck_until_s: float = 0.0,
    words_per_phrase: int = 4,
    workers: int | None = None,
    sfx_loop_audio: Path | None = None,
    impact_sfx_audio: Path | None = None,
    ambient_profile: str = "mystery",
    require_voice: bool = False,
    synthesize_drone: bool = False,
    transition_booms: bool = False,
    caption_scale: float = 1.0,
) -> Path:
    """Render the reel. Raises on any ffmpeg error."""
    if not image_paths:
        raise ValueError("no stills")
    voice_file = Path(voice_audio) if voice_audio else None
    if require_voice and (voice_file is None or not voice_file.is_file()):
        raise RuntimeError(
            "voice track required — refusing to compile a reel without narration"
        )
    exe = ffmpeg_exe()
    if not ffmpeg_has_libass():
        raise RuntimeError("ffmpeg has no libass filter")
    durations = [max(0.2, float(d)) for d in act_durations]
    if len(durations) != len(image_paths):
        pad = durations[-1] if durations else 4.0
        durations = (durations + [pad] * len(image_paths))[:len(image_paths)]
    total = float(sum(durations))
    videos = list(act_video_paths or [])
    hook_until = durations[0] if enable_hook_text and hook_text else 0.0
    cues = phrase_cues(
        word_timings,
        words_per_phrase=words_per_phrase,
        hook_text=hook_text if enable_hook_text else "",
        hook_until_s=hook_until,
        cta_text=cta_text,
        cta_start_s=cta_start_s,
        end_s=total,
    )
    grade = _grade(grain_intensity, vignette_strength)
    encode = _encode(total, ffmpeg_preset)
    pool = workers if workers is not None else render_workers()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reel_ff_") as tmp_name:
        tmp = Path(tmp_name)
        ass_all = tmp / "reel.ass"
        ass_all.write_text(ass_document(cues, scale=caption_scale), encoding="utf-8")
        segments: list[Path] = []
        jobs = []
        cursor = 0.0
        for index, image in enumerate(image_paths):
            dur = durations[index]
            seg = tmp / f"seg_{index:02d}.mp4"
            ass_path = tmp / f"b{index:02d}.ass"
            _beat_ass(ass_path, cues, cursor, dur, scale=caption_scale)
            video = videos[index] if index < len(videos) else None
            video_path = Path(video) if video else None
            jobs.append((index, Path(image), video_path, dur, ass_path, seg))
            segments.append(seg)
            cursor += dur

        def _one(job: tuple) -> None:
            index, image, video, dur, ass_path, seg = job
            _render_beat(
                index, image, video, dur, ass_path, logo_path, grade, encode, seg,
                flicker=bool(enable_flicker),
                fps=int(fps),
            )

        with ThreadPoolExecutor(max_workers=max(1, int(pool))) as executor:
            list(executor.map(_one, jobs))
        listing = tmp / "segs.txt"
        listing.write_text(
            "".join(f"file '{seg.resolve().as_posix()}'\n" for seg in segments),
            encoding="utf-8",
        )
        silent = tmp / "silent.mp4"
        _run([
            exe, "-y", "-v", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", str(silent),
        ])
        voice = voice_file if voice_file is not None and voice_file.is_file() else None
        bed = Path(ambient_audio) if ambient_audio and Path(ambient_audio).is_file() else None
        sfx = Path(sfx_loop_audio) if sfx_loop_audio and Path(sfx_loop_audio).is_file() else None
        impact = (
            Path(impact_sfx_audio)
            if impact_sfx_audio and Path(impact_sfx_audio).is_file()
            else None
        )
        drone = None
        if bed is None and synthesize_drone:
            drone = tmp / "drone.wav"
            _drone_wav(
                drone, total,
                profile=ambient_profile,
                volume=float(ambient_volume or 0.32),
            )
            bed = drone
        booms = None
        cuts = []
        if transition_booms and len(durations) > 1:
            cursor_s = 0.0
            for dur in durations[:-1]:
                cursor_s += float(dur)
                cuts.append(cursor_s)
            booms = tmp / "booms.wav"
            _boom_wav(booms, total, cuts)
        if voice is None and bed is None and sfx is None and impact is None and booms is None:
            shutil.move(str(silent), str(out))
            return out
        # Input 0 is the silent video. Audio stems start at 1, in this order.
        cmd = [exe, "-y", "-v", "error", "-i", str(silent)]
        stems: list[dict] = []
        nxt = 1
        if voice is not None:
            cmd += ["-i", str(voice)]
            stems.append({
                "index": nxt,
                "role": "voice",
                "volume": float(voice_volume_gain or 1.0),
            })
            nxt += 1
        if bed is not None:
            if drone is None:
                cmd += ["-stream_loop", "-1", "-i", str(bed)]
            else:
                cmd += ["-i", str(bed)]
            stems.append({
                "index": nxt,
                "role": "bed",
                "volume": float(ambient_volume or 0.32),
            })
            nxt += 1
        if booms is not None:
            cmd += ["-i", str(booms)]
            stems.append({"index": nxt, "role": "boom", "volume": 1.0})
            nxt += 1
        if sfx is not None:
            cmd += ["-stream_loop", "-1", "-i", str(sfx)]
            stems.append({"index": nxt, "role": "sfx", "volume": 0.35})
            nxt += 1
        if impact is not None:
            cmd += ["-i", str(impact)]
            stems.append({"index": nxt, "role": "impact", "volume": 0.50})
            nxt += 1
        graph = _audio_filter(
            stems,
            duck_until=float(ambient_duck_until_s or 0.0) or (total if voice else 0.0),
            duck_ratio=float(ambient_duck_ratio or 0.70),
        )
        if not graph or (voice is not None and "[1:a]" not in graph and stems[0]["index"] == 1):
            raise RuntimeError("audio graph did not include the voice input")
        cmd += [
            "-filter_complex", graph,
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            # apad and a looped bed never end. -t stops the mux at the picture.
            "-shortest",
            "-t", f"{total:.3f}",
            str(out),
        ]
        _run(cmd)
        if voice is not None:
            heard = output_has_audio(out)
            if heard is False:
                raise RuntimeError("ffmpeg mux produced no audio stream")
    if not out.is_file():
        raise RuntimeError("ffmpeg produced no file")
    logger.info(
        "FFMPEG reel | %s | %.1fs | workers=%s | beats=%d",
        out.name, total, pool, len(image_paths),
    )
    return out
