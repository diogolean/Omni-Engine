"""A voiced line must move the mouth: the envelope fallback (no Rhubarb) and the frame QA."""
import shutil
import subprocess
import wave

import numpy as np
import pytest

from products.animind_core import frameqa, render

SR = render.SAMPLE_RATE
OPEN = {"C", "D", "E"}


def _speech(seconds: float = 2.0, syllables_hz: float = 4.0) -> np.ndarray:
    """A voice-like signal: a 160 Hz tone gated into syllables, with a soft and a loud half."""
    t = np.arange(int(seconds * SR)) / SR
    gate = np.clip(np.sin(2 * np.pi * syllables_hz * t), 0, None) ** 2
    loud = np.where(t < seconds / 2, 0.35, 1.0)
    return (np.sin(2 * np.pi * 160 * t) * gate * loud * 20000).astype(np.int16)


def test_envelope_fallback_varies_the_mouth_after_smoothing():
    cues = render.smooth_visemes([{"t": t, "v": v} for t, v in render.envelope_visemes(_speech())])
    shapes = [c["v"] for c in cues]
    assert len(set(shapes)) >= 4, shapes  # not one frozen "X"
    assert OPEN & set(shapes), shapes  # loud syllables open the jaw
    assert "X" in shapes  # the gaps between syllables close it
    assert all(a != b for a, b in zip(shapes, shapes[1:]))
    assert all(b["t"] - a["t"] >= 1 / 24 for a, b in zip(cues, cues[1:]))  # every mouth lasts a frame


def test_envelope_fallback_rests_on_silence():
    assert render.envelope_visemes(np.zeros(SR, np.int16)) == [(0.0, "X")]


def test_line_visemes_without_rhubarb_is_not_a_frozen_mouth(tmp_path, monkeypatch):
    from animation.parametric import rhubarb
    monkeypatch.setattr(rhubarb, "analyze_visemes", lambda *a, **k: [])  # no RHUBARB_PATH
    clip = tmp_path / "line.wav"
    with wave.open(str(clip), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(_speech().tobytes())
    cues, source = render.line_visemes(clip, "uma fala qualquer", 3.0)
    assert source == "envelope"
    assert cues[0]["t"] >= 3.0
    assert len({c["v"] for c in cues}) >= 4


# --- frame QA -----------------------------------------------------------------------------------

W, H, FPS = 160, 120, 24
BOX = (40, 40, 120, 80)


def _mp4(path, opened: list[bool]):
    frames = []
    for o in opened:
        img = np.full((H, W, 3), (235, 195, 170), np.uint8)  # skin
        if o:
            img[52:70, 62:98] = (60, 20, 20)  # a dark cavity well inside the mouth box
        frames.append(img)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                    "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv444p", "-qp", "0", str(path)],
                   input=b"".join(f.tobytes() for f in frames), check=True, timeout=60)
    return path


def _trace(visemes: list[str], speech: bool = True):
    tr = {"fps": FPS, "size": [W, H], "frames": [
        {"t": round(i / FPS, 3), "takes": 1, "blur": 0.0,
         "mouths": [{"char": 0, "v": v, "layers": 1, "view": "front", "box": list(BOX), "ref": {"cav": 0.5, "blobs": 1}}]}
        for i, v in enumerate(visemes)]}
    if speech:
        tr["speech"] = [{"start": 0.0, "end": len(visemes) / FPS, "speaker": 0}]
    return tr


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@needs_ffmpeg
def test_frameqa_fails_a_shut_mouth_during_speech(tmp_path):
    n = 36
    video = _mp4(tmp_path / "shut.mp4", [False] * n)
    out = frameqa.check(video, _trace(["X"] * n))
    assert not out["pass"]
    assert out["maxOpen"] == 0
    text = " ".join(e[1] for e in out["examples"])
    assert "maxOpen 0" in text and "frozen on X" in text


@needs_ffmpeg
def test_frameqa_fails_drawn_shapes_that_never_open_on_screen(tmp_path):
    n = 36
    video = _mp4(tmp_path / "shut.mp4", [False] * n)
    out = frameqa.check(video, _trace(["X", "B", "C", "B"] * (n // 4)))
    assert not out["pass"]
    assert any("never opens" in e[1] or "maxOpen 0" in e[1] for e in out["examples"])


@needs_ffmpeg
def test_frameqa_passes_a_talking_mouth(tmp_path):
    vis = ["X", "B", "C", "D", "C", "B"] * 6
    video = _mp4(tmp_path / "talk.mp4", [v in OPEN for v in vis])
    out = frameqa.check(video, _trace(vis))
    assert out["pass"], out["examples"]
    assert out["maxOpen"] > frameqa.OPEN_MIN


@needs_ffmpeg
def test_frameqa_without_speech_spans_keeps_the_old_contract(tmp_path):
    """Silent clips (or traces from before speech spans) are not judged for a moving mouth."""
    n = 24
    video = _mp4(tmp_path / "silent.mp4", [False] * n)
    assert frameqa.check(video, _trace(["X"] * n, speech=False))["pass"]


def test_speech_check_skips_short_lines_and_off_screen_speakers():
    tr = _trace(["X"] * 6)  # 0.25 s line: too short to judge
    assert frameqa.speech_check(tr, {}) == []
    tr = _trace(["X"] * 30)
    tr["speech"][0]["speaker"] = 1  # the speaker is not on screen
    assert frameqa.speech_check(tr, {}) == []
