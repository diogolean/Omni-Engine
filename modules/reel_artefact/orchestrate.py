# -*- coding: utf-8 -*-
"""The reel still entry point main.py uses once a shot list exists.

Tests call these two functions. A unit test that climbs one beat on the
calling thread does not exercise this path.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Callable


def make_climb_job(
    *,
    beat_index: int,
    narration: str,
    prompt: str,
    output_directory: Path | str,
    stem: str,
    channel: str,
    flux_generate: Callable[..., Any],
    shot: Any = None,
    entity_id: str = "",
    checkpoint: Any = None,
    generate_kwargs: dict | None = None,
    episode_subject: str = "",
    canonical_shape: str = "",
    banned_subjects: list[str] | None = None,
    is_cta: bool = False,
    n_beats: int = 0,
) -> Callable[[], Any]:
    """One beat, with the planned role and scene the ladder has to honour."""
    from modules.reel_artefact.climb import climb_act

    image_role = ""
    scene_type = ""
    shot_type = ""
    camera = ""
    setting = ""
    shot_subject = ""
    shot_era = ""
    shape = canonical_shape
    eid = entity_id
    if shot is not None and not getattr(shot, "kb_miss", False):
        eid = eid or str(getattr(shot, "entity_id", "") or "")
        shape = shape or str(getattr(shot, "canonical_shape", "") or "")
        image_role = str(getattr(shot, "image_role", "") or "")
        scene_type = str(getattr(shot, "scene_type", "") or "")
        shot_type = str(getattr(shot, "shot_type", "") or "")
        camera = str(getattr(shot, "camera", "") or "")
        setting = str(getattr(shot, "setting", "") or "")
        shot_subject = str(getattr(shot, "subject", "") or "")
        shot_era = str(getattr(shot, "era", "") or "")
    return functools.partial(
        climb_act,
        beat_index=beat_index,
        narration=narration,
        prompt=prompt,
        output_directory=output_directory,
        stem=stem,
        channel=channel,
        flux_generate=flux_generate,
        entity_id=eid,
        checkpoint=checkpoint,
        generate_kwargs=dict(generate_kwargs or {}),
        episode_subject=episode_subject,
        canonical_shape=shape,
        banned_subjects=list(banned_subjects or []) or None,
        is_cta=is_cta,
        n_beats=n_beats,
        shot_type=shot_type,
        camera=camera,
        setting=setting,
        shot_subject=shot_subject,
        shot_era=shot_era,
        image_role=image_role,
        scene_type=scene_type,
        topic=episode_subject,
    )


def run_reel_stills(jobs: dict[int, Callable[[], Any]]) -> dict[int, Any]:
    """First attempt for every beat, then round-robin retries. The real pool."""
    from modules.reel_artefact.fair import run_fair

    return run_fair(jobs)
