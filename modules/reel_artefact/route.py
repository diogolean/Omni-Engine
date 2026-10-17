# -*- coding: utf-8 -*-
"""Pick a still for one beat. Commons first, then a reference-conditioned edit."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from modules.reel_artefact.fetch import FetchedArtefact, fetch_artefact
from modules.reel_artefact.motion import write_motion_sidecar
from modules.reel_artefact.policy import artefact_mode
from modules.reel_artefact.source import write_source_sidecar
from modules.shot_planner.entities import Entity

logger = logging.getLogger(__name__)

_RETRY = re.compile(r"_r\d+")

_GENERIC_PROMPT = (
    "EXPECTED SUBJECT: a photoreal documentary scene. "
    "Landscape, atmosphere, a museum hall, or an archaeologist at work. "
    "No close-up of a specific named artefact. "
    "Photoreal documentary photograph from a real camera, full-bleed, "
    "no text, no watermark."
)


def is_retry_stem(stem: str) -> bool:
    return len(_RETRY.findall(stem or "")) >= 2


def generic_flux_prompt(prompt: str) -> str:
    """Flux prompt for a beat that must not invent an artefact."""
    setting = ""
    for line in (prompt or "").split("."):
        text = line.strip()
        if text.lower().startswith("setting:"):
            setting = text
            break
    parts = [_GENERIC_PROMPT]
    if setting:
        parts.append(setting + ".")
    return " ".join(parts)


def acquire_photo(
    entity: Entity,
    dest: Path,
    *,
    channel: str,
    mode: str | None = None,
    variation: bool = False,
    client=None,
    cache_dir: Path | None = None,
    generate_image=None,
) -> Path | None:
    """Write the artefact still to ``dest``.

    ``chain`` uses a Commons frame when it clears the resolution floor.
    Otherwise, and on a QA retry, it asks Nano Banana to edit the reference.
    ``commons`` never calls the generator. ``nanobanana`` always edits.
    ``flux`` returns None so the caller keeps Flux.
    """
    chosen = mode or artefact_mode()
    if chosen in {None, "flux"}:
        return None
    fetched = fetch_artefact(
        entity,
        channel=channel,
        cache_dir=cache_dir,
        client=client,
    )
    use_commons = chosen in {"commons", "chain"} and not variation
    if use_commons and fetched is not None and fetched.usable_as_still and fetched.still_path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(fetched.still_path).read_bytes())
        _stamp(dest, fetched, entity, provider="commons", model_id="")
        return dest
    if chosen == "commons":
        return None
    if chosen not in {"nanobanana", "chain"}:
        return None
    reference = fetched.reference_path if fetched is not None else None
    return _variation(
        entity,
        dest,
        reference=reference,
        channel=channel,
        cache_dir=cache_dir or _cache_parent(fetched),
        generate_image=generate_image,
        license_name=fetched.license_name if fetched else "",
        credit=fetched.credit_line if fetched else "",
        title=fetched.title if fetched else entity.name,
        source_url=fetched.source_url if fetched else "",
        author=fetched.author if fetched else "",
    )


def _cache_parent(fetched: FetchedArtefact | None) -> Path | None:
    if fetched is None:
        return None
    return fetched.reference_path.parent


def _variation(
    entity: Entity,
    dest: Path,
    *,
    reference: Path | None,
    channel: str,
    cache_dir: Path | None,
    generate_image,
    license_name: str,
    credit: str,
    title: str,
    source_url: str,
    author: str,
) -> Path | None:
    folder = cache_dir or _fallback_cache(channel, entity.id)
    folder.mkdir(parents=True, exist_ok=True)
    cached = folder / "variation.jpg"
    model_id = ""
    if not cached.is_file():
        has_reference = reference is not None and Path(reference).is_file()
        prompt = _edit_prompt(entity, has_reference=has_reference)
        try:
            if generate_image is not None:
                model_id = str(generate_image(prompt, cached, reference) or "nanobanana")
            else:
                from modules.reel_artefact.nanobanana import generate_artefact_image

                model_id = generate_artefact_image(prompt, cached, reference=reference)
        except Exception as exc:  # noqa: BLE001
            logger.info("Nano Banana variation failed (%s)", type(exc).__name__)
            return None
        if not cached.is_file():
            return None
        (folder / "variation.model").write_text(model_id, encoding="utf-8")
    else:
        note = folder / "variation.model"
        model_id = note.read_text(encoding="utf-8").strip() if note.is_file() else ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(cached.read_bytes())
    write_motion_sidecar(dest)
    write_source_sidecar(
        dest,
        {
            "provider": "nanobanana",
            "origin": "nanobanana",
            "license": license_name,
            "credit_line": credit,
            "credit_required": bool(credit),
            "reference_image": str(reference or ""),
            "canonical_shape": entity.canonical_shape,
            "title": title,
            "source_url": source_url,
            "author": author,
            "model_id": model_id,
        },
    )
    if credit:
        from agents.media.free_images.attribution import credit_line, write_sidecar
        from agents.media.free_images.models import Attribution

        line = credit or credit_line(
            title=title,
            author=author,
            license_name=license_name,
            source_url=source_url,
        )
        write_sidecar(
            dest,
            Attribution(
                title=title,
                author=author or "Unknown",
                license=license_name or "see source",
                source_url=source_url,
                provider="nanobanana",
            ),
            credit=line,
        )
    return dest


def _edit_prompt(entity: Entity, *, has_reference: bool) -> str:
    shape = entity.canonical_shape or entity.canonical_visual or entity.name
    if has_reference:
        return (
            "Edit the reference photograph into a photoreal 9:16 documentary still. "
            f"Keep the real object's shape: {shape}. "
            "New museum lighting and a slightly different camera angle. "
            "Do not add text, a watermark, or a modern vehicle."
        )
    return (
        "Photoreal documentary photograph, 9:16, of this real artefact and nothing else: "
        f"{entity.name}. Shape: {shape}. Museum case, single raking lamp, "
        "real camera, no text, no watermark."
    )


def _fallback_cache(channel: str, entity_id: str) -> Path:
    from utils.pipeline_paths import temp_path

    return temp_path(channel or "ancient_knowledge", "artefact_cache", entity_id)


def _stamp(
    dest: Path,
    fetched: FetchedArtefact,
    entity: Entity,
    *,
    provider: str,
    model_id: str,
) -> None:
    from agents.media.free_images.attribution import write_sidecar
    from agents.media.free_images.models import Attribution

    write_motion_sidecar(dest)
    write_sidecar(
        dest,
        Attribution(
            title=fetched.title,
            author=fetched.author,
            license=fetched.license_name,
            source_url=fetched.source_url,
            provider=fetched.provider,
        ),
        credit=fetched.credit_line,
    )
    write_source_sidecar(
        dest,
        {
            "provider": provider,
            "origin": fetched.provider,
            "license": fetched.license_name,
            "credit_line": fetched.credit_line,
            "credit_required": bool(fetched.credit_line),
            "reference_image": str(fetched.reference_path),
            "canonical_shape": entity.canonical_shape,
            "title": fetched.title,
            "source_url": fetched.source_url,
            "author": fetched.author,
            "model_id": model_id,
        },
    )
