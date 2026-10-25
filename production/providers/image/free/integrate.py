# -*- coding: utf-8 -*-
"""Hooks the free-image search into card and reel producers.

The search runs before Flux. A hit is a template input: Pinterest and
Facebook cards still go through the card compositor, and reel stills still
go through the reel treatment. The raw download is not a publishable file.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from production.providers.image.free.attribution import credit_for_image
from production.providers.image.free.policy import reel_free_images_enabled, resolve_policy
from production.providers.image.free.sourcer import resolve_image_source

logger = logging.getLogger(__name__)


class FreeImageUnavailable(RuntimeError):
    """``free_only`` found nothing that passed the license and quality checks."""


def wrap_image_generate(
    generate_fn,
    *,
    channel: str,
    page_cfg: dict | None = None,
    query: str = "",
    producer: str = "reel_image",
):
    """Return ``generate_fn`` unchanged for ``ai_only`` channels.

    ``free_first`` uses a vetted photograph when one passes and otherwise
    calls the original generator (Flux). ``free_only`` does not call it.
    """
    policy = resolve_policy(channel, page_cfg)
    reel = str(producer or "").startswith("reel")
    if policy == "ai_only":
        return generate_fn
    if reel and not reel_free_images_enabled(page_cfg):
        def _passthrough(prompt, output_stem=None, output_directory=None, **kwargs):
            kwargs.pop("free_image_sentence", None)
            kwargs.pop("free_image_entity", None)
            return generate_fn(
                prompt,
                output_stem=output_stem,
                output_directory=output_directory,
                **kwargs,
            )

        return _passthrough

    def _wrapped(prompt, output_stem=None, output_directory=None, **kwargs):
        sentence = str(kwargs.pop("free_image_sentence", "") or "")
        entity = str(kwargs.pop("free_image_entity", "") or "")
        # A full Flux prompt is a poor search query. Prefer the beat sentence.
        search_sentence = sentence or ("" if entity else str(prompt or ""))
        dest_dir = Path(output_directory) if output_directory else Path(".")
        stem = str(output_stem or "free_image")
        dest = dest_dir / f"{stem}.jpg"
        decision = resolve_image_source(
            channel=channel,
            dest=dest,
            topic=query,
            entity=entity,
            sentence=search_sentence,
            policy=policy,
            page_cfg=page_cfg,
            producer=producer,
        )
        if decision.hit is not None:
            logger.info(
                "Skipping generator for %s — free image %s",
                producer,
                decision.hit.path.name,
            )
            return decision.hit.path
        if policy == "free_only":
            raise FreeImageUnavailable(decision.reason or "none_passed")
        logger.info(
            "Free image miss (%s) — Flux fallback for %s",
            decision.reason,
            producer,
        )
        return generate_fn(
            prompt,
            output_stem=output_stem,
            output_directory=output_directory,
            **kwargs,
        )

    return _wrapped


def load_card_source(
    record: dict,
    work_dir: Path,
    *,
    allow_flux: bool = True,
) -> Image.Image | None:
    """Portrait source for a Pinterest or Facebook card template.

    Owned local files are the caller's job. This runs only when that file
    is missing. ``ai_only`` returns None so those channels keep their assets.
    """
    channel = _record_channel(record)
    page_cfg = record.get("page_cfg")
    policy = resolve_policy(channel, page_cfg if isinstance(page_cfg, dict) else None)
    if policy == "ai_only":
        return None
    topic = str(record.get("topic") or record.get("visual_hook") or "")
    entity = str(record.get("entity") or record.get("named_entity") or "")
    sentence = str(record.get("original_caption") or record.get("humanized_caption") or "")
    constraints: dict = {}
    if channel:
        try:
            from production.formats.pins.scene_prompt import search_constraints

            constraints = search_constraints(channel, record)
        except Exception as exc:  # noqa: BLE001
            logger.info("Card subject lookup skipped (%s).", type(exc).__name__)
    slug = str(record.get("subject_slug") or "card").strip() or "card"
    dest = Path(work_dir) / f"{slug}_free_source.jpg"
    decision = resolve_image_source(
        channel=channel,
        dest=dest,
        topic=topic,
        entity=str(constraints.get("subject") or entity),
        sentence=sentence,
        policy=policy,
        producer=str(record.get("producer") or "pinterest_card"),
        required_terms=constraints.get("required_terms") or (),
        reject_terms=constraints.get("reject_terms") or (),
        allow_illustration=bool(constraints.get("allow_illustration")),
    )
    if decision.hit is None and policy == "free_first" and allow_flux:
        fallback_path = _flux_fallback(topic or sentence or entity, dest)
        if fallback_path is not None:
            decision.used_fallback = True
            decision.fallback_path = fallback_path
            record["image_source"] = "flux_fallback"
        else:
            return None
    elif decision.hit is None:
        return None
    path = decision.hit.path if decision.hit is not None else decision.fallback_path
    if path is None or not Path(path).is_file():
        return None
    if decision.hit is not None:
        from production.providers.image.free.attribution import credit_required

        license_name = str(decision.hit.attribution.license or "")
        record["image_credit"] = decision.hit.credit_line
        record["image_source"] = "free_license"
        record["image_attribution"] = decision.hit.attribution.to_json()
        record["image_license"] = license_name
        record["image_credit_required"] = credit_required(license_name)
    elif decision.used_fallback:
        record["image_credit"] = ""
        record["image_license"] = ""
        record["image_credit_required"] = False
    try:
        with Image.open(path) as raw:
            return raw.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        logger.info("Free card source unreadable (%s)", type(exc).__name__)
        return None


def acquire_free_file(
    dest: Path,
    *,
    channel: str,
    topic: str = "",
    entity: str = "",
    sentence: str = "",
    producer: str = "pinterest_card",
) -> bool:
    """Write one template source to ``dest``. True when a free image or Flux fill landed."""
    policy = resolve_policy(channel)
    if policy == "ai_only":
        return False
    try:
        decision = resolve_image_source(
            channel=channel,
            dest=dest,
            topic=topic,
            entity=entity,
            sentence=sentence,
            policy=policy,
            producer=producer,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("Free image search failed (%s)", type(exc).__name__)
        decision = None
    if decision is not None and decision.hit is not None and dest.is_file():
        return True
    if policy != "free_first":
        return False
    fallback = _flux_fallback(topic or sentence or entity, dest)
    return fallback is not None and Path(fallback).is_file()


def card_credit(path: Path | str | None) -> str:
    return credit_for_image(path)


def _flux_fallback(prompt: str, dest: Path) -> Path | None:
    text = (prompt or "").strip()
    if not text:
        return None
    try:
        from production.providers.image.image_provider import get_image_adapter

        adapter = get_image_adapter()
        path = adapter.generate(
            text,
            output_stem=dest.stem,
            output_directory=dest.parent,
            avatar_mode="OFF",
        )
    except Exception as exc:  # noqa: BLE001 — missing keys must not break the drip
        logger.info("Flux fallback unavailable (%s)", type(exc).__name__)
        return None
    written = Path(path) if path else None
    if written is None or not written.is_file():
        return None
    if written.resolve() != Path(dest).resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(written.read_bytes())
    return dest if dest.is_file() else written


def _record_channel(record: dict) -> str:
    for key in ("channel_id", "page_id", "branding"):
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    try:
        from production.formats.pins import config as cfg

        return str(cfg.CHANNEL_ID or "")
    except Exception:  # noqa: BLE001
        return ""
