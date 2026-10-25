# -*- coding: utf-8 -*-
"""Per-channel behaviour flags.

The engine reads these from ``channels_config/<id>/page_config.py``.
It does not branch on a channel name. A new channel is a new folder.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_CHANNELS = _ROOT / "channels_config"
_SKIP = frozenset({"shared", "aiwake"})

# Order the CLI has always offered. Folders added later sort after these.
_KNOWN_ORDER = (
    "anna_protocol",
    "master_mei",
    "wonder_feed",
    "down_dirty",
    "ancient_knowledge",
    "momma_circle",
    "principles_of_wealth_finance_economics",
    "endless_summer_paradise",
)

_CACHE: dict[str, dict[str, Any]] = {}


def channels_root() -> Path:
    return _CHANNELS


def discover_pages() -> tuple[str, ...]:
    """Channel folders that own a ``page_config.py``."""
    found: list[str] = []
    if _CHANNELS.is_dir():
        for child in _CHANNELS.iterdir():
            if not child.is_dir() or child.name.startswith(".") or child.name in _SKIP:
                continue
            if (child / "page_config.py").is_file():
                found.append(child.name)
    ordered = [name for name in _KNOWN_ORDER if name in found]
    ordered.extend(sorted(name for name in found if name not in ordered))
    return tuple(ordered)


def _constants(page_id: str) -> dict[str, Any]:
    """Public literals from page_config.py. Does not import the module."""
    slug = (page_id or "").strip().lower()
    if not slug:
        return {}
    cached = _CACHE.get(slug)
    if cached is not None:
        return cached
    path = _CHANNELS / slug / "page_config.py"
    out: dict[str, Any] = {}
    if path.is_file():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in tree.body:
            targets: list[str] = []
            value = None
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
            ):
                targets = [node.target.id]
                value = node.value
            if not targets or value is None:
                continue
            try:
                parsed = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                continue
            for name in targets:
                if not name.startswith("_"):
                    out[name] = parsed
    _CACHE[slug] = out
    return out


def value(page_id: str, name: str, default: Any = None) -> Any:
    cfg = _constants(page_id)
    if name not in cfg:
        return default
    return cfg[name]


def flag(page_id: str, name: str, default: bool = False) -> bool:
    return bool(value(page_id, name, default))


def enabled(page_ctx: Any, name: str) -> bool:
    """True when the loaded page context's config sets ``name``."""
    if page_ctx is None:
        return False
    cfg = getattr(page_ctx, "page_cfg", None) or {}
    if name in cfg:
        return bool(cfg.get(name))
    return flag(getattr(page_ctx, "page_id", ""), name, False)


def pages_with(name: str) -> tuple[str, ...]:
    return tuple(pid for pid in discover_pages() if flag(pid, name))


def vm_mkdir_relpaths() -> tuple[str, ...]:
    """Paths under the VM deploy root that a channel folder asks for."""
    paths: list[str] = []
    for pid in pages_with("VM_ENSURE_CHANNEL_DIR"):
        paths.append(f"channels_config/{pid}")
    for pid in pages_with("VM_STATE_MOUNT"):
        paths.append(f"state/{pid}/store")
    return tuple(paths)


def compose_store_mounts() -> tuple[str, ...]:
    """Host store bind mounts for channels that set ``VM_STATE_MOUNT``."""
    return tuple(
        f"./state/{pid}/store:/opt/omni-engine/channels_config/{pid}/store"
        for pid in pages_with("VM_STATE_MOUNT")
    )


def main(argv: list[str] | None = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if "--vm-mkdir" in args:
        print(" ".join(vm_mkdir_relpaths()))
        return 0
    if "--compose-mounts" in args:
        print("\n".join(compose_store_mounts()))
        return 0
    print("usage: python -m production.pipeline.channel_flags --vm-mkdir|--compose-mounts")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
