# -*- coding: utf-8 -*-
"""AI-model persona generation.

Each avatar is a folder under ``models_avatar/avatars/``. The shared engine
loads that folder, builds a ComfyUI prompt, and writes images under the
factory ``OUTPUT_PATH``. This package does not register pages in
``channel_loader.VALID_PAGES``.
"""
from __future__ import annotations

__version__ = "0.1.0"
