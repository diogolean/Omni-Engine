# -*- coding: utf-8 -*-
"""Render one artefact still for inspection.

    python -m production.formats.economic_reel.artefact.preview quimbaya
    python -m production.formats.economic_reel.artefact.preview quimbaya --ref
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from production.formats.economic_reel.artefact.fetch import fetch_artefact
from production.formats.economic_reel.artefact.route import acquire_photo
from production.formats.economic_reel.shot_planner.entities import Entity, load_entities


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render one artefact still.")
    parser.add_argument("entity", help="Entity id, name, or alias")
    parser.add_argument(
        "--ref",
        action="store_true",
        help="Require a reference-conditioned edit",
    )
    parser.add_argument("--channel", default="ancient_knowledge")
    args = parser.parse_args(argv)
    from production.cost.google_guardrail import bind_run_channel

    bind_run_channel(args.channel)
    entity = resolve_entity(args.entity, args.channel)
    if entity is None:
        print(f"Unknown entity: {args.entity}", file=sys.stderr)
        return 2
    from shared.paths import review_path

    dest = review_path(args.channel, "artefact_preview", f"{entity.id}.jpg")
    cache_dir: Path | None = None
    mode = "chain"
    if args.ref:
        fetched = fetch_artefact(entity, channel=args.channel)
        reference = fetched.reference_path if fetched is not None else None
        if reference is None or not Path(reference).is_file():
            print(f"No Commons reference for {entity.id}. Ref-edit refused.", file=sys.stderr)
            return 1
        cache_dir = Path(reference).parent
        mode = "nanobanana"
    try:
        path = acquire_photo(
            entity,
            dest,
            channel=args.channel,
            mode=mode,
            cache_dir=cache_dir,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if path is None or not Path(path).is_file():
        print(f"No still written for {entity.id}.", file=sys.stderr)
        return 1
    side = _source(path)
    reference_used = bool(side.get("reference_image"))
    print(f"path={path}")
    print(f"provider={side.get('provider') or ''}")
    print(f"model={side.get('model_id') or ''}")
    print(f"reference={'yes' if reference_used else 'no'}")
    return 0


def resolve_entity(query: str, channel: str) -> Entity | None:
    needle = " ".join((query or "").strip().lower().split())
    if not needle:
        return None
    entities = load_entities(channel)
    for entity in entities:
        aliases = {alias.lower() for alias in entity.aliases}
        names = {entity.id.lower(), entity.name.lower(), *aliases}
        if needle in names:
            return entity
    for entity in entities:
        if needle in entity.id.lower() or needle in entity.name.lower():
            return entity
    return None


def _source(path: Path) -> dict:
    side = Path(str(path) + ".source.json")
    if not side.is_file():
        return {}
    try:
        payload = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
