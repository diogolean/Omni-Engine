# -*- coding: utf-8 -*-
"""Generate a FLUX.1-schnell photograph when a library record has no real photo."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

FLUX_SCHNELL = "black-forest-labs/FLUX.1-schnell"
# Portrait 2:3, multiples of 16, so the photo fills the 1000x1500 card.
FLUX_PIN_WIDTH = 832
FLUX_PIN_HEIGHT = 1248


def flux_prompt(
    topic: str,
    channel_id: str,
    *,
    title: str = "",
    article: str = "",
    complete=None,
    allow_model: bool = True,
) -> str:
    """Concrete documentary scene. Does not read ``ATMOSPHERE_STYLE``."""
    from production.formats.pins.scene_prompt import flux_prompt as build_scene  # noqa: PLC0415

    return build_scene(
        topic,
        channel_id,
        title=title,
        article=article,
        complete=complete,
        allow_model=allow_model,
    )


def generate_flux_photo(
    *,
    topic: str,
    channel_id: str,
    dest_dir: Path,
    stem: str,
    title: str = "",
    article: str = "",
    complete=None,
    allow_model: bool = True,
) -> Path | None:
    """Render one FLUX.1-schnell image into ``dest_dir``. Returns the file or None."""
    from production.formats.pins.scene_prompt import PIN_NEGATIVE_PROMPT  # noqa: PLC0415
    from production.providers.image.image_provider import get_image_adapter  # noqa: PLC0415

    dest_dir.mkdir(parents=True, exist_ok=True)
    adapter = get_image_adapter(model_id=FLUX_SCHNELL)
    path = adapter.generate(
        flux_prompt(
            topic,
            channel_id,
            title=title,
            article=article,
            complete=complete,
            allow_model=allow_model,
        ),
        output_stem=stem,
        output_directory=dest_dir,
        aspect_ratio="2:3",
        avatar_mode="OFF",
        width=FLUX_PIN_WIDTH,
        height=FLUX_PIN_HEIGHT,
        model_name=FLUX_SCHNELL,
        visual_role="documentary",
        negative_prompt=PIN_NEGATIVE_PROMPT,
    )
    if path is None:
        return None
    file_path = Path(path)
    return file_path if file_path.is_file() else None


def ensure_flux_photo(
    record: dict,
    *,
    outputs_dir: Path,
    channel_id: str,
    prefix: str,
    client=None,
    bucket: str | None = None,
) -> Path | None:
    """Generate a photo, keep it in the library, and upload that file to B2."""
    topic = str(record.get("topic") or record.get("pinterest_title") or "untitled")
    title = str(record.get("pinterest_title") or topic)
    article = str(
        record.get("original_caption")
        or record.get("humanized_caption")
        or record.get("article_excerpt")
        or ""
    )
    stem = str(record.get("subject_slug") or record.get("post_id") or "pin")
    library_images = outputs_dir / "library" / "images"
    try:
        path = generate_flux_photo(
            topic=topic,
            channel_id=channel_id,
            dest_dir=library_images,
            stem=stem,
            title=title,
            article=article,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("FLUX photo failed for %s (%s).", stem, type(exc).__name__)
        return None
    if path is None:
        return None
    relative = f"images/{path.name}"
    record["local_image_path"] = str(path)
    record["image_relative"] = relative
    record["generated_by"] = "flux.1-schnell"
    try:
        from shared.storage.b2_client import upload_object  # noqa: PLC0415

        upload_object(
            prefix + relative,
            path,
            client=client,
            bucket=bucket,
            content_type="image/jpeg",
        )
        record["b2_image_key"] = prefix + relative
    except Exception as exc:  # noqa: BLE001
        log.info("FLUX image stayed local; B2 upload failed (%s).", type(exc).__name__)
    return path
