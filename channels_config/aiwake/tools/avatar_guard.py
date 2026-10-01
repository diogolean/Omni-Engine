"""Frame guard: the face on screen has to be the model that wrote the line."""

from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path
from typing import Any

import imageio_ffmpeg
import numpy as np
from PIL import Image

from channels_config.aiwake.avatars import model_family, render_manifest

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
_TEMPLATES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "nameplates"
_FAMILIES = ("gemini", "llama", "gpt-4o", "claude", "deepseek")


def _load_templates() -> dict[str, np.ndarray]:
    found = {}
    for family in _FAMILIES:
        path = _TEMPLATES / f"{family.replace('-', '')}.png"
        if not path.is_file():
            continue
        image = Image.open(path).convert("RGB").resize((96, 96))
        found[family] = np.asarray(image, dtype=np.float32)
    if len(found) < 4:
        raise FileNotFoundError(f"nameplate templates missing in {_TEMPLATES}")
    return found


def _head(image: Image.Image) -> np.ndarray:
    width, height = image.size
    crop = image.crop((int(width * 0.22), int(height * 0.08), int(width * 0.78), int(height * 0.42)))
    return np.asarray(crop.convert("RGB").resize((96, 96)), dtype=np.float32)


# Mean RGB of the nameplate band. Claude and Llama helmets are close; the
# plate itself is not (Claude sits near 188,116,82 and Llama near 149,119,104).
_PLATE = {
    "gemini": (137.0, 154.9, 149.0),
    "llama": (149.4, 119.1, 104.1),
    "gpt-4o": (192.3, 158.6, 125.6),
    "claude": (187.9, 116.0, 82.2),
    "deepseek": (109.9, 113.3, 121.4),
}


def _plate_color(image: Image.Image) -> np.ndarray:
    width, height = image.size
    crop = image.crop((int(width * 0.40), int(height * 0.22), int(width * 0.60), int(height * 0.30)))
    return np.asarray(crop.convert("RGB"), dtype=np.float32).mean(axis=(0, 1))


def nameplate_family(image: Image.Image, templates: dict[str, np.ndarray] | None = None) -> str:
    """Nearest forehead color. The five plates do not overlap."""
    del templates
    color = _plate_color(image)
    best_family = ""
    best_score = 1e18
    for family, plate in _PLATE.items():
        score = float(np.sum((color - np.asarray(plate)) ** 2))
        if score < best_score:
            best_score = score
            best_family = family
    return best_family


def _frame_at(video: Path, seconds: float, destination: Path) -> Image.Image | None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-ss", f"{max(0.2, seconds):.2f}", "-i", str(video), "-frames:v", "1", str(destination)],
        check=False,
        capture_output=True,
    )
    if not destination.is_file() or destination.stat().st_size < 1000:
        return None
    return Image.open(destination).convert("RGB")


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate() or 1)


def _turn_wavs(video: Path, session_id: str) -> list[Path]:
    candidates = [
        video.parent / f"{session_id}_battle_audio_turns",
        video.with_name(video.stem + "_audio_turns"),
    ]
    for folder in candidates:
        if folder.is_dir():
            return sorted(folder.glob("turn_*.wav"))
    return []


def _jsonl(session_id: str, transcripts: Path) -> list[dict[str, Any]]:
    path = transcripts / f"{session_id}.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def guard_video(
    video: Path,
    session_id: str,
    *,
    transcripts: Path,
    probe: dict[str, Any] | None = None,
    durations: list[float] | None = None,
) -> dict[str, Any]:
    """Check nameplates, duration, and silence. Returns a quality_review body."""
    reasons: list[str] = []
    turns = _jsonl(session_id, transcripts)
    try:
        manifest = render_manifest(turns) if turns else {"turns": []}
    except ValueError as exc:
        manifest = {"turns": [], "error": str(exc)}
        reasons.append("avatar_mismatch")
    wavs = _turn_wavs(video, session_id)
    if durations is None:
        durations = []
        for wav in wavs:
            try:
                durations.append(_wav_duration(wav))
            except (wave.Error, OSError):
                reasons.append("turn_audio_unreadable")
    if probe:
        if probe.get("long_silence"):
            reasons.append("silence")
        if not probe.get("video_ok") or not probe.get("audio_ok"):
            reasons.append("codec_or_frame")
        duration = float(probe.get("duration") or 0)
        audio_sum = sum(durations)
        if audio_sum and duration + 0.05 < audio_sum:
            reasons.append("shorter_than_turn_audio")
        if not wavs:
            reasons.append("last_utterance_missing")
        elif duration and audio_sum >= duration - 0.05:
            reasons.append("last_utterance_not_inside_video")
    if not turns:
        reasons.append("jsonl_missing")
    templates = _load_templates()
    cursor = 0.4
    checked = []
    for index, turn in enumerate(turns):
        expected = model_family(str(turn.get("model_slug") or turn.get("speaker_name") or ""))
        span = durations[index] if index < len(durations) else 3.0
        moment = cursor + max(0.4, span * 0.45)
        frame = _frame_at(video, moment, Path.cwd() / "outputs" / "aiwake" / "guard_frames" / f"{session_id}_{index}.jpg")
        seen = nameplate_family(frame, templates) if frame is not None else ""
        if not seen or seen != expected:
            reasons.append("avatar_mismatch")
        checked.append({"turn": index, "expected": expected, "seen": seen})
        cursor += span + 0.4
        if "avatar_mismatch" in reasons and index >= 1:
            break
    status = "fail" if reasons else "pass"
    return {
        "status": status,
        "reasons": sorted(set(reasons)),
        "guard": status,
        "render_manifest": manifest,
        "frames": checked,
    }
