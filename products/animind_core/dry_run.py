"""Unpaid end-to-end avatar dry-run: POST generate → GET puppet v3 + layer prints."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from . import admin_avatar, settings

LAYER_NAMES = ("torso", "neck", "head", "eyes", "brows")
VIEW_ORDER = ("front", "left", "right", "profile")


def enabled() -> bool:
    return (os.getenv("ANIMIND_DRY_RUN") or "").strip() in {"1", "true", "yes"} or settings.force_mock()


def run(body: dict[str, Any], owner: str, base_url: str) -> dict[str, Any]:
    """Force the mock provider through the public generate_avatar adapter."""
    if not enabled():
        raise RuntimeError("dry_run_disabled")
    previous = os.environ.get("ANIMIND_FORCE_MOCK")
    os.environ["ANIMIND_FORCE_MOCK"] = "1"
    try:
        payload = {**body, "generate": True, "dryRun": True}
        result = admin_avatar.run(payload, owner, base_url)
        result["dryRun"] = True
        result["providerCall"] = False
        return result
    finally:
        if previous is None:
            os.environ.pop("ANIMIND_FORCE_MOCK", None)
        else:
            os.environ["ANIMIND_FORCE_MOCK"] = previous


def layer_sheet(owner: str, character_id: str, destination: Path) -> Path:
    """Contact sheet of body/neck/head/eyes/brows for every available view."""
    character = admin_avatar.get(owner, character_id)
    if character is None:
        raise FileNotFoundError(character_id)
    skin = settings.data_dir() / "users" / owner / "puppets" / character_id
    cells: list[tuple[str, Image.Image]] = []
    for view_name in VIEW_ORDER:
        view_dir = skin / "views" / view_name
        if not view_dir.is_dir():
            continue
        for layer_name in LAYER_NAMES:
            path = view_dir / f"{layer_name}.png"
            if not path.is_file():
                continue
            with Image.open(path) as im:
                cells.append((f"{view_name}/{layer_name}", im.convert("RGBA").copy()))
        mouth = view_dir / "mouths" / "X.png"
        if mouth.is_file():
            with Image.open(mouth) as im:
                cells.append((f"{view_name}/mouth_X", im.convert("RGBA").copy()))
    if not cells:
        raise FileNotFoundError("no layer pngs")
    cell_w = max(img.width for _, img in cells)
    cell_h = max(img.height for _, img in cells) + 28
    cols = min(5, len(cells))
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * cell_w, rows * cell_h), (24, 28, 34, 255))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (label, img) in enumerate(cells):
        x = (index % cols) * cell_w
        y = (index // cols) * cell_h
        sheet.alpha_composite(img, (x + (cell_w - img.width) // 2, y + 24))
        draw.text((x + 4, y + 4), label, fill=(235, 240, 245, 255), font=font)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, format="PNG")
    return destination


def write_report(result: dict[str, Any], owner: str, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    puppet_path = out_dir / "puppet.v3.json"
    puppet_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    sheet_path = layer_sheet(owner, str(result["id"]), out_dir / "layers.png")
    summary = {
        "schema": result.get("schema"),
        "id": result.get("id"),
        "status": result.get("status"),
        "ready": result.get("ready"),
        "review_required": result.get("review_required"),
        "defects": result.get("defects"),
        "views": sorted((result.get("views") or {}).keys()),
        "dryRun": True,
        "providerCall": False,
        "puppetJson": str(puppet_path),
        "layerSheet": str(sheet_path),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> None:
    import argparse
    import hashlib
    import tempfile

    from fastapi.testclient import TestClient

    parser = argparse.ArgumentParser(prog="python -m products.animind_core.dry_run")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name", default="Ada")
    parser.add_argument("--prompt", default="Ada Lovelace, cartoon bust for dry-run")
    args = parser.parse_args(argv)

    data = Path(tempfile.mkdtemp(prefix="animind-dry-run-"))
    os.environ["ANIMIND_DATA_DIR"] = str(data)
    os.environ["ANIMIND_FORCE_MOCK"] = "1"
    os.environ["ANIMIND_DRY_RUN"] = "1"
    os.environ["CORE_API_KEY"] = "dry-run-key"
    os.environ["ANIMIND_ADMIN_TOKEN"] = "dry-run-admin-token-16"
    os.environ.pop("ANIMIND_GENERATION", None)
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("GOOGLE_API_KEY", None)

    from .app import app  # noqa: PLC0415

    client = TestClient(app)
    user = "dry_run_owner"
    headers = {
        "Authorization": "Bearer dry-run-key",
        "X-Animind-User": user,
        "X-Animind-Admin": "dry-run-admin-token-16",
        "Idempotency-Key": "dry-run-avatar-001",
        "X-Request-Id": "dry-run-request-001",
    }
    post = client.post(
        "/v1/avatars",
        headers=headers,
        json={"name": args.name, "prompt": args.prompt, "generate": True, "dryRun": True},
    )
    post.raise_for_status()
    body = post.json()
    owner = hashlib.sha256(user.encode()).hexdigest()[:24]
    got = client.get(f"/v1/avatars/{body['id']}", headers={
        "Authorization": "Bearer dry-run-key",
        "X-Animind-User": user,
    })
    got.raise_for_status()
    assert got.json() == body
    summary = write_report(body, owner, args.out)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
