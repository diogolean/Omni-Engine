"""Parametric renders. OpenCV is optional on a bare checkout; CI installs it."""
from __future__ import annotations

import json
import math
import subprocess
import wave
from pathlib import Path

import pytest

pytest.importorskip("cv2")

import numpy as np

import animation.api
import animation.parametric.audio_analyzer as audio_impl
import animation.parametric.compositor as compositor_impl
import animation.parametric.puppet as puppet_impl
import animation.parametric.renderer as renderer_impl
import core.animator as animator_facade
import core.animator.audio_analyzer as audio_shim
import core.animator.compositor as compositor_shim
import core.animator.factory.rigger as rigger_shim
import core.animator.puppet as puppet_shim
import core.animator.renderer as renderer_shim
from animation.parametric.animator_types import DialogueTurn, SpeakerStyle
from animation.parametric.audio_analyzer import AudioAnalyzer
from core.animator.aiwake_plate import apply_puppet


def _silent_wav(path: Path, seconds: float, rate: int = 8000) -> None:
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))


def _media_duration(path: Path) -> float:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(probe.stdout.strip())


def _render(render, puppets: Path, character_id: str, audio: Path, output: Path, **extra):
    return render(
        turns=[
            DialogueTurn(
                speaker=character_id,
                start_time=0.0,
                end_time=extra.pop("turn_end", 0.3),
                text="hello",
            )
        ],
        audio_path=audio,
        styles=[SpeakerStyle(character_id=character_id, label="Host")],
        output_path=output,
        puppets_dir=puppets,
        fps=10,
        width=1080,
        height=640,
        burn_subtitles=False,
        use_rhubarb=False,
        **extra,
    )


def test_leaf_shims_are_the_implementation() -> None:
    assert renderer_shim is renderer_impl
    assert puppet_shim is puppet_impl
    assert compositor_shim is compositor_impl
    assert audio_shim is audio_impl
    assert audio_shim.AudioAnalyzer is AudioAnalyzer
    assert renderer_shim.AnimationRenderer is renderer_impl.AnimationRenderer
    assert puppet_shim.PuppetRig is puppet_impl.PuppetRig
    assert animator_facade.render_dynamic_animation is animation.api.render_dynamic_animation


def test_aiwake_plate_is_installed_by_the_shim() -> None:
    assert compositor_impl.DEEPSEEK_LOCKED_IDS == frozenset({"deepseek_cyborg_v3"})
    assert compositor_impl.LEGACY_ARTIST_V2_IDS == frozenset(
        {"gemini_cyborg_v2", "llama_cyborg_v2"}
    )
    assert compositor_impl.GEMINI_LEAD_X == 420
    assert compositor_impl.LLAMA_LEAD_X == 660
    assert puppet_impl.character_eye_nudge("gemini_cyborg_v2", 1) == (2, 0)
    assert rigger_shim.palette_overrides("deepseek_cyborg_v3", {})["cavity_interior"] == (
        "#151820"
    )


def test_empty_puppets_folder_renders_on_both_paths(tmp_path: Path) -> None:
    wav = tmp_path / "short.wav"
    _silent_wav(wav, 0.3)
    puppet_impl.use_placeholder_asset_provider()
    try:
        plain = _render(
            animation.api.render_dynamic_animation,
            tmp_path / "plain",
            "plain_host",
            wav,
            tmp_path / "plain.mp4",
        )
        manifest = json.loads(
            (tmp_path / "plain" / "plain_host" / "puppet.json").read_text(encoding="utf-8")
        )
        assert manifest["asset_provider"] == "neutral-placeholder"
        assert plain.frames_written >= 1
        assert (tmp_path / "plain.mp4").stat().st_size > 0
    finally:
        apply_puppet(puppet_impl)

    branded = _render(
        animator_facade.render_dynamic_animation,
        tmp_path / "branded",
        "branded_host",
        wav,
        tmp_path / "branded.mp4",
    )
    manifest = json.loads(
        (tmp_path / "branded" / "branded_host" / "puppet.json").read_text(encoding="utf-8")
    )
    assert "asset_provider" not in manifest
    assert "palette" in manifest
    assert branded.frames_written >= 1
    assert (tmp_path / "branded.mp4").stat().st_size > 0


def test_duration_override_shortens_the_rendered_file(tmp_path: Path) -> None:
    wav = tmp_path / "long.wav"
    _silent_wav(wav, 2.0)
    puppet_impl.use_placeholder_asset_provider()
    try:
        stats = _render(
            animation.api.render_dynamic_animation,
            tmp_path / "puppets",
            "host",
            wav,
            tmp_path / "short.mp4",
            duration_override=0.4,
            turn_end=2.0,
        )
    finally:
        apply_puppet(puppet_impl)
    assert stats.frames_written == math.ceil(0.4 * 10)
    assert 0.25 <= _media_duration(tmp_path / "short.mp4") <= 0.6


def test_short_audio_stops_at_the_waveform(tmp_path: Path) -> None:
    wav = tmp_path / "short.wav"
    _silent_wav(wav, 0.3)
    puppet_impl.use_placeholder_asset_provider()
    try:
        stats = _render(
            animation.api.render_dynamic_animation,
            tmp_path / "puppets",
            "host",
            wav,
            tmp_path / "clip.mp4",
            turn_end=8.0,
        )
    finally:
        apply_puppet(puppet_impl)
    assert stats.frames_written == math.ceil(0.3 * 10)
    assert _media_duration(tmp_path / "clip.mp4") < 1.0


def test_ffmpeg_failure_includes_stderr(tmp_path: Path) -> None:
    renderer = renderer_impl.AnimationRenderer(width=16, height=16, fps=10)
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    out = tmp_path / "out.mp4"
    cmd = [
        renderer.ffmpeg_path,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(tmp_path / "missing-input.wav"),
        str(out),
    ]
    with pytest.raises(renderer_impl._FfmpegFailure) as caught:
        renderer._pipe_frames(cmd, [frame], out)
    message = str(caught.value)
    assert message.split("::", 1)[-1].strip()
    assert "empty stderr" not in message
    assert "No such file" in message or "Error" in message


def test_closed_pipe_with_a_finished_file_is_success(tmp_path: Path, monkeypatch) -> None:
    """Exit 0 plus a finished file is a clean stop, not 'Broken pipe'."""
    out = tmp_path / "done.mp4"
    out.write_bytes(b"ok")

    class _Stdin:
        closed = False

        def write(self, _data: bytes) -> None:
            raise BrokenPipeError(32, "Broken pipe")

        def close(self) -> None:
            self.closed = True

    class _Proc:
        def __init__(self) -> None:
            self.stdin = _Stdin()
            self.stdout = None
            self.stderr = None
            self.returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    monkeypatch.setattr(renderer_impl.subprocess, "Popen", lambda *_a, **_k: _Proc())
    renderer = renderer_impl.AnimationRenderer(width=16, height=16, fps=10)
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    written, _elapsed = renderer._pipe_frames(["ffmpeg"], [frame], out)
    assert written == 0
    assert out.read_bytes() == b"ok"


def test_closed_pipe_without_a_file_includes_the_return_code(
    tmp_path: Path, monkeypatch
) -> None:
    out = tmp_path / "missing.mp4"

    class _Stdin:
        closed = False

        def write(self, _data: bytes) -> None:
            raise BrokenPipeError(32, "Broken pipe")

        def close(self) -> None:
            self.closed = True

    class _Stream:
        def read(self) -> bytes:
            return b""

    class _Proc:
        def __init__(self) -> None:
            self.stdin = _Stdin()
            self.stdout = _Stream()
            self.stderr = _Stream()
            self.returncode = 1

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 1

    monkeypatch.setattr(renderer_impl.subprocess, "Popen", lambda *_a, **_k: _Proc())
    renderer = renderer_impl.AnimationRenderer(width=16, height=16, fps=10)
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    with pytest.raises(renderer_impl._FfmpegFailure) as caught:
        renderer._pipe_frames(["ffmpeg"], [frame], out)
    message = str(caught.value)
    assert "Broken pipe" in message
    assert "exited 1" in message
    assert "empty stderr" in message


def test_shortest_keeps_the_file_when_audio_is_shorter(tmp_path: Path) -> None:
    wav = tmp_path / "short.wav"
    _silent_wav(wav, 0.2)
    renderer = renderer_impl.AnimationRenderer(width=32, height=32, fps=10)
    out = tmp_path / "out.mp4"
    cmd = renderer._build_cmd(
        audio_path=wav,
        output_path=out,
        use_gpu=False,
        audio_codec="aac",
    )
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    renderer._pipe_frames(cmd, (frame for _ in range(40)), out)
    assert out.is_file() and out.stat().st_size > 0
    assert _media_duration(out) < 1.0
