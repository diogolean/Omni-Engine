"""The parametric engine lives in animation.parametric and stays product-agnostic.

Importing the compositor, puppet, or renderer pulls OpenCV. Those checks
live in test_animation_render.py so this module still collects on a CI
image that has not installed it.
"""
from __future__ import annotations

import ast
import math
import wave
from pathlib import Path

from animation.parametric.animator_types import DialogueTurn
from animation.parametric.audio_analyzer import AudioAnalyzer

import core.animator.voice as voice

_REPO = Path(__file__).resolve().parents[1]
_ANIMATION = _REPO / "animation"
_FORBIDDEN_ROOTS = {"channels_config", "products", "distribution", "production", "core"}
_CHARACTER_IDS = (
    "chatgpt_cyborg",
    "claude_cyborg",
    "gemini_cyborg",
    "llama_cyborg",
    "deepseek_cyborg",
    "aiwake_arena",
    "aiwake_render_",
)


def _silent_wav(path: Path, seconds: float, rate: int = 8000) -> None:
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))


def test_leaf_shim_sources_point_at_the_implementation() -> None:
    renderer = (_REPO / "core" / "animator" / "renderer.py").read_text(encoding="utf-8")
    assert "animation.parametric.renderer" in renderer
    assert "sys.modules[__name__]" in renderer
    puppet = (_REPO / "core" / "animator" / "puppet.py").read_text(encoding="utf-8")
    assert "animation.parametric.puppet" in puppet
    assert "apply_puppet" in puppet
    factory = (_REPO / "core" / "animator" / "factory" / "__init__.py").read_text(encoding="utf-8")
    assert "sys.modules[__name__]" not in factory
    facade = (_REPO / "core" / "animator" / "__init__.py").read_text(encoding="utf-8")
    assert "sys.modules[__name__]" not in facade
    plate = (_REPO / "core" / "animator" / "aiwake_plate.py").read_text(encoding="utf-8")
    assert "products.aiwake.animator.plate" in plate
    assert "sys.modules[__name__]" in plate
    impl = (_REPO / "products" / "aiwake" / "animator" / "plate.py").read_text(encoding="utf-8")
    assert "register_asset_provider" in impl
    assert "asset_generator" in impl


def test_aiwake_harness_stays_out_of_animation() -> None:
    assert voice.assign_debater_voices.__module__ == "products.aiwake.animator.voice.registry"
    assert not (_ANIMATION / "parametric" / "voice").exists()
    create_puppet = _REPO / "core" / "animator" / "factory" / "create_puppet.py"
    assert "products.aiwake.animator.factory.create_puppet" in create_puppet.read_text(encoding="utf-8")
    assert (_REPO / "products" / "aiwake" / "animator" / "factory" / "create_puppet.py").is_file()
    assert (_REPO / "core" / "animator" / "pipeline.py").is_file()
    assert (_REPO / "core" / "animator" / "factory" / "one_shot_factory.py").is_file()
    assert (_REPO / "core" / "animator" / "asset_generator.py").is_file()
    registry = (_REPO / "core" / "animator" / "voice" / "registry.py").read_text(encoding="utf-8")
    assert "products.aiwake.animator.voice.registry" in registry
    puppet_source = (_ANIMATION / "parametric" / "puppet.py").read_text(encoding="utf-8")
    assert "asset_generator" not in puppet_source
    assert "register_asset_provider" in puppet_source


def test_animation_does_not_import_products_or_channels() -> None:
    offenders: list[str] = []
    for path in _ANIMATION.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                root = name.split(".", 1)[0]
                if root in _FORBIDDEN_ROOTS:
                    offenders.append(f"{path.relative_to(_REPO)} imports {name}")
    assert offenders == []


def test_animation_source_has_no_aiwake_character_ids() -> None:
    hits: list[str] = []
    for path in _ANIMATION.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in _CHARACTER_IDS:
            if token in text:
                hits.append(f"{path.relative_to(_REPO)} contains {token}")
    assert hits == []


def test_renderer_stays_an_ffmpeg_pipe() -> None:
    source = (_ANIMATION / "parametric" / "renderer.py").read_text(encoding="utf-8")
    assert "from shared.ffmpeg import resolve_ffmpeg" in source
    assert "libx264" in source
    assert "import moviepy" not in source.lower()
    assert "from moviepy" not in source.lower()
    assert "animation_render_" in source
    assert "aiwake_render_" not in source
    assert "_ffmpeg_failure_text" in source
    audio_source = (_ANIMATION / "parametric" / "audio_analyzer.py").read_text(encoding="utf-8")
    assert "from moviepy import AudioFileClip" in audio_source


def test_duration_override_sets_the_analyzer_timeline(tmp_path: Path) -> None:
    wav = tmp_path / "tone.wav"
    _silent_wav(wav, 2.0)
    turns = [DialogueTurn(speaker="host", start_time=0.0, end_time=8.0, text="hello")]
    shortened = AudioAnalyzer(fps=10).analyze(
        wav, turns, duration_override=0.4, use_rhubarb=False
    )
    assert shortened.duration_s == 0.4
    assert shortened.n_frames == math.ceil(0.4 * 10)
    full = AudioAnalyzer(fps=10).analyze(wav, turns, use_rhubarb=False)
    assert full.n_frames == math.ceil(2.0 * 10)
    assert full.duration_s == 2.0
