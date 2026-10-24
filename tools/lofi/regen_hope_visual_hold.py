# -*- coding: utf-8 -*-
"""Regen hope HOLD stills for visual-gate review. Does not touch VO.

Captions stay the locked 9-liner. Window is a narrative anchor, not a
shot-list: at most two pane-primary stills (beats 3 and 9). Other beats
use different rooms / street / lamp / table compositions. No unearned rain.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.pipeline_paths import page_outputs_dir

os.environ["LOFI_FLUX_BACKEND"] = "dev"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True, encoding="utf-8-sig")

from core.economic_reel_lofi import config as lofi_cfg  # noqa: E402
from core.economic_reel_lofi.image_gen import generate_scene_image_dev  # noqa: E402
from core.economic_reel_lofi.pipeline import generate_and_qa_scene  # noqa: E402
from core.economic_reel_lofi.visual_identity import (  # noqa: E402
    apply_hope_composition_spec,
    apply_lighting_to_beat,
    assemble_v2_prompt_dev,
    hope_beat_spec,
)

HOLD = page_outputs_dir("wonder_feed") / "clips" / "lofi_hold_hope_20260824_194933_v01.json"
PROFILE = "style-riso_painting_retro_vintage"
STYLE_LOCK = (
    "Same risograph gouache print as every other beat in this episode: "
    "paper tooth, hard indigo and charcoal color blocks. Dry air, dry pavement."
)
NO_PANE = "Blank wall fills the frame from edge to edge."
PANE_OK = (
    "One dry window lit from inside, seen from the street. Empty sill. "
    "Clear glass. Not a portrait."
)
ISOLATE = "One subject. Blank wall or open sky. The picture contains nothing else."

# Nouns licensed by the spoken line (plus lamp only when the line is about light).
BEAT_FIX: dict[int, dict] = {
    1: {
        "subject_type": "woman",
        "composition_type": "wide_environment",
        "setting": "open empty road under a dark sky",
        "key_object": "empty road",
        "shot_scale": "wide",
        "window_primary": False,
        "force_print_silhouette": True,
        "lighting_condition": "blue_hour_streetlight",
        "pose_hint": (
            "Distant small clothed silhouette on a wide empty road. "
            "Pavement and sky dominate the frame. Hands unreadably small."
        ),
    },
    2: {
        "subject_type": "woman",
        "composition_type": "wide_environment",
        "setting": "open empty road under a dark sky",
        "key_object": "empty road",
        "shot_scale": "medium",
        "window_primary": False,
        "lighting_condition": "blue_hour_streetlight",
        "anchor_beat": "",
        "pose_hint": (
            "Small figure walking on a wide empty road under the sky. "
            "Pavement and sky only."
        ),
    },
    3: {
        "subject_type": "object_focus",
        "composition_type": "object_focus",
        "setting": "building facade on a quiet street at night",
        "key_object": "one window still lit",
        "shot_scale": "medium",
        "window_primary": True,
        "lighting_condition": "blue_hour_streetlight",
        "anchor_beat": "introduce",
        "episode_anchor_name": "one window still lit",
        "visual_anchor_hint": "one window still lit, first seen",
    },
    4: {
        "subject_type": "object_focus",
        "composition_type": "object_focus",
        "setting": "empty closed room, blank wall",
        "key_object": "one lamp almost dark",
        "shot_scale": "medium",
        "window_primary": False,
        "lighting_condition": "indoor_lamp_glow",
        "anchor_beat": "",
        "pose_hint": (
            "One lampshade filling the frame against a blank wall. "
            "The lamp is the only object."
        ),
    },
    5: {
        "subject_type": "woman",
        "composition_type": "wide_environment",
        "setting": "open empty road under a dark sky",
        "key_object": "empty road",
        "shot_scale": "medium",
        "window_primary": False,
        "lighting_condition": "blue_hour_streetlight",
        "anchor_beat": "",
        "pose_hint": (
            "Small figure walking on a wide empty road under the sky. "
            "Pavement and sky only."
        ),
    },
    6: {
        "subject_type": "object_focus",
        "composition_type": "object_focus",
        "setting": "empty closed room, blank wall",
        "key_object": "one table lamp still on",
        "shot_scale": "medium",
        "window_primary": False,
        "lighting_condition": "indoor_lamp_glow",
        "anchor_beat": "",
        "pose_hint": "One lampshade filling the frame against a blank wall.",
    },
    7: {
        "subject_type": "object_focus",
        "composition_type": "object_focus",
        "setting": "blank plaster wall and ceiling",
        "key_object": "lamp glow spilling up a blank wall",
        "shot_scale": "extreme-close",
        "window_primary": False,
        "lighting_condition": "indoor_lamp_glow",
        "anchor_beat": "",
        "pose_hint": (
            "Looking up at blank plaster. A triangle of warm lamp-light "
            "printed on the wall and ceiling. The lamp body is out of frame. "
            "Only the glow. Empty plaster around it."
        ),
    },
    8: {
        "subject_type": "object_focus",
        "composition_type": "wide_environment",
        "setting": "empty street at night",
        "key_object": "the street",
        "shot_scale": "medium",
        "window_primary": False,
        "lighting_condition": "blue_hour_streetlight",
        "anchor_beat": "",
        "pose_hint": "Wide dry street.",
    },
    9: {
        "subject_type": "object_focus",
        "composition_type": "wide_environment",
        "setting": "quiet street looking at a building facade",
        "key_object": "one window still lit",
        "shot_scale": "medium",
        "window_primary": True,
        "lighting_condition": "blue_hour_streetlight",
        "anchor_beat": "callback",
        "episode_anchor_name": "one window still lit",
        "visual_anchor_hint": "one window still lit, stayed",
    },
}

CLEAR_CLOSE = (
    "close_variant",
    "close_character",
    "close_target",
    "eye_close_context",
    "pose_hint",
    "visual_fallback",
    "setting_archetype_remap",
    "composition_remap",
    "anchor_beat",
    "episode_anchor_name",
    "visual_anchor_hint",
)


FIGURE_STAGING = (
    "One figure as a flat printed clothed silhouette, same risograph gouache "
    "as the rest of this episode: paper tooth, hard indigo and charcoal color "
    "blocks. Side profile toward off-frame light. Hands out of frame or hidden "
    "in sleeves. No visible fingers. The wall is background only. Not facing the wall."
)


PATH_STAGING = (
    "Small figure on a wide empty path under the sky. Pavement and sky only. "
    "No extra objects."
)


def _extra_for(scene: int) -> str:
    if scene in {1, 2, 5}:
        return f"{STYLE_LOCK} {PATH_STAGING}"
    pane = PANE_OK if BEAT_FIX[scene].get("window_primary") else NO_PANE
    return f"{STYLE_LOCK} {pane} {ISOLATE}"


def _patch_row(row: dict, scene: int) -> dict:
    fix = BEAT_FIX[scene]
    for key in CLEAR_CLOSE:
        row[key] = ""
    row.update(fix)
    row["scene"] = scene
    row["visual_identity_profile"] = PROFILE
    apply_lighting_to_beat(row, str(fix.get("lighting_condition") or "blue_hour_streetlight"))
    apply_hope_composition_spec(row, scene)
    spec = hope_beat_spec(scene)
    if spec.get("treatment"):
        row["pose_hint"] = spec["treatment"]
        if str(row.get("pose_hint") or "") and fix.get("pose_hint"):
            row["pose_hint"] = f"{spec['treatment']}. {fix['pose_hint']}"
    row["visual_prompt"] = assemble_v2_prompt_dev(row, profile=PROFILE)
    return row


def main() -> int:
    hold = json.loads(HOLD.read_text(encoding="utf-8"))
    run_dir = Path(hold["work_dir"])
    script = hold.get("script") if isinstance(hold.get("script"), dict) else {}
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    if len(lines) != 9:
        print(f"expected 9 lines, got {len(lines)}")
        return 1
    if not lofi_cfg.uses_flux_dev():
        print("LOFI_FLUX_BACKEND is not dev — refuse schnell for this review")
        return 2

    only = {int(x) for x in sys.argv[1:] if str(x).isdigit()}
    bak = run_dir / "_pre_window_cap"
    bak.mkdir(parents=True, exist_ok=True)
    for i in range(1, 10):
        src = run_dir / f"scene_{i:02d}.png"
        dest = bak / src.name
        if src.is_file() and not dest.is_file():
            shutil.copy2(src, dest)
            print(f"[visual] backed up {src.name} -> {bak.name}")

    images: list[str] = list(hold.get("scene_images") or [])
    while len(images) < 9:
        images.append("")
    gates: list[dict] = list(hold.get("object_gate_by_scene") or [])
    while len(gates) < 9:
        gates.append({})
    for row in lines:
        scene = int(row.get("scene") or 0)
        if only and scene not in only:
            continue
        caption = str(row.get("text") or "")
        _patch_row(row, scene)
        out_img = run_dir / f"scene_{scene:02d}.png"
        print(
            f"[visual] scene {scene} {caption!r} "
            f"st={row.get('subject_type')} obj={row.get('key_object')!r} "
            f"window_primary={int(bool(row.get('window_primary')))} "
            f"light={row.get('lighting_condition')}"
        )
        def _assemble(beat, focus_step=0):
            return assemble_v2_prompt_dev(
                beat, profile=PROFILE, focus_step=focus_step
            )

        ok, gate, n_img, n_crit = generate_and_qa_scene(
            row,
            out_img,
            attempt_budget=4 if scene == 1 else 3,
            extra_prompt=_extra_for(scene),
            generate_fn=generate_scene_image_dev,
            assemble_fn=_assemble,
        )
        gate = dict(gate)
        gate["image_calls"] = n_img
        gate["critic_calls"] = n_crit
        gate["visual_hold_ok"] = bool(ok)
        gate["window_primary"] = bool(row.get("window_primary"))
        gates[scene - 1] = gate
        images[scene - 1] = str(out_img)
        print(
            f"[visual] scene {scene} ok={ok} calls={n_img} "
            f"flaws={gate.get('qa_flaws') or gate.get('flaws')}"
        )

    script["lines"] = lines
    script["close_variant"] = ""
    hold["script"] = script
    hold["scene_images"] = images
    hold["object_gate_by_scene"] = gates
    hold["visual_identity_profile"] = PROFILE
    hold["flux_backend"] = "dev"
    hold["visual_hold_pass"] = "20260824_window_cap_variation"
    HOLD.write_text(json.dumps(hold, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("[visual] updated", HOLD)
    print("[visual] stills in", run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
