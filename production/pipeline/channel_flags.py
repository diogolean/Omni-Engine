# -*- coding: utf-8 -*-
"""Per-channel behaviour flags.

The reader lives in ``shared.channel_flags`` so path helpers can use it
without importing production. This module is the pipeline entry point
(``python -m production.pipeline.channel_flags``).
"""
from shared.channel_flags import (
    channels_root,
    coalesce_page,
    compose_store_mounts,
    default_page,
    discover_channel_ids,
    discover_pages,
    enabled,
    flag,
    main,
    pages_with,
    sole_page,
    value,
    vm_mkdir_relpaths,
)

__all__ = [
    "channels_root",
    "coalesce_page",
    "compose_store_mounts",
    "default_page",
    "discover_channel_ids",
    "discover_pages",
    "enabled",
    "flag",
    "main",
    "pages_with",
    "sole_page",
    "value",
    "vm_mkdir_relpaths",
]

if __name__ == "__main__":
    raise SystemExit(main())
