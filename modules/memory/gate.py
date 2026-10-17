# -*- coding: utf-8 -*-
"""Production gate. Offline is allowed unless ``SUPERMEMORY_REQUIRED`` is set."""

from __future__ import annotations

import logging
from typing import Any

from modules.memory.config import env_flag

_LOG = logging.getLogger("omni.memory")


def require_production_memory(
    bridge: Any,
    topic: str | None = None,
    *,
    container_tag: str = "",
) -> Any:
    """Return ``bridge``.

    A down service does not abort the pipeline. Set ``SUPERMEMORY_REQUIRED=1``
    to refuse production while it is offline. A near-duplicate topic still
    raises ``ValueError``, including a match that came from the local ledger.
    """
    active = bool(getattr(bridge, "is_active", False))
    base_url = str(getattr(bridge, "base_url", "") or "")
    if not active:
        if env_flag("SUPERMEMORY_REQUIRED"):
            raise RuntimeError(
                f"production animation requires Supermemory at {base_url}; "
                "the service is offline and SUPERMEMORY_REQUIRED is set"
            )
        _LOG.warning(
            "Supermemory offline at %s; production continues on the local ledger",
            base_url or "the configured host",
        )
    else:
        _LOG.info(
            "Supermemory production gate ready at %s (container_tag=%s)",
            base_url,
            container_tag or getattr(bridge, "history_container", ""),
        )
    if topic and bridge.check_topic_similarity(topic):
        raise ValueError(f"Supermemory rejected repetitive production topic: {topic}")
    return bridge
