# -*- coding: utf-8 -*-
"""Gemini image generation for one artefact still.

The call tries ``gemini-2.5-flash-image`` (Nano Banana) first. Google's
docs mark that id for shutdown on 2 October 2026, so a failure falls
through to ``gemini-nano-banana-2.1`` and then the Flash Lite image model.
A reference photograph is sent when one exists.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from modules.reel_artefact.pricing import NANOBANANA_MODELS

logger = logging.getLogger(__name__)


def model_chain() -> list[str]:
    override = (os.getenv("REEL_NANOBANANA_MODEL") or "").strip()
    if override:
        rest = [item for item in NANOBANANA_MODELS if item != override]
        return [override, *rest]
    return list(NANOBANANA_MODELS)


def generate_artefact_image(
    prompt: str,
    dest: Path,
    *,
    reference: Path | None = None,
    aspect_ratio: str = "9:16",
) -> str:
    """Write one image to ``dest``. Returns the model id that produced it."""
    key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing")
    from google.genai import types  # noqa: PLC0415

    from core.google_guardrail import (  # noqa: PLC0415
        guarded_generate_content,
        make_guarded_gemini_client,
    )

    client = make_guarded_gemini_client(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    config = types.GenerateContentConfig(
        response_modalities=["TEXT", "IMAGE"],
        image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
    )
    contents: Any = prompt
    if reference is not None and Path(reference).is_file():
        mime = "image/png" if Path(reference).suffix.lower() == ".png" else "image/jpeg"
        contents = [
            types.Part.from_bytes(data=Path(reference).read_bytes(), mime_type=mime),
            types.Part.from_text(text=prompt),
        ]
    last_error = "no_model"
    for model in model_chain():
        try:
            response = guarded_generate_content(
                client,
                model=model,
                contents=contents,
                config=config,
                kind="image",
                source="reel_artefact.nanobanana",
            )
        except Exception as exc:  # noqa: BLE001
            last_error = f"{model}:{type(exc).__name__}"
            logger.info("Nano Banana %s failed (%s)", model, type(exc).__name__)
            continue
        if _save_response(response, dest):
            return model
        last_error = f"{model}:no_image"
    raise RuntimeError(f"nanobanana_failed:{last_error}")


def _save_response(response: Any, dest: Path) -> bool:
    for candidate in getattr(response, "candidates", None) or ():
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or ():
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None)
            if data:
                dest.write_bytes(bytes(data))
                return dest.is_file() and dest.stat().st_size > 32
    return False
