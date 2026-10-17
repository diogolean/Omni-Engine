# -*- coding: utf-8 -*-
"""Human review gates for the emotional-first LOFI pipeline.

When review_required=true, the pipeline writes a hold artifact and returns
before Gate 1 approval. Approved narration is rendered to TTS before atmospheric
concepts and Gate 2; images remain blocked until Gate 2 approval. Resume with
--lofi-resume-from PATH --lofi-approve-gate 1|2. Locked --lofi-script
auto-clears Gate 1 only. Gate 2 (before any image API) never auto-passes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PIPELINE_VERSION = "emotional_tts_first_v2"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_state(path: Path, state: dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["pipeline_version"] = PIPELINE_VERSION
    state["updated_at"] = _utc()
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_state(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    if p.is_dir():
        for name in (
            "pipeline_state.json",
            "gate2_hold.json",
            "gate1_hold.json",
        ):
            cand = p / name
            if cand.is_file():
                p = cand
                break
        else:
            jsons = sorted(p.glob("lofi_hold_gate*.json")) + sorted(p.glob("lofi_pipeline_*.json"))
            if not jsons:
                raise FileNotFoundError(f"no pipeline state in {path}")
            p = jsons[-1]
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"pipeline state is not an object: {p}")
    if isinstance(raw.get("script"), dict) and "gate1" not in raw:
        # Locked script JSON / stills meta — wrap.
        return {
            "pipeline_version": PIPELINE_VERSION,
            "source": str(p),
            "script": raw.get("script") if isinstance(raw.get("script"), dict) else raw,
            "gate1": {"status": "approved", "reason": "loaded_script_json"},
            "gate2": {"status": "pending"},
            "stage_completed": 1,
        }
    raw["source"] = str(p)
    return raw


def gate_status(state: dict[str, Any], gate: int) -> str:
    rec = state.get(f"gate{gate}") or {}
    return str(rec.get("status") or "pending").strip().lower()


def mark_approved(state: dict[str, Any], gate: int, *, reason: str = "cli") -> dict[str, Any]:
    state[f"gate{gate}"] = {
        "status": "approved",
        "approved_at": _utc(),
        "reason": reason,
    }
    return state


def auto_pass_gates(state: dict[str, Any]) -> dict[str, Any]:
    """Auto-pass Gate 1 only. Gate 2 always needs --lofi-approve-gate 2."""
    state["gate1"] = {
        "status": "auto_pass",
        "approved_at": _utc(),
        "reason": "review_required=false",
    }
    if str((state.get("gate2") or {}).get("status") or "") != "approved":
        state["gate2"] = {
            "status": "pending",
            "reason": "image_api_requires_explicit_approval",
        }
    return state


def hold_result_payload(
    *,
    gate: int,
    state_path: Path,
    script: dict[str, Any] | None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "hold_gate": gate,
        "state_path": str(state_path),
        "resume_cmd": (
            f"--lofi-resume-from {state_path} --lofi-approve-gate {gate}"
        ),
        "errors": list(errors or []),
        "theme": str((script or {}).get("theme") or ""),
        "scene_count": len((script or {}).get("lines") or []),
    }


def write_approval_artifact(
    path: Path,
    *,
    script: dict[str, Any],
    gate: int = 2,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Single review file: tightened beats + image prompts. Stop before Flux."""
    from agents.writer.spoken_budget import assess_lines, count_words

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [r for r in (script.get("lines") or []) if isinstance(r, dict)]
    beat_s = float(script.get("scene_duration_s") or 3.0)
    budget = assess_lines(
        [str(r.get("text") or r.get("beat_text") or "") for r in lines],
        duration_s=float(script.get("duration_requested_s") or beat_s * max(1, len(lines))),
        beat_s=beat_s,
    )
    beats: list[dict[str, Any]] = []
    for row in lines:
        text = str(row.get("text") or row.get("beat_text") or "")
        beats.append(
            {
                "scene": row.get("scene"),
                "text": text,
                "words": count_words(text),
                "duration_s": float(row.get("duration_s") or beat_s),
                "spoken_word_ceiling": row.get("spoken_word_ceiling"),
                "setting": row.get("setting") or row.get("episode_place"),
                "key_object": row.get("key_object"),
                "visual_concept": row.get("visual_concept") or row.get("scene_description"),
                "palette_key": row.get("palette_key") or row.get("palette_temp"),
                "hook_angle": row.get("hook_angle"),
                "hook_gaze": row.get("hook_gaze"),
                "arc_position": row.get("arc_position"),
            }
        )
    payload = {
        "approval_required": True,
        "gate": gate,
        "theme": script.get("theme"),
        "subtheme": script.get("subtheme"),
        "human_situation": script.get("human_situation"),
        "closing_tool": script.get("closing_tool"),
        "judge": script.get("judge") or script.get("verdict"),
        "spoken_budget": script.get("spoken_budget") or budget,
        "hook_variety": {
            "angle": (lines[0].get("hook_angle") if lines else None),
            "gaze": (lines[0].get("hook_gaze") if lines else None),
            "palette_key": (lines[0].get("palette_key") if lines else None),
        },
        "beats": beats,
        "stop": "Do not generate images or assemble until this artifact is explicitly approved.",
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path = path.with_suffix(".md")
    md_lines = [
        f"# Approval required — {script.get('theme') or 'episode'} / {script.get('subtheme') or ''}",
        "",
        "No image API / assemble until you explicitly approve this artifact.",
        "",
        f"Spoken budget ok: `{bool((script.get('spoken_budget') or budget).get('ok'))}`",
        "",
        "| # | words | dur | setting | key_object | palette | text |",
        "|---|-------|-----|---------|------------|---------|------|",
    ]
    for b in beats:
        md_lines.append(
            f"| {b.get('scene')} | {b.get('words')} | {b.get('duration_s')} | "
            f"{b.get('setting') or ''} | {b.get('key_object') or ''} | "
            f"{b.get('palette_key') or ''} | {(b.get('text') or '').replace('|', '/')} |"
        )
    if lines:
        md_lines.extend(
            [
                "",
                "## Scene 1 hook",
                f"- angle: `{lines[0].get('hook_angle')}`",
                f"- gaze: `{lines[0].get('hook_gaze')}`",
                f"- palette: `{lines[0].get('palette_key')}`",
                "",
                str(lines[0].get("visual_concept") or lines[0].get("scene_description") or ""),
            ]
        )
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return path
