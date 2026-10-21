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


def atmosphere_style(channel_id: str) -> str:
    """Channel ``ATMOSPHERE_STYLE`` when the page module loads, else a plain brief."""
    if channel_id:
        try:
            import importlib  # noqa: PLC0415

            page = importlib.import_module(f"channels_config.{channel_id}.page_config")
            style = str(getattr(page, "ATMOSPHERE_STYLE", "") or "").strip()
            if style:
                return style
        except Exception as exc:  # noqa: BLE001
            log.info("Atmosphere style unavailable for %s (%s).", channel_id, type(exc).__name__)
    return (
        "Ultra-realistic cinematic photograph of the named place. "
        "No text, no letters, no title card, no watermark."
    )


def flux_prompt(topic: str, channel_id: str) -> str:
    style = atmosphere_style(channel_id)
    subject = (topic or "the subject").strip()
    return (
        f"{style}\n\n"
        f"Subject: {subject}. "
        "Portrait 2:3 photograph, the subject filling the frame. "
        "A real photograph of the place, not a poster, not a title card, "
        "no words, no letters, no captions, no logos."
    )


def generate_flux_photo(
    *,
    topic: str,
    channel_id: str,
    dest_dir: Path,
    stem: str,
) -> Path | None:
    """Render one FLUX.1-schnell image into ``dest_dir``. Returns the file or None."""
    from production.providers.image.image_provider import get_image_adapter  # noqa: PLC0415

    dest_dir.mkdir(parents=True, exist_ok=True)
    adapter = get_image_adapter(model_id=FLUX_SCHNELL)
    path = adapter.generate(
        flux_prompt(topic, channel_id),
        output_stem=stem,
        output_directory=dest_dir,
        aspect_ratio="2:3",
        avatar_mode="OFF",
        width=FLUX_PIN_WIDTH,
        height=FLUX_PIN_HEIGHT,
        model_name=FLUX_SCHNELL,
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
    stem = str(record.get("subject_slug") or record.get("post_id") or "pin")
    library_images = outputs_dir / "library" / "images"
    try:
        path = generate_flux_photo(
            topic=topic,
            channel_id=channel_id,
            dest_dir=library_images,
            stem=stem,
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
        from agents.media.b2_client import upload_object  # noqa: PLC0415

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
