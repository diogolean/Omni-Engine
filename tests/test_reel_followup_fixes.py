# -*- coding: utf-8 -*-
"""Call-signature, portable media probe, and spoken-number fixes."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

from modules.reel_qa.mp4_gate import decide_mp4_gate, probe_media
from modules.reel_qa.timings import filter_spoken_timings
from modules.reel_visual_qa import generate_and_gate


def test_visual_gate_forwards_episode_subject(monkeypatch) -> None:
    seen: dict = {}

    def _inner(*args, **kwargs):
        del args
        seen.update(kwargs)
        return SimpleNamespace(publishable=False)

    monkeypatch.setattr("modules.reel_qa.retry.generate_and_gate", _inner)
    generate_and_gate(
        lambda prompt, **kwargs: None,
        prompt="the hammer",
        chunk_text="The wood of the handle.",
        output_stem="london_act01",
        episode_subject="The London Hammer",
    )
    assert seen["episode_subject"] == "The London Hammer"


def test_main_gate_calls_match_visual_qa_signature() -> None:
    """Every keyword main.py passes into the still gate is a real parameter."""
    allowed = set(inspect.signature(generate_and_gate).parameters)
    tree = ast.parse(Path("engine/orchestrator.py").read_text(encoding="utf-8"))
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name == "partial":
            if not node.args or not isinstance(node.args[0], ast.Name):
                continue
            if node.args[0].id != "_qwen_gate":
                continue
        elif name not in {"_qwen_gate", "_still_gate", "_slide_gate"}:
            continue
        keywords = [kw.arg for kw in node.keywords if kw.arg]
        if "chunk_text" not in keywords:
            continue
        found += 1
        unknown = [key for key in keywords if key not in allowed]
        assert unknown == []
    assert found >= 2


def test_ffmpeg_banner_probe_reads_duration_and_audio(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"not-a-real-mp4")
    monkeypatch.setattr("modules.reel_qa.mp4_gate.ffprobe_executable", lambda: None)
    monkeypatch.setattr(
        "modules.reel_qa.mp4_gate.ffmpeg_executable",
        lambda: "/opt/imageio/ffmpeg",
    )

    def _run(cmd, **kwargs):
        del kwargs
        command = list(cmd)
        if "-af" in command:
            return SimpleNamespace(
                returncode=0,
                stdout="",
                stderr="mean_volume: -18.2 dB\nmax_volume: -3.1 dB\n",
            )
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                "Duration: 00:01:27.60, start: 0.000000, bitrate: 800 kb/s\n"
                "Stream #0:0(und): Video: h264\n"
                "Stream #0:1(und): Audio: aac\n"
            ),
        )

    monkeypatch.setattr("modules.reel_qa.mp4_gate.subprocess.run", _run)
    info = probe_media(video)
    assert info["error"] == ""
    assert info["duration_s"] == 87.6
    assert info["has_audio"] is True
    assert info["mean_volume_db"] == -18.2
    decision = decide_mp4_gate(info, [{"passed": True, "text": "hammer"}])
    assert decision["passed"] is True


def test_missing_probe_is_technical_not_silent(tmp_path: Path, monkeypatch) -> None:
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"mp4")
    monkeypatch.setattr("modules.reel_qa.mp4_gate.ffprobe_executable", lambda: None)
    monkeypatch.setattr("modules.reel_qa.mp4_gate.ffmpeg_executable", lambda: None)
    info = probe_media(video)
    decision = decide_mp4_gate(
        info,
        [{"passed": False, "text": "hammer", "reason": "frame_extract_failed"}],
    )
    assert decision["passed"] is False
    assert decision["technical_failure"] is True
    assert decision["reasons"] == ["probe_unavailable:FileNotFoundError"]
    assert "audio_missing" not in decision["reasons"]
    assert "duration_below_minimum" not in decision["reasons"]
    assert "frame_subtitle_mismatch" not in decision["reasons"]


def test_subtitle_timings_keep_years_and_quantities() -> None:
    kept = filter_spoken_timings(
        [
            ("In", 0.0, 0.2),
            ("1934", 0.2, 0.6),
            ("a", 0.6, 0.7),
            ("family", 0.7, 1.0),
            ("discovered", 1.0, 1.5),
            ("dates", 2.0, 2.3),
            ("over", 2.3, 2.5),
            ("400", 2.5, 2.9),
            ("million", 2.9, 3.3),
            ("years", 3.3, 3.7),
            ("1.5s", 4.0, 4.2),
            ("break", 4.2, 4.3),
            ("[pause]", 4.3, 4.5),
        ]
    )
    words = [word for word, _start, _end in kept]
    assert words == [
        "In",
        "1934",
        "a",
        "family",
        "discovered",
        "dates",
        "over",
        "400",
        "million",
        "years",
    ]
