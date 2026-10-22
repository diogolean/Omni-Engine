"""Aiwake seating, ink, and palette numbers for the parametric engine.

The engine in ``animation/parametric/`` does not know these character ids.
The ``core.animator`` shims install this plate so existing renders keep the
same framing. TODO M10: move this module to ``products/aiwake/``.
"""
from __future__ import annotations

from typing import Any


def character_eye_nudge(character_id: str, index: int) -> tuple[int, int]:
    """Micron calibration in artist-canvas pixels for one optic."""
    character = character_id.lower()
    if "gemini" in character and index == 1:
        return (2, 0)  # viewer's-right / far optic
    if "llama" in character and index == 0:
        return (-2, -2)  # viewer's-left / near optic
    return (0, 0)


def character_brow_adjust(
    character_id: str, index: int
) -> tuple[float | None, int, int]:
    """Stroke override, x nudge, and y nudge for one Aiwake brow."""
    lowered = character_id.lower()
    is_gemini = "gemini" in lowered
    is_llama = "llama" in lowered
    if is_gemini and index == 0:
        return 9.5, 0, -2  # near optic sits higher and wider
    if is_gemini and index == 1:
        return 7.5, 4, 0  # far optic, viewer's right
    if is_gemini:
        return 7.5, 0, 0
    if is_llama and index == 0:
        return None, -3, 0  # viewer's left
    return None, 0, 0


def palette_overrides(character_id: str, palette: dict) -> dict:
    if character_id.strip().lower() == "deepseek_cyborg_v3":
        return {**palette, "cavity_interior": "#151820"}
    return palette


def _shared_panorama(*, puppets_dir):
    from core.animator.asset_generator import ensure_shared_panorama

    return ensure_shared_panorama(puppets_dir=puppets_dir)


def _stamp_scene_brows(layer, rig_brows, expression, color):
    from core.animator.render.facial_rig import stamp_scene_brows

    return stamp_scene_brows(layer, rig_brows, expression, color)


def _ensure_aiwake_manifest(puppet_dir):
    from core.animator.asset_generator import ensure_puppet_manifest

    return ensure_puppet_manifest(puppet_dir)


def _generate_aiwake_assets(skin, *, missing=None):
    from core.animator.asset_generator import generate_puppet_assets

    return generate_puppet_assets(skin, missing=missing)


def apply_puppet(module: Any) -> None:
    module.character_eye_nudge = character_eye_nudge
    module.character_brow_adjust = character_brow_adjust
    module.stamp_scene_brows = _stamp_scene_brows
    module.register_asset_provider(_ensure_aiwake_manifest, _generate_aiwake_assets)


def apply_rigger(module: Any) -> None:
    module.palette_overrides = palette_overrides


def apply_compositor(module: Any) -> None:
    # ChatGPT and Claude, every view: another +5% on the present fit, and
    # 70px lower. ChatGPT is another 1% then 1% smaller and 45px lower.
    # Claude is another 150px lower. Locked DeepSeek: 9% then 2% then 1%,
    # 200px toward the viewer's left, 115px down, and a 5 degree roll.
    module.DEBATER_PRESENT_IDS = frozenset({"chatgpt_cyborg_v1", "claude_cyborg_v1"})
    module.DEBATER_EXTRA_SCALE = 1.05
    module.DEBATER_EXTRA_DROP_PX = 70
    module.DEBATER_SHIFT_X = {
        "chatgpt_cyborg_v1": 35,
        "claude_cyborg_v1": -35,
    }
    module.CLAUDE_PRESENT_IDS = frozenset({"claude_cyborg_v1"})
    module.CLAUDE_EXTRA_DROP_PX = 150
    module.CHATGPT_PRESENT_IDS = frozenset({"chatgpt_cyborg_v1"})
    module.CHATGPT_EXTRA_DROP_PX = 30 + 15
    module.CHATGPT_SCALE = 0.99 * 0.99
    module.CHATGPT_LEFT_SHIFT_X = -20
    module.DEEPSEEK_LOCKED_IDS = frozenset({"deepseek_cyborg_v3"})
    module.DEEPSEEK_EXTRA_SCALE = 1.09 * 1.02 * 1.01
    module.DEEPSEEK_SHIFT_X = -200
    module.DEEPSEEK_SHIFT_Y = 115
    module.DEEPSEEK_ROLL_CCW_DEG = 5.0
    module.PARAMETRIC_FORCE_IDS = frozenset({"deepseek_cyborg_v3"})
    module.PRODUCTION_FRAMING_IDS = frozenset({"gemini_cyborg_v2", "llama_cyborg_v2"})
    module.LEGACY_ARTIST_V2_IDS = frozenset({"gemini_cyborg_v2", "llama_cyborg_v2"})
    module._SHARED_PANORAMA_LOADER = _shared_panorama
