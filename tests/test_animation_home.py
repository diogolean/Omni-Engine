"""The parametric engine lives in animation.parametric and stays product-agnostic."""
from __future__ import annotations

import ast
from pathlib import Path

import animation.parametric.audio_analyzer as audio
import animation.parametric.compositor as compositor_impl
import animation.parametric.puppet as puppet_impl
import animation.parametric.renderer as renderer_impl
import core.animator.audio_analyzer as audio_shim
import core.animator.compositor as compositor_shim
import core.animator.factory.create_puppet as create_puppet
import core.animator.factory.rigger as rigger_shim
import core.animator.puppet as puppet_shim
import core.animator.renderer as renderer_shim
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


def test_leaf_shims_are_the_implementation() -> None:
    assert renderer_shim is renderer_impl
    assert puppet_shim is puppet_impl
    assert compositor_shim is compositor_impl
    assert audio_shim is audio
    assert renderer_shim.AnimationRenderer is renderer_impl.AnimationRenderer
    assert puppet_shim.PuppetRig is puppet_impl.PuppetRig


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


def test_aiwake_harness_stays_out_of_animation() -> None:
    assert voice.assign_debater_voices.__module__ == "core.animator.voice.registry"
    assert not (_ANIMATION / "parametric" / "voice").exists()
    assert create_puppet.__name__ == "core.animator.factory.create_puppet"
    assert (_REPO / "core" / "animator" / "pipeline.py").is_file()
    assert (_REPO / "core" / "animator" / "factory" / "one_shot_factory.py").is_file()
    assert "TODO M10" in (_REPO / "core" / "animator" / "voice" / "registry.py").read_text(
        encoding="utf-8"
    )


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
    audio_source = (_ANIMATION / "parametric" / "audio_analyzer.py").read_text(encoding="utf-8")
    assert "from moviepy import AudioFileClip" in audio_source
