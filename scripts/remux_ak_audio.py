# -*- coding: utf-8 -*-
"""Audio-only remix for existing Ancient Knowledge reels.

Keeps the compiled video track (images + any burned overlays) and existing
narration. Regenerates and/or re-mixes BGM (+ optional atmosphere SFX) with
ducking from page_config — no image or TTS regeneration.

Important limitation
--------------------
Burned-in subtitles live on the *video* track. This tool cannot add captions
to a reel that was compiled without word_timings (e.g. silent serverless VO).
To get subtitles you must recompile from scene images + timings, not remux.
"""
from __future__ import annotations

from pathlib import Path as _ReorgPath
import sys as _reorg_sys
_REORG_ROOT = _ReorgPath(__file__).resolve().parents[1]
if str(_REORG_ROOT) not in _reorg_sys.path:
    _reorg_sys.path.insert(0, str(_REORG_ROOT))

import argparse
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
from utils.pipeline_paths import moviepy_temp_audio_dir, page_outputs_dir

load_dotenv(ROOT / ".env")
os.environ.setdefault("ENABLE_REMOTE_GPU_WORKFLOWS", "false")

from moviepy import (  # type: ignore
    AudioFileClip,
    CompositeAudioClip,
    VideoFileClip,
    concatenate_audioclips,
)

CLIPS = page_outputs_dir("ancient_knowledge") / "clips"


def _load_mix_defaults() -> dict:
    """Pull duck/volume knobs from ancient_knowledge page_config when available."""
    defaults = {
        "bgm_vol": 0.26,
        "sfx_vol": 0.22,
        "duck": 0.48,
        "bgm_start": 0.5,
        "style": "mystery",
        "directive": ROOT
        / "channels_config"
        / "ancient_knowledge"
        / "prompts"
        / "music_prompt_directive.txt",
        "sfx_prompt": (
            "Slow dark ancient temple atmosphere, low stone chamber drone only, "
            "distant wind through ruins, no rhythm, no percussion, no melody, "
            "no vocals, seamless seamless loop"
        ),
    }
    try:
        from channels_config.ancient_knowledge import page_config as pc

        defaults["bgm_vol"] = float(getattr(pc, "AMBIENT_VOLUME", defaults["bgm_vol"]))
        defaults["sfx_vol"] = float(
            getattr(pc, "ATMOSPHERE_SFX_VOLUME", defaults["sfx_vol"])
        )
        defaults["duck"] = float(getattr(pc, "AMBIENT_DUCK_RATIO", defaults["duck"]))
        defaults["bgm_start"] = float(getattr(pc, "BGM_START_TIME", defaults["bgm_start"]))
        defaults["style"] = str(
            getattr(pc, "AMBIENT_MUSIC_STYLE", defaults["style"]) or "mystery"
        )
        rel = getattr(pc, "MUSIC_PROMPT_DIRECTIVE_RELPATH", None)
        if rel:
            p = ROOT / str(rel)
            if p.is_file():
                defaults["directive"] = p
        sfx_p = getattr(pc, "AMBIENT_SFX_PROMPT", None)
        if sfx_p:
            defaults["sfx_prompt"] = str(sfx_p)
    except Exception:
        pass
    return defaults


def _loop_to(path: Path, duration: float):
    clip = AudioFileClip(str(path))
    if clip.duration >= duration - 0.05:
        return clip.subclipped(0, duration)
    n = max(1, int(math.ceil(duration / max(0.5, clip.duration))))
    parts = [AudioFileClip(str(path)) for _ in range(n)]
    return concatenate_audioclips(parts).subclipped(0, duration)


def _duck(clip, bed_vol: float, until_s: float, duck: float):
    dur = float(clip.duration or 0)
    until = max(0.0, min(float(until_s), dur - 0.05))
    ducked = max(0.05, bed_vol * duck)
    full = max(0.05, bed_vol)
    if until <= 0.05:
        return clip.with_volume_scaled(full)
    head = clip.subclipped(0, until).with_volume_scaled(ducked)
    tail = clip.subclipped(until, dur).with_volume_scaled(full)
    return concatenate_audioclips([head, tail])


def mix_beds(
    voice: Path,
    music: Path,
    out_wav: Path,
    *,
    sfx: "Path | None" = None,
    bgm_vol: float = 0.26,
    sfx_vol: float = 0.22,
    duck: float = 0.48,
    bgm_start: float = 0.5,
) -> tuple[Path, float]:
    """VO + ducked BGM (+ optional atmosphere SFX) → WAV. Audio-driven duration."""
    v = AudioFileClip(str(voice))
    total = float(v.duration) + 0.35
    narr_end = float(v.duration)
    layers = []
    sfx_c = None
    if sfx is not None and Path(sfx).is_file():
        sfx_c = _duck(_loop_to(Path(sfx), total), sfx_vol, narr_end, duck)
        layers.append(sfx_c)
    bgm = _loop_to(Path(music), total)
    try:
        bgm = bgm.with_start(bgm_start)
    except Exception:
        pass
    bgm = _duck(bgm, bgm_vol, narr_end, duck)
    layers.extend([bgm, v])
    mixed = CompositeAudioClip(layers).with_duration(total)
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    mixed.write_audiofile(str(out_wav), fps=48000, logger=None)
    for c in (v, sfx_c, bgm, mixed):
        if c is None:
            continue
        try:
            c.close()
        except Exception:
            pass
    return out_wav, total


def mux_audio_onto_video(
    video_in: Path,
    audio_path: Path,
    video_out: Path,
    duration: float,
) -> Path:
    """Replace audio on an already-compiled MP4. Does not touch burned pixels."""
    vid = VideoFileClip(str(video_in))
    aud = AudioFileClip(str(audio_path))
    if vid.duration < duration - 0.05:
        final = vid.with_audio(aud).with_duration(
            min(duration, max(vid.duration, aud.duration))
        )
    else:
        final = vid.subclipped(0, min(vid.duration, duration)).with_audio(aud)
        if final.duration < duration - 0.05:
            final = final.with_duration(duration)
    video_out = Path(video_out)
    video_out.parent.mkdir(parents=True, exist_ok=True)
    final.write_videofile(
        str(video_out),
        codec="libx264",
        audio_codec="aac",
        fps=vid.fps or 24,
        logger=None,
        threads=4,
        temp_audiofile_path=moviepy_temp_audio_dir(),
    )
    vid.close()
    aud.close()
    try:
        final.close()
    except Exception:
        pass
    return video_out


def regenerate_mystery_music(
    out_path: Path,
    *,
    duration_s: float,
    topic: str,
    style: str = "mystery",
    directive: "Path | None" = None,
) -> Path:
    from agents.media.audio_engine import generate_music_v2_bed

    path = generate_music_v2_bed(
        Path(out_path),
        duration_seconds=float(duration_s),
        topic=topic,
        directive_path=directive,
        style_profile=style,
    )
    if path is None or not Path(path).is_file():
        raise RuntimeError(f"music_v2 bed failed → {out_path}")
    return Path(path)


def remix_reel_audio(
    *,
    video: Path,
    voice: Path,
    out: Path,
    music: "Path | None" = None,
    sfx: "Path | None" = None,
    regen_music: bool = False,
    topic: str = "",
    stem: str = "remix",
    trim_voice_to_video: bool = True,
) -> Path:
    """
    Audio-only remix capability.

    - Never regenerates images or narration.
    - Optionally regenerates music_v2 with mystery directive + page_config duck mix.
    - Muxes onto the existing compiled video (subtitle pixels unchanged).
    - By default trims VO to the compiled video length (avoids long freeze-frame pads).
    """
    cfg = _load_mix_defaults()
    video = Path(video)
    voice = Path(voice)
    if not video.is_file():
        raise FileNotFoundError(video)
    if not voice.is_file():
        raise FileNotFoundError(voice)

    with VideoFileClip(str(video)) as vid:
        video_dur = float(vid.duration)
    with AudioFileClip(str(voice)) as v:
        voice_dur = float(v.duration)

    if trim_voice_to_video and voice_dur > video_dur + 0.35:
        trimmed = CLIPS / f"{stem}_voice_trim_to_video.mp3"
        print(
            f"[remix] VO ({voice_dur:.1f}s) > video ({video_dur:.1f}s) — "
            f"trimming VO to video length (no freeze-frame pad)"
        )
        clip = AudioFileClip(str(voice)).subclipped(0, video_dur)
        clip.write_audiofile(str(trimmed), fps=48000, logger=None)
        clip.close()
        voice = trimmed
        voice_dur = video_dur

    music_path = Path(music) if music else None
    if regen_music or music_path is None or not music_path.is_file():
        music_out = CLIPS / f"{stem}_music_v2_remix.mp3"
        print(
            f"[remix] regenerating mystery music_v2 | dur≈{voice_dur:.1f}s | "
            f"style={cfg['style']} | topic={topic[:60]!r}"
        )
        music_path = regenerate_mystery_music(
            music_out,
            duration_s=max(40.0, voice_dur),
            topic=topic or stem.replace("_", " "),
            style=cfg["style"],
            directive=cfg["directive"],
        )
        print(f"[remix] music → {music_path.name} ({music_path.stat().st_size} bytes)")
    else:
        print(f"[remix] reusing music → {music_path.name}")

    sfx_path = Path(sfx) if sfx and Path(sfx).is_file() else None
    if sfx_path:
        print(f"[remix] reusing atmosphere SFX → {sfx_path.name}")
    else:
        print("[remix] no atmosphere SFX — VO + BGM only")

    mix_wav = CLIPS / f"{stem}_remix_audio.wav"
    print(
        f"[remix] mix | bgm={cfg['bgm_vol']} sfx={cfg['sfx_vol']} "
        f"duck={cfg['duck']} start={cfg['bgm_start']}"
    )
    mix_path, dur = mix_beds(
        voice,
        music_path,
        mix_wav,
        sfx=sfx_path,
        bgm_vol=cfg["bgm_vol"],
        sfx_vol=cfg["sfx_vol"],
        duck=cfg["duck"],
        bgm_start=cfg["bgm_start"],
    )
    print(f"[remix] mux → {out.name} (audio-driven {dur:.1f}s)")
    print(
        "[remix] NOTE: burned-in subtitles are not modified. "
        "If the source video has none, remux cannot invent them."
    )
    return mux_audio_onto_video(video, mix_path, Path(out), dur)


def remix_mesopotamia() -> Path:
    """Baghdad / Mesopotamia battery — music remix only (existing EL VO + video)."""
    return remix_reel_audio(
        video=CLIPS / "reel_mesopotamia_s__battery___electri_v01.mp4",
        voice=CLIPS / "the_baghdad_battery_8cb4cd_v01_v01_voice_el.mp3",
        sfx=CLIPS / "the_baghdad_battery_8cb4cd_v01_v01_atmosphere_sfx.mp3",
        regen_music=True,
        topic=(
            "The Baghdad Battery — Mesopotamian clay vessels that appear to be "
            "ancient galvanic cells; solemn temple mystery underscore"
        ),
        stem="the_baghdad_battery_8cb4cd_v01",
        out=CLIPS / "reel_mesopotamia_audio_remix_mystery_v01.mp4",
    )


def remux_gobekli() -> Path:
    """Legacy one-shot: re-duck existing Göbekli beds onto progressive reel."""
    cfg = _load_mix_defaults()
    voice = CLIPS / "göbekli_tepe_was_0e1f75_v01_v01_voice.mp3"
    if not voice.is_file():
        voice = next(p for p in CLIPS.glob("*0e1f75*_voice.mp3"))
    music = next(p for p in CLIPS.glob("*0e1f75*_music_v2.mp3") if "remix" not in p.name)
    sfx = next(p for p in CLIPS.glob("*0e1f75*_atmosphere_sfx.mp3"))
    reel = next(p for p in CLIPS.glob("reel_*bekli*.mp4") if "remux" not in p.name.lower())
    print("Göbekli sources:", voice.name, music.name, reel.name)
    mix, dur = mix_beds(
        voice,
        music,
        CLIPS / "gobekli_remux_audio.wav",
        sfx=sfx,
        bgm_vol=cfg["bgm_vol"],
        sfx_vol=cfg["sfx_vol"],
        duck=cfg["duck"],
        bgm_start=cfg["bgm_start"],
    )
    out = CLIPS / "reel_gobekli_tepe_remux_music_m8pct_v01.mp4"
    return mux_audio_onto_video(reel, mix, out, dur)


def main() -> None:
    p = argparse.ArgumentParser(description="AK audio-only remix (no image/TTS regen)")
    sub = p.add_subparsers(dest="cmd")

    m = sub.add_parser("mesopotamia", help="Remix Mesopotamia battery reel music only")
    m.set_defaults(func=lambda _a: remix_mesopotamia())

    g = sub.add_parser("gobekli", help="Re-duck existing Göbekli beds")
    g.set_defaults(func=lambda _a: remux_gobekli())

    r = sub.add_parser("remix", help="Generic audio-only remix")
    r.add_argument("--video", required=True, type=Path)
    r.add_argument("--voice", required=True, type=Path)
    r.add_argument("--out", required=True, type=Path)
    r.add_argument("--music", type=Path, default=None)
    r.add_argument("--sfx", type=Path, default=None)
    r.add_argument("--regen-music", action="store_true")
    r.add_argument("--topic", default="")
    r.add_argument("--stem", default="remix")
    r.set_defaults(
        func=lambda a: remix_reel_audio(
            video=a.video,
            voice=a.voice,
            out=a.out,
            music=a.music,
            sfx=a.sfx,
            regen_music=a.regen_music,
            topic=a.topic,
            stem=a.stem,
        )
    )

    args = p.parse_args()
    if not args.cmd:
        # Safe default: Mesopotamia music remix only (no full regenerations).
        args.cmd = "mesopotamia"
        out = remix_mesopotamia()
    else:
        out = args.func(args)
    print("WROTE", out, out.stat().st_size)


if __name__ == "__main__":
    main()
