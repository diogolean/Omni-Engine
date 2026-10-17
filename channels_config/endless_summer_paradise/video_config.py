# -*- coding: utf-8 -*-
"""Endless Summer Paradise — optional AI video clips.

Off unless enabled. The ingest library is unchanged; this only names the
provider a future clip step should call.

Environment overrides (prefix wins over the generic name):

    ESP_VIDEO_ENABLED=true
    ESP_VIDEO_PROVIDER=elevenlabs
    ESP_VIDEO_MODEL=veo-3.1-fast-generate-001
    ESP_VIDEO_ALLOW_SEEDANCE=false

Quality model, when you want it: ``veo-3.1-generate-001``.
Seedance model ids stay off until ElevenLabs approves access and
``VIDEO_ALLOW_SEEDANCE`` / ``ESP_VIDEO_ALLOW_SEEDANCE`` is true.
"""
from __future__ import annotations

ENV_PREFIX: str = "ESP"

# Master switch. The CLI can still render one explicit test clip.
VIDEO_ENABLED: bool = False

VIDEO_PROVIDER: str = "elevenlabs"
VIDEO_MODEL: str = "veo-3.1-fast-generate-001"

# bytedance-seedance-v2 / -fast / -mini / v2.5 need workspace approval.
VIDEO_ALLOW_SEEDANCE: bool = False

# Polling. ElevenLabs asks for at least 10 seconds between GETs.
VIDEO_POLL_MIN_INTERVAL_S: float = 10.0
VIDEO_POLL_TIMEOUT_S: float = 900.0
