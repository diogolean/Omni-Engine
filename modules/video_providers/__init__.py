# -*- coding: utf-8 -*-
"""Video generation providers.

Active backend: ElevenLabs Flows (Veo 3.1). Gemini Veo and Kling/fal are
skeletons behind the same interface and do not send requests.

Channels keep generation off until ``video_config.py`` or an env override
enables it. The CLI is the explicit one-clip entry point.
"""
from modules.video_providers.registry import get_provider
from modules.video_providers.service import generate_clip
from modules.video_providers.types import ReferenceImage, VideoRequest

__all__ = [
    "ReferenceImage",
    "VideoRequest",
    "generate_clip",
    "get_provider",
]
