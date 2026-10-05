# -*- coding: utf-8 -*-
"""Burn phrase subtitles onto an already-compiled reel (no image/TTS regen).

1. Transcribe VO (Gemini) — or use --script
2. Approximate word timings across VO duration
3. Overlay yellow lower-third phrases (AK style) onto the MP4
"""
from __future__ import annotations

from pathlib import Path as _ReorgPath
import sys as _reorg_sys
_REORG_ROOT = _ReorgPath(__file__).resolve().parents[1]
if str(_REORG_ROOT) not in _reorg_sys.path:
    _reorg_sys.path.insert(0, str(_REORG_ROOT))

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
from utils.pipeline_paths import moviepy_temp_audio_dir, page_outputs_dir

load_dotenv(ROOT / ".env")

import numpy as np
from moviepy import VideoFileClip  # type: ignore
from PIL import Image, ImageDraw, ImageFont

from agents.media.audio_engine import approximate_word_timings
from core.reel_sequence_engine import (
    _chunk_words_into_phrases,
    _draw_wrapped_text,
    _sanitize_subtitle_timings,
)

CLIPS = page_outputs_dir("ancient_knowledge") / "clips"


def _ak_style() -> dict:
    font = ROOT / "Fonts" / "Montserrat" / "static" / "Montserrat-Bold.ttf"
    try:
        from channels_config.ancient_knowledge import page_config as pc

        rel = getattr(pc, "FONT_PATH", None)
        if rel and (ROOT / str(rel)).is_file():
            font = ROOT / str(rel)
        return {
            "font_path": font,
            "fontsize": int(getattr(pc, "SUBTITLE_FONTSIZE", 56)),
            "y": int(getattr(pc, "SUBTITLE_Y_POSITION", 1500)),
            "fill": (255, 230, 0),
            "words_per_phrase": 4,
        }
    except Exception:
        return {
            "font_path": font,
            "fontsize": 56,
            "y": 1500,
            "fill": (255, 230, 0),
            "words_per_phrase": 4,
        }


def transcribe_audio(audio_path: Path) -> str:
    """Cheap Gemini transcription of existing VO (no TTS spend)."""
    import config as app_config
    from google.genai import types
    from core.google_guardrail import guarded_generate_content, make_guarded_gemini_client

    key = getattr(app_config, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing — cannot transcribe VO for subtitles")

    client = make_guarded_gemini_client(key)
    data = Path(audio_path).read_bytes()
    mime = "audio/mpeg" if audio_path.suffix.lower() in {".mp3", ".mpeg"} else "audio/wav"
    prompt = (
        "Transcribe this English narration exactly as spoken. "
        "Return ONLY the plain transcript text — no timestamps, no labels, "
        "no markdown, no commentary."
    )
    resp = guarded_generate_content(
        client,
        model="models/gemini-2.5-flash",
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=data, mime_type=mime),
                    types.Part.from_text(text=prompt),
                ],
            )
        ],
        source="scripts.burn_subs_onto_reel",
    )
    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        raise RuntimeError(f"Empty transcript from Gemini for {audio_path}")
    # Collapse whitespace
    return " ".join(text.split())


def build_phrase_timeline(
    script: str,
    duration_s: float,
    *,
    words_per_phrase: int = 4,
) -> list[tuple[str, float, float]]:
    wt = approximate_word_timings(script, duration_s)
    raw = _chunk_words_into_phrases(wt, words_per_phrase)
    cleaned = _sanitize_subtitle_timings(raw)
    out: list[tuple[str, float, float]] = []
    cut = max(0.05, float(duration_s) - 0.04)
    for ph, ps, pe in cleaned:
        ps2 = max(0.0, float(ps))
        pe2 = min(cut, float(pe))
        if ps2 < pe2 and ph.strip():
            toks = ph.split()
            if len(toks) > max(1, words_per_phrase):
                ph = " ".join(toks[:words_per_phrase])
            out.append((ph, ps2, pe2))
    return _sanitize_subtitle_timings(out)


def burn_subtitles(
    video_in: Path,
    video_out: Path,
    phrases: list[tuple[str, float, float]],
    *,
    font_path: Path,
    fontsize: int,
    y: int,
    fill: tuple,
) -> Path:
    vid = VideoFileClip(str(video_in))
    w, h = int(vid.w), int(vid.h)
    try:
        font = ImageFont.truetype(str(font_path), fontsize)
    except Exception:
        font = ImageFont.load_default()

    cache: dict[str, np.ndarray] = {}
    for ph, _, _ in phrases:
        if ph in cache:
            continue
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        _draw_wrapped_text(
            draw,
            ph,
            font,
            y if y < h else int(h * 0.82),
            w,
            fill=fill,
            stroke_width=0,
            max_lines=2,
        )
        cache[ph] = np.array(layer)

    def _phrase_at(t: float) -> str:
        for ph, ps, pe in phrases:
            if ps <= t < pe:
                return ph
        return ""

    def _make_frame(t: float) -> np.ndarray:
        frame = vid.get_frame(t)
        ph = _phrase_at(t)
        if not ph or ph not in cache:
            return frame
        base = Image.fromarray(frame).convert("RGBA")
        overlay = Image.fromarray(cache[ph], mode="RGBA")
        base.alpha_composite(overlay)
        return np.array(base.convert("RGB"))

    from moviepy import VideoClip  # type: ignore

    out_clip = VideoClip(_make_frame, duration=float(vid.duration)).with_fps(vid.fps or 30)
    if vid.audio is not None:
        out_clip = out_clip.with_audio(vid.audio)
    video_out = Path(video_out)
    video_out.parent.mkdir(parents=True, exist_ok=True)
    out_clip.write_videofile(
        str(video_out),
        codec="libx264",
        audio_codec="aac",
        fps=vid.fps or 30,
        logger=None,
        threads=4,
        temp_audiofile_path=moviepy_temp_audio_dir(),
    )
    try:
        out_clip.close()
    except Exception:
        pass
    vid.close()
    return video_out


def burn_mesopotamia_remix() -> Path:
    style = _ak_style()
    video = CLIPS / "reel_mesopotamia_audio_remix_mystery_v01.mp4"
    # Prefer the VO actually muxed into the remix (trimmed to video length)
    voice = CLIPS / "the_baghdad_battery_8cb4cd_v01_v01_voice_el_trim_to_video.mp3"
    if not voice.is_file():
        voice = CLIPS / "the_baghdad_battery_8cb4cd_v01_v01_voice_el.mp3"
    out = CLIPS / "reel_mesopotamia_audio_remix_mystery_subs_v01.mp4"

    print(f"[subs] video={video.name}")
    print(f"[subs] voice={voice.name}")
    print("[subs] transcribing VO with Gemini…")
    script = transcribe_audio(voice)
    script_path = CLIPS / "the_baghdad_battery_8cb4cd_v01_transcript.txt"
    script_path.write_text(script, encoding="utf-8")
    print(f"[subs] transcript ({len(script.split())} words) → {script_path.name}")
    print(script[:240], "…")

    from moviepy import AudioFileClip

    with VideoFileClip(str(video)) as v:
        dur = float(v.duration)
    # Cap timing to video; if voice shorter, use voice dur
    with AudioFileClip(str(voice)) as a:
        voice_dur = float(a.duration)
    timing_dur = min(dur, voice_dur)

    phrases = build_phrase_timeline(
        script, timing_dur, words_per_phrase=style["words_per_phrase"]
    )
    print(f"[subs] {len(phrases)} phrases over {timing_dur:.1f}s")
    result = burn_subtitles(
        video,
        out,
        phrases,
        font_path=style["font_path"],
        fontsize=style["fontsize"],
        y=style["y"],
        fill=style["fill"],
    )
    print("WROTE", result, result.stat().st_size)
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--video", type=Path, default=None)
    p.add_argument("--voice", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--script", type=Path, default=None, help="Skip STT; use this text file")
    args = p.parse_args()
    if args.video is None:
        burn_mesopotamia_remix()
        return

    style = _ak_style()
    video = Path(args.video)
    voice = Path(args.voice) if args.voice else video
    out = Path(args.out) if args.out else video.with_name(video.stem + "_subs.mp4")
    if args.script and Path(args.script).is_file():
        script = Path(args.script).read_text(encoding="utf-8").strip()
    else:
        # Extract audio from video if voice is the video itself
        if voice.suffix.lower() in {".mp4", ".mov", ".mkv"}:
            from moviepy import AudioFileClip

            tmp = CLIPS / f"{video.stem}_extract_audio.mp3"
            v = VideoFileClip(str(voice))
            v.audio.write_audiofile(str(tmp), logger=None)
            v.close()
            script = transcribe_audio(tmp)
        else:
            script = transcribe_audio(voice)
    from moviepy import AudioFileClip, VideoFileClip as V

    with V(str(video)) as v:
        dur = float(v.duration)
    phrases = build_phrase_timeline(script, dur, words_per_phrase=style["words_per_phrase"])
    burn_subtitles(
        video,
        out,
        phrases,
        font_path=style["font_path"],
        fontsize=style["fontsize"],
        y=style["y"],
        fill=style["fill"],
    )
    print("WROTE", out, out.stat().st_size)


if __name__ == "__main__":
    main()
