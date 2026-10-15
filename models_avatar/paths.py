# -*- coding: utf-8 -*-
"""Media locations under the factory output root.

Generated images always go through :func:`utils.pipeline_paths.outputs_path`,
which honors ``OUTPUT_PATH`` (the Google Drive root in the factory ``.env``).
Layout::

    {OUTPUT_PATH}/models_avatar/<avatar>/<YYYY-MM-DD>/<preset>/[<batch>]/
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

from models_avatar.env import models_dir_override, output_media_dir
from models_avatar.errors import GenerationError


def today_stamp(moment: datetime | None = None) -> str:
    current = moment or datetime.now()
    return current.strftime("%Y-%m-%d")


def generation_dir(
    avatar_id: str,
    preset: str,
    *,
    day: str | None = None,
    batch_id: str | None = None,
) -> Path:
    """Directory for one avatar, calendar day, and preset."""
    parts = [avatar_id.strip().lower(), day or today_stamp(), preset.strip()]
    if batch_id:
        parts.append(batch_id.strip())
    return output_media_dir(*parts)


def image_path(
    avatar_id: str,
    preset: str,
    filename: str,
    *,
    day: str | None = None,
    batch_id: str | None = None,
) -> Path:
    return generation_dir(avatar_id, preset, day=day, batch_id=batch_id) / filename


def single_image_path(avatar_id: str, preset: str, *, day: str | None = None) -> Path:
    """A new ``{timestamp}_{shortid}.png`` that does not already exist.

    Single generates must not reuse ``{seed}.png``. The same seed is a
    normal rerun, and a fixed name would overwrite the previous still on
    Drive.
    """
    directory = generation_dir(avatar_id, preset, day=day)
    for _ in range(8):
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        short = uuid.uuid4().hex[:8]
        path = directory / f"{stamp}_{short}.png"
        if not path.exists() and not path.with_suffix(".json").exists():
            return path
    raise GenerationError(
        f"Could not allocate a free image name under {directory}."
    )


def atomic_write_bytes(dest: Path, payload: bytes) -> None:
    """Publish ``dest`` by writing ``dest.name.part`` and ``os.replace``.

    Google Drive sync uploads a file once the name exists. Writing the
    final name in place can sync a partial image. Replace is the publish
    step. An existing final file is left untouched.
    """
    if dest.exists():
        raise GenerationError(f"Refusing to overwrite {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    partial.write_bytes(payload)
    os.replace(partial, dest)


def comfy_models_root() -> Path:
    """Where Civitai downloads are written.

    ``MODELS_AVATAR_MODELS_DIR`` when set (point this at a pod ComfyUI
    ``models`` folder). Otherwise ``{OUTPUT_PATH}/models_avatar/_models``,
    which stays on the factory media root and out of the git tree.
    """
    override = models_dir_override()
    if override:
        return Path(override).expanduser()
    return output_media_dir("_models")
