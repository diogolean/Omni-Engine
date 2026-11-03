"""Compatibility shim. Image adapters live in production.providers.image.

Gemini helpers, the model router, and the visual judge are sibling shims.
"""
from __future__ import annotations

_IMAGE_EXPORTS = frozenset(
    {
        "GeminiImageAdapter",
        "ImageProvider",
        "TogetherImageAdapter",
        "TogetherImageGenerator",
        "get_image_adapter",
    }
)


def __getattr__(name: str):
    if name not in _IMAGE_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    image = importlib.import_module("production.providers.image")
    return getattr(image, name)
