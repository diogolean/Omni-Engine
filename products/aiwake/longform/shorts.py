# -*- coding: utf-8 -*-
"""Two or three vertical teasers from one horizontal episode.

The cut reads a bounded slice of the master (``-ss`` then ``-t``). When the
master is missing, a finite color plate plus the same slice of the battle
audio stands in. Neither path uses ``apad`` or ``amix=longest``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from products.aiwake.longform.ffmpeg_guard import ffmpeg_binary, run_bounded
from products.aiwake.longform.metadata import ChapterMark, short_metadata
from products.aiwake.longform.plan import EpisodePlan
from products.aiwake.settings import LongFormatPreset

# Dual-presence anchors on a 1920-wide stage, as fractions.
_LEFT_ANCHOR = 420 / 1920
_RIGHT_ANCHOR = 1500 / 1920


@dataclass(frozen=True)
class ShortClip:
    path: Path
    start_s: float
    duration_s: float
    side: str
    metadata: dict[str, object]
    regenerated: bool


def choose_windows(
    *,
    duration_s: float,
    chapters: list[ChapterMark],
    count: int,
    target_s: float,
    min_s: float,
    max_s: float,
) -> list[tuple[float, float, str]]:
    """Return ``(start, end, side)`` windows inside the episode.

    Middle rounds are preferred. Windows do not overlap and each one is at
    least ``min_s`` when the master is long enough to allow it.
    """
    span = min(max_s, max(min_s, target_s))
    sides = ("left", "right", "left")
    windows: list[tuple[float, float, str]] = []
    rounds = [chapter for chapter in chapters if chapter.kind == "round"]
    chapter_ends = _chapter_ends(chapters, duration_s)
    for index, chapter in enumerate(rounds):
        if len(windows) >= count:
            break
        end_at = chapter_ends.get(chapter.title, duration_s)
        start = chapter.start_s + min(2.0, max(0.0, end_at - chapter.start_s) * 0.08)
        placed = _place(start, span, min_s, duration_s, windows)
        if placed is None:
            continue
        windows.append((placed[0], placed[1], sides[index % len(sides)]))
    fractions = (0.18, 0.48, 0.72)
    fraction_index = 0
    while len(windows) < count and fraction_index < len(fractions):
        start = max(0.0, duration_s * fractions[fraction_index])
        fraction_index += 1
        placed = _place(start, span, min_s, duration_s, windows)
        if placed is None:
            continue
        windows.append((placed[0], placed[1], sides[len(windows) % len(sides)]))
    return windows[:count]


def cut_or_regenerate_shorts(
    *,
    master: Path | None,
    battle_audio: Path | None,
    output_dir: Path,
    plan: EpisodePlan,
    chapters: list[ChapterMark],
    preset: LongFormatPreset,
    long_title: str,
    topic: str,
    duration_s: float,
    count: int,
) -> list[ShortClip]:
    """Cut teasers from the master, or rebuild them from the battle audio."""
    output_dir.mkdir(parents=True, exist_ok=True)
    windows = choose_windows(
        duration_s=duration_s,
        chapters=chapters,
        count=count,
        target_s=float(preset.shorts_target_s),
        min_s=float(preset.shorts_min_s),
        max_s=59.0,
    )
    clips: list[ShortClip] = []
    for index, (start, end, side) in enumerate(windows, start=1):
        length = end - start
        destination = output_dir / f"short_{index:02d}.mp4"
        regenerated = False
        if master is not None and master.is_file():
            _cut_master(
                master,
                destination,
                start=start,
                length=length,
                side=side,
                width=int(preset.shorts_width),
                height=int(preset.shorts_height),
            )
        elif battle_audio is not None and battle_audio.is_file():
            _regenerate_plate(
                battle_audio,
                destination,
                start=start,
                length=length,
                width=int(preset.shorts_width),
                height=int(preset.shorts_height),
            )
            regenerated = True
        else:
            continue
        chapter_title = _chapter_title_at(chapters, start)
        clip_title = f"{long_title} — {chapter_title}"
        meta = short_metadata(
            long_title=long_title,
            topic=topic,
            clip_title=clip_title,
            start_s=start,
            duration_s=length,
        )
        meta["side"] = side
        meta["regenerated"] = regenerated
        meta["file"] = destination.name
        (output_dir / f"short_{index:02d}.json").write_text(
            json.dumps(meta, indent=2) + "\n",
            encoding="utf-8",
        )
        clips.append(
            ShortClip(
                path=destination,
                start_s=start,
                duration_s=length,
                side=side,
                metadata=meta,
                regenerated=regenerated,
            )
        )
    return clips


def _cut_master(
    master: Path,
    destination: Path,
    *,
    start: float,
    length: float,
    side: str,
    width: int,
    height: int,
) -> None:
    crop = _vertical_crop(side)
    # -ss and -t before -i bound how much of the master is decoded.
    cmd = [
        ffmpeg_binary(),
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{length:.3f}",
        "-i",
        str(master),
        "-vf",
        f"{crop},scale={width}:{height}:flags=lanczos,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    run_bounded(cmd, timeout=max(60.0, length * 8.0))


def _regenerate_plate(
    audio: Path,
    destination: Path,
    *,
    start: float,
    length: float,
    width: int,
    height: int,
) -> None:
    """Finite color plate. ``d=`` on the lavfi source stops the generator."""
    cmd = [
        ffmpeg_binary(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x0B1020:s={width}x{height}:r=30:d={length:.3f}",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{length:.3f}",
        "-i",
        str(audio),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-t",
        f"{length:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    run_bounded(cmd, timeout=max(60.0, length * 8.0))


def _vertical_crop(side: str) -> str:
    """9:16 window aimed at one fighter, expressed as fractions of the master."""
    anchor = {"left": _LEFT_ANCHOR, "right": _RIGHT_ANCHOR}.get(side, 0.5)
    # crop width is 9/16 of the frame height. On 16:9 that is 0.316 of the width.
    crop_w = "ih*9/16"
    x = f"max(0\\,min(iw-({crop_w})\\,iw*{anchor:.4f}-({crop_w})/2))"
    return f"crop={crop_w}:ih:{x}:0"


def _place(
    start: float,
    span: float,
    min_s: float,
    duration_s: float,
    existing: list[tuple[float, float, str]],
) -> tuple[float, float] | None:
    if duration_s < min_s:
        return None
    begin = max(0.0, start)
    if existing and begin < existing[-1][1] + 0.5:
        begin = existing[-1][1] + 0.5
    end = min(duration_s, begin + span)
    if end - begin < min_s:
        end = min(duration_s, begin + min_s)
    if end - begin < min_s:
        begin = max(0.0, duration_s - min_s)
        end = duration_s
    if existing and begin < existing[-1][1] + 0.25:
        return None
    if end - begin < min_s - 0.05:
        return None
    return begin, end


def _chapter_ends(chapters: list[ChapterMark], duration_s: float) -> dict[str, float]:
    ordered = sorted(chapters, key=lambda item: item.start_s)
    ends: dict[str, float] = {}
    for index, chapter in enumerate(ordered):
        nxt = ordered[index + 1].start_s if index + 1 < len(ordered) else duration_s
        ends[chapter.title] = nxt
    return ends


def _chapter_title_at(chapters: list[ChapterMark], start_s: float) -> str:
    current = chapters[0].title if chapters else "Teaser"
    for chapter in chapters:
        if chapter.start_s <= start_s + 0.05:
            current = chapter.title
    return current
