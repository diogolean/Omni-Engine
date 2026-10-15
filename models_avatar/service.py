# -*- coding: utf-8 -*-
"""Prepare one avatar job and, when asked, run it on a backend."""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path

from models_avatar.avatar_config import AvatarConfig, load_avatar
from models_avatar.backends import GenerationJob, GenerationResult, get_backend
from models_avatar.backends.base import GenerationBackend
from models_avatar.errors import GenerationError
from models_avatar.paths import atomic_write_bytes, single_image_path, today_stamp
from models_avatar.prompts import (
    TemplateLibrary,
    compose_negative,
    compose_positive,
    default_template_path,
    load_templates,
    render_scene,
)
from models_avatar.workflow import build_workflow, save_node_id

logger = logging.getLogger(__name__)


def prepare_job(
    avatar: AvatarConfig,
    *,
    preset: str,
    scene: str,
    seed: int,
    dest: Path,
    filename_prefix: str | None = None,
) -> GenerationJob:
    """Build the workflow for one already-composed scene."""
    spec = avatar.preset(preset)
    positive = compose_positive(avatar, scene)
    negative = compose_negative(avatar)
    prefix = filename_prefix or f"{avatar.id}_{spec.name}_{seed}"
    workflow = build_workflow(
        avatar,
        spec,
        positive=positive,
        negative=negative,
        seed=seed,
        filename_prefix=prefix,
    )
    return GenerationJob(
        avatar_id=avatar.id,
        preset=spec.name,
        seed=seed,
        positive=positive,
        negative=negative,
        workflow=workflow,
        dest=dest,
        save_node_id=save_node_id(avatar),
    )


def render(
    avatar_id: str,
    *,
    preset: str | None = None,
    prompt: str | None = None,
    set_name: str | None = None,
    template_id: str | None = None,
    seed: int | None = None,
    backend: GenerationBackend | None = None,
    backend_name: str | None = None,
    dest: Path | None = None,
    day: str | None = None,
) -> GenerationResult:
    """Generate one image. ``prompt`` is a scene; the trigger is prefixed."""
    avatar = load_avatar(avatar_id)
    spec_name = preset or avatar.default_preset
    scene = _resolve_scene(avatar, prompt=prompt, set_name=set_name, template_id=template_id)
    chosen_seed = seed if seed is not None else random.randint(1, 2**31 - 1)
    if dest is not None:
        _refuse_existing(dest)
        target = dest
    else:
        target = single_image_path(avatar.id, spec_name, day=day or today_stamp())
    job = prepare_job(
        avatar,
        preset=spec_name,
        scene=scene,
        seed=chosen_seed,
        dest=target,
    )
    engine = backend or get_backend(backend_name)
    logger.info(
        "models_avatar render | avatar=%s preset=%s seed=%s backend=%s file=%s",
        avatar.id,
        spec_name,
        chosen_seed,
        getattr(engine, "name", backend_name),
        target.name,
    )
    result = engine.generate(job)
    write_sidecar(
        result.path,
        prompt=result.prompt,
        negative=result.negative,
        seed=result.seed,
        preset=result.preset,
        workflow=job.workflow,
    )
    return result


def write_sidecar(
    image: Path,
    *,
    prompt: str,
    negative: str,
    seed: int,
    preset: str,
    workflow: dict,
) -> Path:
    """JSON record beside a single image: prompt, negative, seed, preset, workflow."""
    sidecar = image.with_suffix(".json")
    _refuse_existing(sidecar)
    payload = {
        "prompt": prompt,
        "negative": negative,
        "seed": seed,
        "preset": preset,
        "workflow": workflow,
    }
    atomic_write_bytes(
        sidecar,
        (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    return sidecar


def _refuse_existing(path: Path) -> None:
    if path.exists():
        raise GenerationError(f"Refusing to overwrite {path}")
    if path.suffix.lower() == ".png":
        sidecar = path.with_suffix(".json")
        if sidecar.exists():
            raise GenerationError(f"Refusing to overwrite {sidecar}")


def _resolve_scene(
    avatar: AvatarConfig,
    *,
    prompt: str | None,
    set_name: str | None,
    template_id: str | None,
) -> str:
    if prompt and template_id:
        raise ValueError("Pass either a prompt or a template id, not both.")
    if prompt:
        return prompt
    if not template_id:
        raise ValueError("Pass a prompt or a template id.")
    library = load_templates(default_template_path(avatar))
    template = library.get(set_name or _only_set(library, template_id), template_id)
    return render_scene(library, template)


def _only_set(library: TemplateLibrary, template_id: str) -> str:
    matches = [
        name
        for name, items in library.sets.items()
        if any(item.id == template_id for item in items)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Template {template_id!r} needs --set. Matches: {', '.join(matches) or 'none'}."
        )
    return matches[0]
