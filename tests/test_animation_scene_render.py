"""Scene renders: pose, mouth, camera, a deterministic MP4, and a bounded ffmpeg."""
from __future__ import annotations

import inspect
import math
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("cv2")
pytestmark = pytest.mark.timeout(180)

import numpy as np

import animation.api as animation_api
from animation.examples.make_audio import write_sample
from animation.parametric.animator_types import AnalyzedAudio
from animation.parametric.audio_analyzer import AudioAnalyzer
from animation.parametric.renderer import AnimationRenderer, RenderTimeout
from animation.parametric.scene_compositor import SceneCompositor
from animation.parametric.scene_render import _load_rigs, _turns, effective_lines
from animation.parametric.scene_spec import load_scene, preview_geometry
from animation.parametric.puppet import PuppetRig

_REPO = Path(__file__).resolve().parents[1]


def _spec(**extra):
    data = {
        "version": 1,
        "width": 160,
        "height": 96,
        "fps": 10,
        "seed": 1,
        "characters": [
            {
                "id": "host",
                "accent": "#7EB6D9",
                "pose": [{"t": 0.0, "head_angle": 0, "y_offset": 0, "glow": 0}],
            }
        ],
        "lines": [{"speaker": "host", "start": 0.0, "end": 1.0, "text": "hello"}],
    }
    data.update(extra)
    return load_scene(data)


def _analyzed(
    n: int,
    fps: int,
    speakers: dict[str, str],
    *,
    emotion: str = "neutral",
) -> AnalyzedAudio:
    names = list(speakers)
    return AnalyzedAudio(
        fps=fps,
        duration_s=n / float(fps),
        n_frames=n,
        rms=[0.8] * n,
        active_speaker=[names[0]] * n,
        speaking_speaker=[names[0]] * n,
        mouth_state={name: [2] * n for name in names},
        eye_state={name: [0] * n for name in names},
        viseme={name: [shape] * n for name, shape in speakers.items()},
        emotion=[emotion] * n,
        camera_tight=[False] * n,
    )


def _compositor(spec, rig_root: Path, analyzed: AnalyzedAudio) -> SceneCompositor:
    return SceneCompositor(
        spec=spec,
        rigs=_load_rigs(spec, rig_root),
        lines=effective_lines(spec),
        width=spec.width,
        height=spec.height,
    )


def _ffmpeg_pids() -> set[int]:
    result = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        check=False,
        capture_output=True,
        text=True,
    )
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        command = parts[1]
        if "ffmpeg" in command and not command.startswith("ps "):
            pids.add(int(parts[0]))
    return pids


def test_render_dynamic_animation_signature_is_unchanged() -> None:
    signature = inspect.signature(animation_api.render_dynamic_animation)
    assert list(signature.parameters) == [
        "turns",
        "audio_path",
        "styles",
        "output_path",
        "puppets_dir",
        "fps",
        "width",
        "height",
        "duration_override",
        "seed",
        "burn_subtitles",
        "use_rhubarb",
        "enable_cta",
        "outro_start_s",
        "outro_frame",
        "subtitle_fade_s",
        "scene",
        "panorama_path",
        "stage_mode",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    from animation.parametric.scene_render import render_scene

    assert animation_api.render_scene is render_scene


def test_pose_mouth_camera_and_facing_change_the_frame(tmp_path: Path) -> None:
    neutral = _spec()
    turned = _spec(
        characters=[
            {
                "id": "host",
                "accent": "#7EB6D9",
                "pose": [{"t": 0.0, "head_angle": 24, "y_offset": 0, "glow": 0}],
            }
        ]
    )
    analyzed = _analyzed(2, 10, {"host": "X"})
    rest = _compositor(neutral, tmp_path / "rest", analyzed).frame(0, analyzed)
    posed = _compositor(turned, tmp_path / "posed", analyzed).frame(0, analyzed)
    assert rest.shape == (96, 160, 3)
    assert not np.array_equal(rest, posed)

    open_mouth = _analyzed(2, 10, {"host": "D"})
    speaking = _compositor(neutral, tmp_path / "mouth", open_mouth).frame(0, open_mouth)
    assert not np.array_equal(rest, speaking)

    close = _spec(camera=[{"t": 0.0, "mode": "closeup", "zoom": 1.15}])
    wide = _spec(camera=[{"t": 0.0, "mode": "full", "zoom": 1.0}])
    close_frame = _compositor(close, tmp_path / "close", analyzed).frame(0, analyzed)
    wide_frame = _compositor(wide, tmp_path / "wide", analyzed).frame(0, analyzed)
    assert not np.array_equal(close_frame, wide_frame)

    left = _spec(
        characters=[
            {
                "id": "host",
                "facing": "left",
                "pose": [{"t": 0.0, "head_angle": 0, "y_offset": 0, "glow": 0}],
            }
        ]
    )
    flipped = _compositor(left, tmp_path / "left", analyzed).frame(0, analyzed)
    assert not np.array_equal(rest, flipped)


def test_shot_cuts_to_the_speaker(tmp_path: Path) -> None:
    spec = _spec(
        stage="shot",
        characters=[
            {
                "id": "alpha",
                "accent": "#7EB6D9",
                "pose": [{"t": 0.0, "head_angle": -20, "glow": 0}],
            },
            {
                "id": "beta",
                "accent": "#C45C26",
                "pose": [{"t": 0.0, "head_angle": 20, "glow": 0}],
            },
        ],
        lines=[
            {"speaker": "alpha", "start": 0.0, "end": 0.5, "text": "alpha"},
            {"speaker": "beta", "start": 0.5, "end": 1.0, "text": "beta"},
        ],
    )
    analyzed = _analyzed(10, 10, {"alpha": "X", "beta": "X"})
    compositor = _compositor(spec, tmp_path / "shot", analyzed)
    assert not np.array_equal(compositor.frame(0, analyzed), compositor.frame(5, analyzed))


def test_frames_are_pulled_one_at_a_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec()
    analyzed = _analyzed(6, 10, {"host": "X"})
    compositor = _compositor(spec, tmp_path / "lazy", analyzed)
    calls = {"n": 0}
    original = PuppetRig.compose

    def _counting(self, **kwargs):
        calls["n"] += 1
        return original(self, **kwargs)

    monkeypatch.setattr(PuppetRig, "compose", _counting)
    frames = compositor.iter_frames(analyzed)
    assert inspect.isgenerator(frames)
    first = next(frames)
    assert first.shape == (96, 160, 3)
    assert calls["n"] == 1


def test_loud_audio_opens_the_mouth(tmp_path: Path) -> None:
    spec = _spec(fps=10, lines=[{"speaker": "host", "start": 0.0, "text": "hello"}])
    silent = tmp_path / "silent.wav"
    loud = tmp_path / "loud.wav"
    write_sample(silent, seconds=0.5)
    silent.write_bytes(loud_silence(silent))
    write_sample(loud, seconds=0.5)
    lines = effective_lines(spec)
    turns = _turns(lines, "envelope")
    rigs = _load_rigs(spec, tmp_path / "rigs")
    quiet = AudioAnalyzer(fps=10, seed=1).analyze(silent, turns, use_rhubarb=False)
    voiced = AudioAnalyzer(fps=10, seed=1).analyze(loud, turns, use_rhubarb=False)
    compositor = SceneCompositor(
        spec=spec,
        rigs=rigs,
        lines=lines,
        width=spec.width,
        height=spec.height,
    )
    index = next(
        i
        for i, (closed, opened) in enumerate(zip(quiet.viseme["host"], voiced.viseme["host"]))
        if closed != opened
    )
    assert not np.array_equal(
        compositor.frame(index, quiet),
        compositor.frame(index, voiced),
    )


def loud_silence(template: Path) -> bytes:
    """A silent wav with the same length as ``template``."""
    import wave

    with wave.open(str(template), "rb") as source:
        frames = source.getnframes()
        rate = source.getframerate()
        width = source.getsampwidth()
        channels = source.getnchannels()
    buffer = Path(str(template) + ".silent.wav")
    with wave.open(str(buffer), "w") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(b"\x00" * frames * width * channels)
    return buffer.read_bytes()


def test_two_renders_are_the_same_file(tmp_path: Path) -> None:
    wav = write_sample(tmp_path / "tone.wav", seconds=0.4)
    spec = {
        "version": 1,
        "width": 96,
        "height": 64,
        "fps": 8,
        "seed": 2,
        "characters": [{"id": "host", "pose": [{"t": 0, "head_angle": 6, "glow": 0.2}]}],
        "lines": [{"speaker": "host", "start": 0, "text": "hello"}],
    }
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    stats_a = animation_api.render_scene(spec, wav, first)
    stats_b = animation_api.render_scene(spec, wav, second)
    assert stats_a.frames_written == math.ceil(0.4 * 8)
    assert stats_a.frames_written == stats_b.frames_written
    assert first.read_bytes() == second.read_bytes()
    assert first.stat().st_size > 0


def test_preview_shrinks_the_frame(tmp_path: Path) -> None:
    wav = write_sample(tmp_path / "tone.wav", seconds=0.3)
    spec = {
        "version": 1,
        "width": 320,
        "height": 180,
        "fps": 30,
        "characters": [{"id": "host"}],
        "lines": [{"speaker": "host", "start": 0, "text": "hello"}],
    }
    out = tmp_path / "preview.mp4"
    stats = animation_api.render_scene(spec, wav, out, preview=True)
    assert (stats.width, stats.height, stats.fps) == preview_geometry(320, 180, 30)
    assert out.is_file() and out.stat().st_size > 0


def test_cli_preview_writes_an_mp4(tmp_path: Path) -> None:
    wav = write_sample(tmp_path / "tone.wav", seconds=0.3)
    spec_path = tmp_path / "scene.yaml"
    spec_path.write_text(
        "\n".join(
            [
                "version: 1",
                "width: 96",
                "height: 64",
                "fps: 10",
                "characters:",
                "  - {id: host}",
                "lines:",
                '  - {speaker: host, start: 0, text: "hi"}',
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "cli.mp4"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "animation",
            "render",
            str(spec_path),
            "--audio",
            str(wav),
            "--output",
            str(out),
            "--preview",
        ],
        cwd=_REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert out.is_file() and out.stat().st_size > 0
    assert str(out) in completed.stdout


def test_cli_help_lists_preview() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "animation", "render", "--help"],
        cwd=_REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "--preview" in completed.stdout
    assert "--audio" in completed.stdout


def test_legacy_ffmpeg_command_is_unchanged(tmp_path: Path) -> None:
    renderer = AnimationRenderer(width=32, height=32, fps=10)
    command = renderer._build_cmd(
        audio_path=tmp_path / "audio.wav",
        output_path=tmp_path / "out.mp4",
        use_gpu=False,
        audio_codec="aac",
    )
    assert "veryfast" in command
    assert "grain" in command
    assert "-threads" not in command
    assert "bitexact" not in command


def test_encode_timeout_stops_ffmpeg(tmp_path: Path) -> None:
    before = _ffmpeg_pids()

    def _slow():
        time.sleep(1.2)
        yield np.zeros((16, 16, 3), dtype=np.uint8)

    renderer = AnimationRenderer(width=16, height=16, fps=10)
    with pytest.raises(RenderTimeout):
        renderer.render(
            frame_iter=_slow(),
            audio_path=None,
            output_path=tmp_path / "late.mp4",
            duration_s=1.0,
            encode_timeout_s=0.3,
            retain_frames=False,
            preset="ultrafast",
            crf=30,
            tune=None,
            deterministic=True,
            threads=1,
        )
    leftover = _ffmpeg_pids() - before
    assert leftover == set()


def test_failed_encode_does_not_replay_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = AnimationRenderer(width=16, height=16, fps=10)
    from animation.parametric import renderer as renderer_impl

    def _fail(*_args, **_kwargs):
        raise renderer_impl._FfmpegFailure("encode failed", [])

    monkeypatch.setattr(renderer, "_pipe_frames", _fail)
    pulled = {"n": 0}

    def frames():
        pulled["n"] += 1
        yield np.zeros((16, 16, 3), dtype=np.uint8)
        raise AssertionError("frame iterator was replayed")

    with pytest.raises(RuntimeError, match="encode failed"):
        renderer._run_ffmpeg(
            frames(),
            audio_path=None,
            output_path=tmp_path / "out.mp4",
            use_gpu=False,
            retain_frames=False,
        )
    assert pulled["n"] == 0
