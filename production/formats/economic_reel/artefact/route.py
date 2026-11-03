# -*- coding: utf-8 -*-
"""Pick a still for one beat. A real photo first, then a cheap edit."""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from PIL import Image

from production.formats.economic_reel.artefact.budget import (
    claim_still,
    file_hash_key,
    note_real_use,
    real_allowed,
    release_still,
    take_nano_slot,
)
from production.formats.economic_reel.artefact.crop import anchor_for, prepare_reel_frame
from production.formats.economic_reel.artefact.fetch import FetchedArtefact, fetch_gallery
from production.formats.economic_reel.artefact.logbook import note_source
from production.formats.economic_reel.artefact.motion import profile_for, write_motion_sidecar
from production.formats.economic_reel.artefact.policy import artefact_mode
from production.formats.economic_reel.artefact.source import write_source_sidecar
from production.formats.economic_reel.shot_planner.entities import Entity

logger = logging.getLogger(__name__)

_RETRY = re.compile(r"_r\d+")
_VARIATION_GUARD = threading.Lock()
_VARIATION_LOCKS: dict[str, threading.Lock] = {}

_GENERIC_PROMPT = (
    "EXPECTED SUBJECT: a photoreal documentary scene. "
    "35mm film grain, natural colour, real location. "
    "Simple composition: a landscape, a museum hall, or people at a real site. "
    "No close-up of a specific named artefact. "
    "Photoreal documentary photograph from a real camera, full-bleed, "
    "no text, no watermark."
)


def is_retry_stem(stem: str) -> bool:
    return len(_RETRY.findall(stem or "")) >= 2


def _setting_clause(prompt: str) -> str:
    """The Setting sentence, keeping abbreviations and parentheticals intact.

    Splitting the whole prompt on ``.`` turned ``(e.g. a plateau)`` into ``(e``.
    """
    match = re.search(r"(?i)\bsetting\s*:\s*", prompt or "")
    if not match:
        return ""
    rest = prompt[match.end():]
    depth = 0
    out: list[str] = []
    index = 0
    while index < len(rest):
        char = rest[index]
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == "." and depth == 0:
            prev = rest[index - 1] if index else ""
            nxt = rest[index + 1] if index + 1 < len(rest) else ""
            if not (prev.isalpha() and nxt.isalpha()):
                break
        out.append(char)
        index += 1
    clause = "".join(out).strip(" ,")
    if not clause:
        return ""
    return "Setting: " + clause + "."


def generic_flux_prompt(prompt: str) -> str:
    """Flux prompt for a beat that must not invent an artefact."""
    parts = [_GENERIC_PROMPT]
    setting = _setting_clause(prompt)
    if setting:
        parts.append(setting)
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
    beat_index: int = 0,
    allow_nano: bool = True,
    text_only: bool = False,
    skip_real: bool = False,
    n_beats: int = 0,
) -> Path | None:
    """Write the artefact still to ``dest``.

    ``chain`` publishes a real photograph when one is large enough. Nano
    Banana runs only when no usable still exists, and only inside the reel
    cap: a reference edit, or text-only when the entity marks that safe and
    no reference exists. ``commons`` never calls the generator. ``flux``
    returns None. ``nanobanana`` is the inspection path and still edits.
    """
    chosen = mode or artefact_mode()
    if chosen in {None, "flux"}:
        return None
    slot = int(beat_index) + (1 if variation else 0)
    gallery = fetch_gallery(
        entity,
        channel=channel,
        cache_dir=cache_dir,
        client=client,
    )
    stills = [
        photo for photo in gallery
        if photo.usable_as_still and photo.reference_path.is_file()
    ]
    reference_photo = next((photo for photo in gallery if photo.reference_path.is_file()), None)
    folder = cache_dir or _cache_parent(reference_photo) or _fallback_cache(channel, entity.id)

    if text_only:
        if not allow_nano or not bool(getattr(entity, "text_only_safe", False)):
            return None
        return _variation(
            entity,
            dest,
            reference=None,
            channel=channel,
            cache_dir=folder,
            generate_image=generate_image,
            license_name="",
            credit="",
            title=entity.name,
            source_url="",
            author="",
            kind="nb-text",
            beat_index=beat_index,
            slot=slot,
            n_beats=n_beats,
        )

    if chosen == "commons":
        fresh = [photo for photo in stills if real_allowed(_photo_key(photo))]
        if not fresh:
            return None
        return _publish_real(fresh[slot % len(fresh)], dest, entity, slot, beat_index)

    if chosen == "nanobanana":
        reference = reference_photo.reference_path if reference_photo is not None else None
        if reference is not None and Path(reference).is_file():
            return _variation(
                entity,
                dest,
                reference=reference,
                channel=channel,
                cache_dir=folder,
                generate_image=generate_image,
                license_name=reference_photo.license_name if reference_photo else "",
                credit=reference_photo.credit_line if reference_photo else "",
                title=reference_photo.title if reference_photo else entity.name,
                source_url=reference_photo.source_url if reference_photo else "",
                author=reference_photo.author if reference_photo else "",
                kind="nb-ref",
                beat_index=beat_index,
                slot=slot,
                n_beats=n_beats,
            )
        if allow_nano and bool(getattr(entity, "text_only_safe", False)):
            return _variation(
                entity,
                dest,
                reference=None,
                channel=channel,
                cache_dir=folder,
                generate_image=generate_image,
                license_name="",
                credit="",
                title=entity.name,
                source_url="",
                author="",
                kind="nb-text",
                beat_index=beat_index,
                slot=slot,
                n_beats=n_beats,
            )
        return None

    if chosen != "chain":
        return None
    if stills and not skip_real:
        fresh = [photo for photo in stills if real_allowed(_photo_key(photo))]
        if fresh:
            return _publish_real(fresh[slot % len(fresh)], dest, entity, slot, beat_index)
    if allow_nano and reference_photo is not None:
        edited = _variation(
            entity,
            dest,
            reference=reference_photo.reference_path,
            channel=channel,
            cache_dir=folder,
            generate_image=generate_image,
            license_name=reference_photo.license_name,
            credit=reference_photo.credit_line,
            title=reference_photo.title,
            source_url=reference_photo.source_url,
            author=reference_photo.author,
            kind="nb-ref",
            beat_index=beat_index,
            slot=slot,
            n_beats=n_beats,
        )
        if edited is not None:
            return edited
    if (
        allow_nano
        and reference_photo is None
        and bool(getattr(entity, "text_only_safe", False))
    ):
        return _variation(
            entity,
            dest,
            reference=None,
            channel=channel,
            cache_dir=folder,
            generate_image=generate_image,
            license_name="",
            credit="",
            title=entity.name,
            source_url="",
            author="",
            kind="nb-text",
            beat_index=beat_index,
            slot=slot,
            n_beats=n_beats,
        )
    return None


def cached_variation(entity: Entity, *, channel: str, cache_dir: Path | None = None) -> Path | None:
    """A Nano Banana file already paid for, if this entity has one."""
    folder = cache_dir or _fallback_cache(channel, entity.id)
    for name in ("variation_ref.jpg", "variation_text.jpg", "variation.jpg"):
        path = folder / name
        if path.is_file():
            return path
    return None


def _photo_key(fetched: FetchedArtefact) -> str:
    path = fetched.reference_path
    try:
        return str(path.resolve()) if path else ""
    except OSError:
        return str(path or "")


def _publish_real(
    fetched: FetchedArtefact,
    dest: Path,
    entity: Entity,
    slot: int,
    beat_index: int,
) -> Path | None:
    if not fetched.reference_path.is_file():
        return None
    try:
        with Image.open(fetched.reference_path) as opened:
            frame, _how = prepare_reel_frame(opened.convert("RGB"), anchor=anchor_for(slot))
    except (OSError, ValueError):
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.save(dest, format="JPEG", quality=92)
    write_motion_sidecar(dest, profile=profile_for(slot))
    _stamp(dest, fetched, entity, provider=fetched.provider or "commons", model_id="")
    note_real_use(_photo_key(fetched))
    note_source(f"real:{fetched.provider or 'commons'}", entity.id, int(beat_index) + 1)
    return dest


def _cache_parent(fetched: FetchedArtefact | None) -> Path | None:
    if fetched is None:
        return None
    return fetched.reference_path.parent


def _variation_lock(folder: Path) -> threading.Lock:
    key = str(folder)
    with _VARIATION_GUARD:
        lock = _VARIATION_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _VARIATION_LOCKS[key] = lock
        return lock


_CAPPED = object()
_ANGLES = (
    "a tighter crop on the display case",
    "a three-quarter view from the left",
    "a low angle along the plinth",
    "a wider view with the case small in the hall",
)


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
    kind: str = "nb-ref",
    beat_index: int = 0,
    slot: int = 0,
    n_beats: int = 0,
) -> Path | None:
    folder = cache_dir or _fallback_cache(channel, entity.id)
    folder.mkdir(parents=True, exist_ok=True)
    cached = folder / ("variation_text.jpg" if kind == "nb-text" else "variation_ref.jpg")
    with _variation_lock(folder):
        if cached.is_file():
            placed = _place_cached(
                cached,
                dest,
                entity,
                reference=reference,
                license_name=license_name,
                credit=credit,
                title=title,
                source_url=source_url,
                author=author,
                beat_index=beat_index,
                slot=slot,
                n_beats=n_beats,
            )
            if placed is not _CAPPED:
                return placed if isinstance(placed, Path) else None
        return _render_edit(
            entity,
            dest,
            reference=reference,
            folder=folder,
            canonical=cached,
            generate_image=generate_image,
            license_name=license_name,
            credit=credit,
            title=title,
            source_url=source_url,
            author=author,
            kind=kind,
            beat_index=beat_index,
            slot=slot,
            n_beats=n_beats,
        )


def _place_cached(
    cached: Path,
    dest: Path,
    entity: Entity,
    *,
    reference: Path | None,
    license_name: str,
    credit: str,
    title: str,
    source_url: str,
    author: str,
    beat_index: int,
    slot: int,
    n_beats: int,
):
    """Copy a previous edit. ``_CAPPED`` when this hash is already at the limit."""
    try:
        key = file_hash_key(str(cached))
    except OSError:
        return None
    try:
        target = str(dest.resolve())
    except OSError:
        target = str(dest)
    ok, reason = claim_still(target, key, n_beats)
    if not ok:
        logger.info(
            "cached edit capped | entity=%s | beat=%s | %s",
            entity.id,
            int(beat_index) + 1,
            reason,
        )
        return _CAPPED
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(cached.read_bytes())
    except OSError:
        release_still(target, key)
        return None
    write_motion_sidecar(dest, profile=profile_for(slot))
    note_source("reuse:cache", entity.id, int(beat_index) + 1)
    _write_edit_sidecar(
        dest,
        entity,
        reference=reference,
        license_name=license_name,
        credit=credit,
        title=title,
        source_url=source_url,
        author=author,
        provider="reuse",
        model_id="",
        image_hash=key,
    )
    return dest


def _render_edit(
    entity: Entity,
    dest: Path,
    *,
    reference: Path | None,
    folder: Path,
    canonical: Path,
    generate_image,
    license_name: str,
    credit: str,
    title: str,
    source_url: str,
    author: str,
    kind: str,
    beat_index: int,
    slot: int,
    n_beats: int,
) -> Path | None:
    """A new Nano Banana edit. A capped cache asks for a different angle."""
    if not take_nano_slot():
        logger.info(
            "Nano Banana cap reached | entity=%s | beat=%s",
            entity.id,
            int(beat_index) + 1,
        )
        return None
    has_reference = reference is not None and Path(reference).is_file()
    prompt = _edit_prompt(entity, has_reference=has_reference)
    target = canonical
    if canonical.is_file():
        angle = _ANGLES[int(beat_index) % len(_ANGLES)]
        prompt = (
            f"{prompt} Different framing from the cached still: {angle}. "
            "Not the same crop."
        )
        target = folder / f"variation_{kind}_{int(beat_index):02d}_{int(slot):02d}.jpg"
    try:
        if generate_image is not None:
            model_id = str(generate_image(prompt, target, reference) or "nanobanana")
        else:
            from production.formats.economic_reel.artefact.nanobanana import generate_artefact_image

            model_id = generate_artefact_image(prompt, target, reference=reference)
    except Exception as exc:  # noqa: BLE001
        logger.info("Nano Banana variation failed (%s)", type(exc).__name__)
        return None
    if not target.is_file():
        return None
    (folder / f"{target.name}.model").write_text(model_id, encoding="utf-8")
    try:
        key = file_hash_key(str(target))
    except OSError:
        return None
    try:
        reserved = str(dest.resolve())
    except OSError:
        reserved = str(dest)
    ok, reason = claim_still(reserved, key, n_beats)
    if not ok:
        logger.info(
            "new edit matched a capped still | entity=%s | %s",
            entity.id,
            reason,
        )
        return None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() != dest.resolve():
            dest.write_bytes(target.read_bytes())
    except OSError:
        release_still(reserved, key)
        return None
    write_motion_sidecar(dest, profile=profile_for(slot))
    note_source(f"{kind}:{model_id or 'nanobanana'}", entity.id, int(beat_index) + 1)
    _write_edit_sidecar(
        dest,
        entity,
        reference=reference,
        license_name=license_name,
        credit=credit,
        title=title,
        source_url=source_url,
        author=author,
        provider="nanobanana",
        model_id=model_id,
        image_hash=key,
    )
    return dest


def _write_edit_sidecar(
    dest: Path,
    entity: Entity,
    *,
    reference: Path | None,
    license_name: str,
    credit: str,
    title: str,
    source_url: str,
    author: str,
    provider: str,
    model_id: str,
    image_hash: str,
) -> None:
    write_source_sidecar(
        dest,
        {
            "provider": provider,
            "origin": "nanobanana",
            "license": license_name,
            "credit_line": credit,
            "credit_required": bool(credit),
            "reference_image": str(reference or ""),
            "canonical_shape": entity.canonical_shape,
            "title": title,
            "source_url": source_url,
            "author": author,
            "model_id": model_id if provider == "nanobanana" else "",
            "image_hash": image_hash,
            "reuse_key": image_hash,
            "still_kind": "object",
        },
    )
    if not credit:
        return
    from production.providers.image.free.attribution import credit_line, write_sidecar
    from production.providers.image.free.models import Attribution

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
            provider=provider,
        ),
        credit=line,
    )


def _edit_prompt(entity: Entity, *, has_reference: bool) -> str:
    shape = entity.canonical_shape or entity.canonical_visual or entity.name
    if has_reference:
        text = (
            "Edit the reference photograph into a photoreal 9:16 documentary still. "
            f"Keep the real object's shape: {shape}. "
            "New museum lighting and a slightly different camera angle. "
            "Do not add text or a watermark. "
            "Do not invent parts that are absent from the reference."
        )
    else:
        text = (
            "Photoreal documentary photograph, 9:16, of this real artefact and nothing else: "
            f"{entity.name}. Shape: {shape}. Museum case, single raking lamp, "
            "real camera, no text, no watermark."
        )
    limits = _limits(entity)
    if limits:
        return f"{text} {limits}"
    return text


def _limits(entity: Entity) -> str:
    items = [
        str(item).strip().rstrip(".")
        for item in getattr(entity, "edit_negatives", ()) or ()
        if str(item).strip()
    ]
    if not items:
        return ""
    return "Constraints: " + ", ".join(items) + "."


def _fallback_cache(channel: str, entity_id: str) -> Path:
    from production.formats.economic_reel.artefact.fetch import artefact_cache_dir

    return artefact_cache_dir(channel, entity_id)


def _stamp(
    dest: Path,
    fetched: FetchedArtefact,
    entity: Entity,
    *,
    provider: str,
    model_id: str,
) -> None:
    from production.providers.image.free.attribution import write_sidecar
    from production.providers.image.free.models import Attribution

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
