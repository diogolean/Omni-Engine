# -*- coding: utf-8 -*-
"""Shared Supermemory client for every channel.

Speaks API v3 (container tags, ``POST /v3/documents``, ``POST /v3/search``)
through the ``supermemory`` 3.x SDK. Server 0.0.8 on the workstation and
server 0.0.9 or later on the VM both serve that API. The 5.x SDK speaks
``/ns/{namespace}`` and is not used here.

URL and key come from ``SUPERMEMORY_BASE_URL`` and ``SUPERMEMORY_API_KEY``
for any host. A down service falls back to a per-namespace JSONL ledger and
a retry queue.
"""

from modules.memory.ancient import (
    ANCIENT_KNOWLEDGE_REEL_ANGLES,
    ANCIENT_KNOWLEDGE_REEL_VISUALS,
    load_visual_memory,
    visual_memory_path,
)
from modules.memory.client import AddResult, MemoryClient
from modules.memory.config import MemorySettings, load_settings
from modules.memory.gate import require_production_memory
from modules.memory.namespaces import (
    AIWAKE_PRODUCTION_HISTORY,
    AIWAKE_SYSTEM_RULES,
    memory_namespace,
)
from modules.memory.similarity import lexical_similarity
from modules.memory.wsl import ensure_supermemory_running

__all__ = [
    "AIWAKE_PRODUCTION_HISTORY",
    "AIWAKE_SYSTEM_RULES",
    "ANCIENT_KNOWLEDGE_REEL_ANGLES",
    "ANCIENT_KNOWLEDGE_REEL_VISUALS",
    "AddResult",
    "MemoryClient",
    "MemorySettings",
    "ensure_supermemory_running",
    "lexical_similarity",
    "load_settings",
    "load_visual_memory",
    "memory_namespace",
    "require_production_memory",
    "visual_memory_path",
]
