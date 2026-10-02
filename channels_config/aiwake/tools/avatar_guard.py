"""Frame guard: the face on screen has to be the model that wrote the line."""

from __future__ import annotations

import json
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

import imageio_ffmpeg
import numpy as np
from PIL import Image

from channels_config.aiwake.avatars import facing_for, model_family, render_manifest

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


_FRAME_TEMPLATES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "frames"
_PLATE_BOX = (0.40, 0.20, 0.60, 0.30)


def _plate_image(image: Image.Image, box: tuple[float, float, float, float] = _PLATE_BOX) -> np.ndarray:
    width, height = image.size
    crop = image.convert("RGB").crop(
        (int(width * box[0]), int(height * box[1]), int(width * box[2]), int(height * box[3]))
    )
    return np.asarray(crop.resize((64, 32)), dtype=np.float32)


def _ncc(left: np.ndarray, right: np.ndarray) -> float:
    a = left - float(left.mean())
    b = right - float(right.mean())
    denom = float(np.sqrt(np.sum(a * a) * np.sum(b * b)))
    if denom <= 1e-6:
        return -1.0
    return float(np.sum(a * b) / denom)


def _plate_templates(templates: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Nameplate crops from the full-frame fixtures, plus the loaded head templates."""
    found = {}
    for path in _FRAME_TEMPLATES.glob("*.jpg"):
        family = {"gpt4o": "gpt-4o"}.get(path.stem, path.stem)
        found[family] = _plate_image(Image.open(path))
    for family, image in templates.items():
        found.setdefault(family, image)
    return found


_PLATE_WORDS = (
    ("DEEPSEEK", "deepseek"),
    ("CHATGPT", "gpt-4o"),
    ("GEMINI", "gemini"),
    ("CLAUDE", "claude"),
    ("LLAMA", "llama"),
)


def _family_from_plate_text(text: str) -> str:
    letters = "".join(ch for ch in (text or "").upper() if ch.isalpha())
    if not letters:
        return ""
    for word, family in _PLATE_WORDS:
        if word in letters:
            return family
        if len(letters) >= 5 and (word.startswith(letters) or letters.startswith(word[:5])):
            return family
    return ""


def _ocr_text(image: Image.Image) -> str:
    try:
        import asyncio

        from winocr import recognize_pil
    except ImportError:
        return ""
    try:
        result = asyncio.run(recognize_pil(image.convert("RGB"), "en"))
    except RuntimeError:
        return ""
    return str(getattr(result, "text", "") or "")


def _located_plate(image: Image.Image) -> Image.Image | None:
    """Find the metal nameplate rectangle and return that crop."""
    try:
        import cv2
    except ImportError:
        return None
    frame = np.asarray(image.convert("RGB"))
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    source = cv2.imread(str(_FRAME_TEMPLATES / "deepseek.jpg"), cv2.IMREAD_GRAYSCALE)
    if source is None:
        return None
    height, width = source.shape
    template = source[int(height * 0.23) : int(height * 0.33), int(width * 0.32) : int(width * 0.74)]
    upper = gray[: int(gray.shape[0] * 0.7)]
    best = None
    for factor in (0.6, 0.85, 1.1, 1.4, 1.8):
        resized = cv2.resize(
            template,
            (max(12, int(template.shape[1] * factor)), max(12, int(template.shape[0] * factor))),
        )
        if resized.shape[0] >= upper.shape[0] or resized.shape[1] >= upper.shape[1]:
            continue
        result = cv2.matchTemplate(upper, resized, cv2.TM_CCOEFF_NORMED)
        _min, score, _min_loc, loc = cv2.minMaxLoc(result)
        if best is None or score > best[0]:
            best = (float(score), loc, resized.shape)
    if best is None or best[0] < 0.45:
        return None
    x, y = best[1]
    plate_h, plate_w = best[2]
    # The match sits on the top of the plate. Keep the letters that hang below it.
    y1 = min(frame.shape[0], y + int(plate_h * 1.8))
    crop = frame[y:y1, x : x + plate_w]
    if crop.size == 0:
        return None
    return Image.fromarray(crop)


def _ocr_family(image: Image.Image) -> str:
    """Read the metal nameplate. A missed read falls through to the template."""
    width, height = image.size
    wide = image.crop((int(width * 0.18), int(height * 0.06), int(width * 0.82), int(height * 0.40)))
    wide = wide.resize((max(1, wide.width * 2), max(1, wide.height * 2)), Image.Resampling.BILINEAR)
    found = _family_from_plate_text(_ocr_text(wide))
    if found:
        return found
    located = _located_plate(image)
    if located is None:
        return ""
    located = located.resize((max(1, located.width * 4), max(1, located.height * 4)), Image.Resampling.LANCZOS)
    return _family_from_plate_text(_ocr_text(located))


def _template_family(image: Image.Image, templates: dict[str, np.ndarray] | None) -> tuple[str, float]:
    plates = _plate_templates(templates or _load_templates())
    sample = _plate_image(image)
    best_family = ""
    best_score = -1.0
    for family, plate in plates.items():
        score = _ncc(sample, plate)
        if score > best_score:
            best_score = score
            best_family = family
    if best_score < 0.35:
        return "", best_score
    return best_family, best_score


def nameplate_family(image: Image.Image, templates: dict[str, np.ndarray] | None = None) -> str:
    """Read the nameplate. The template crop is only the fallback."""
    read = _ocr_family(image)
    if read:
        return read
    found, score = _template_family(image, templates)
    if found and score >= 0.45:
        return found
    return ""


def body_center_x(image: Image.Image) -> float:
    """Horizontal center of the character, ignoring the side borders."""
    frame = np.asarray(image.convert("RGB"), dtype=np.float32)
    height, width, _ = frame.shape
    band = frame[int(height * 0.15) : int(height * 0.70)]
    border = np.concatenate([frame[:, :6].reshape(-1, 3), frame[:, -6:].reshape(-1, 3)])
    background = np.median(border, axis=0)
    mask = np.sqrt(np.sum((band - background) ** 2, axis=2)) > 40
    columns = mask.sum(axis=0)
    if float(columns.sum()) < 10:
        return width / 2
    xs = np.arange(width)
    return float(np.sum(xs * columns) / columns.sum())


def seat_matches_frame(image: Image.Image, seat: str) -> bool:
    """Left seat stays off the right half. Right seat stays off the far left."""
    center = body_center_x(image)
    width = image.size[0]
    if seat == "left":
        return center <= width * 0.51
    if seat == "right":
        return center >= width * 0.42
    return False


def _scan_frames(video: Path, session_id: str, templates: dict[str, np.ndarray]) -> list[dict[str, Any]]:
    """One ffmpeg pass, every 2s from 1s. Generated frames only; the video stays."""
    folder = Path.cwd() / "outputs" / "aiwake" / "guard_frames" / session_id
    folder.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    pattern = folder / f"s{stamp}_%02d.jpg"
    subprocess.run(
        [FFMPEG, "-y", "-ss", "1", "-i", str(video), "-vf", "fps=1/2", "-frames:v", "24", str(pattern)],
        check=False,
        capture_output=True,
    )
    samples = []
    for index, path in enumerate(sorted(folder.glob(f"s{stamp}_*.jpg"))):
        if path.stat().st_size < 1000:
            continue
        frame = Image.open(path).convert("RGB")
        samples.append(
            {
                "t": 1 + index * 2,
                "seen": nameplate_family(frame, templates),
                "center_x": round(body_center_x(frame), 1),
                "frame": frame,
            }
        )
    return samples


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
    seated = {int(item.get("turn") or index): item for index, item in enumerate(manifest.get("turns") or [])}
    # The wav clock lags the cut. Scan the picture and require each turn's model
    # on that turn's side, instead of trusting one timestamp.
    samples = _scan_frames(video, session_id, templates)
    checked = []
    for index, turn in enumerate(turns):
        expected = model_family(str(turn.get("model_slug") or turn.get("speaker_name") or ""))
        role = str(turn.get("role") or "")
        seat = str((seated.get(index) or {}).get("seat") or ("left" if role == "orchestrator" else "right"))
        facing = ""
        try:
            facing = facing_for(expected, seat) if expected else ""
        except ValueError:
            reasons.append("illegal_seat")
        hits = [
            sample
            for sample in samples
            if sample["seen"] == expected and seat_matches_frame(sample["frame"], seat)
        ]
        seen = hits[0]["seen"] if hits else next((sample["seen"] for sample in samples if sample["seen"] == expected), "")
        side_ok = bool(hits)
        if not hits:
            reasons.append("avatar_mismatch" if not seen else "screen_seat")
        checked.append(
            {
                "turn": index,
                "expected": expected,
                "seen": seen or (samples[0]["seen"] if samples else ""),
                "seat": seat,
                "facing": facing,
                "center_x": hits[0]["center_x"] if hits else None,
                "side_ok": side_ok,
            }
        )
    status = "fail" if reasons else "pass"
    return {
        "status": status,
        "reasons": sorted(set(reasons)),
        "guard": status,
        "render_manifest": manifest,
        "frames": checked,
    }
