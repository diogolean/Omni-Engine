# -*- coding: utf-8 -*-
"""Optional descriptor for a future channel-loader hook.

Avatar folders are the unit of configuration. They are not entries in
``channel_loader.VALID_PAGES``. Registering one there would pull the video
factory (master DNA, voice, posting) into a still-image persona and would
change page resolution for every existing channel. This helper only exposes
the fields a later, explicit registration would need.
"""
from __future__ import annotations

from typing import Any

from production.providers.avatar.avatar_config import load_avatar


def channel_descriptor(avatar_id: str) -> dict[str, Any]:
    avatar = load_avatar(avatar_id)
    return {
        "page_id": avatar.id,
        "display_name": avatar.identity.name,
        "kind": "models_avatar",
        "trigger": avatar.identity.trigger,
        "presets": sorted(avatar.presets),
        "default_preset": avatar.default_preset,
        "workflow": str(avatar.workflow_path),
        "registered_in_channel_loader": False,
    }
