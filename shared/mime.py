# -*- coding: utf-8 -*-
"""Image MIME type from a filename, for vision API payloads."""
from __future__ import annotations


def image_mime_for(filename: str) -> str:
    """``image/png`` or ``image/webp`` by extension, else ``image/jpeg``."""
    lo = filename.lower()
    if lo.endswith(".png"):
        return "image/png"
    if lo.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"
