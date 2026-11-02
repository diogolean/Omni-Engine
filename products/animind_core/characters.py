"""POST /v1/characters: contract v2 layered cut-out rig.

Real path (Gemini key set), see :mod:`products.animind_core.rig`:
  Gemini image (Aiwake ``create_puppet.generate_character_image``) -> BiRefNet
  matte (rembg ``birefnet-general``, as in the Rigly rigger) -> Gemini feature
  boxes -> torso / neck / mouthless head / eyes / lids / brows layers + 9 Rhubarb mouth sprites,
  in ``left`` and ``right`` (mirror) views. ``front`` reuses the left art.
Mock path (no key, or ``ANIMIND_FORCE_MOCK=1``): a flat PIL drawing cut into
the same layers, for wiring tests. ``generator`` says which one ran.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import avatar, casting, rig, settings, sheet, stick, styles, telemetry
from .image_models import BudgetExceeded

_LOG = logging.getLogger("animind_core")
_BUILD_LOCK = threading.Lock()  # one character at a time: BiRefNet needs ~8 GB on CPU
AIWAKE_AVATAR_SOURCES = (
    "products/aiwake/animator/factory/create_puppet.py",
    "products/aiwake/animator/factory/one_shot_factory.py",
    "animation/parametric/factory/layer_slicer.py",
    "products/aiwake/animator/render/facial_rig.py",
    "products/aiwake/animator/asset_generator.py",
    "products/aiwake/animator/factory/generate_concepts.py",
    "products/aiwake/animator/vision/head_analyzer.py",
    "animation/parametric/factory/landmark_detector.py",
)


def avatar_pipeline() -> dict[str, Any]:
    return {
        "sources": list(AIWAKE_AVATAR_SOURCES),
        "views": {"front": "layered", "left": "layered", "right": "mirror:left"},
        "parts": ["torso", "neck", "head", "eyes", "lids", "brows", "mouths:X,A-H", "emotions"],
    }


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return (re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_") or "character")[:32]


def puppets_dir() -> Path:
    return settings.data_dir() / "assets" / "puppets"


def load(character_id: str) -> dict[str, Any] | None:
    """Saved ``character.json`` for an id made by this core, else None."""
    if not re.fullmatch(r"[a-z0-9_]{1,48}", character_id or ""):
        return None
    path = puppets_dir() / character_id / "character.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


REUSE_MIN_SCORE = 80
CHARACTER_SET_CAP_USD = 0.30


def _identity_key(name: str, art_style: str) -> str:
    return f"{_slug(name)}_{art_style}"


def _spend_path(name: str, art_style: str) -> Path:
    return settings.data_dir() / "character_cache" / f"{_identity_key(name, art_style)}.json"


def image_spend(name: str, art_style: str) -> float:
    """USD already spent on images for this name and art style, across requests."""
    path = _spend_path(name, art_style)
    if not path.is_file():
        return 0.0
    try:
        return max(0.0, float(json.loads(path.read_text(encoding="utf-8")).get("spentUsd") or 0))
    except (OSError, ValueError, TypeError):
        return 0.0


def record_image_spend(name: str, art_style: str, cost_usd: float, character_id: str, score: int) -> None:
    path = _spend_path(name, art_style)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
    spent = max(0.0, float(current.get("spentUsd") or 0)) + max(0.0, float(cost_usd))
    best_score = int(current.get("bestScore") or -1)
    best_id = current.get("bestId")
    if score >= best_score:
        best_score, best_id = score, character_id
    path.write_text(json.dumps({
        "spentUsd": round(spent, 4),
        "bestId": best_id,
        "bestScore": best_score,
        "capUsd": CHARACTER_SET_CAP_USD,
    }), encoding="utf-8")


def image_cap_blocks(name: str, art_style: str, estimate_usd: float = 0.12) -> bool:
    """Another image set would pass the US$0.30 cap for this character."""
    return image_spend(name, art_style) + max(0.0, estimate_usd) > CHARACTER_SET_CAP_USD + 1e-9


def _saved_rigs(name: str, art_style: str) -> list[tuple[int, str, dict[str, Any]]]:
    found: list[tuple[int, str, dict[str, Any]]] = []
    root = puppets_dir()
    for path in root.glob("*/character.json") if root.is_dir() else []:
        try:
            character = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        score = int((character.get("qa") or {}).get("score") or 0)
        if (character.get("status") != "ready" or character.get("generator") != "gemini"
                or character.get("artStyle") != art_style or _slug(str(character.get("name") or "")) != _slug(name)
                or character.get("id") != path.parent.name or not (path.parent / "rig.json").is_file()):
            continue
        found.append((score, str(character.get("createdAt") or ""), character))
    return found


def reusable(name: str, art_style: str, base_url: str) -> dict[str, Any] | None:
    """A saved Gemini rig of the same persona and art style, or None.

    Asking for "Sócrates" again must not pay for a new image. A rig that passed
    image QA (score >= 80) wins. If the only saved rig failed QA once, that rig
    is still returned: regenerating it costs about US$0.12 and the US$0.30 cap
    is per character set. ``reuse: false`` (or a ``rawPrompt``) skips this.
    """
    saved = _saved_rigs(name, art_style)
    if not saved:
        return None
    passing = [item for item in saved if item[0] >= REUSE_MIN_SCORE]
    pool = passing or saved
    _score, _created, character = max(pool, key=lambda item: item[:2])
    text = re.sub(r"https?://[^\"\s]+?/v1/files/", f"{base_url}/v1/files/", json.dumps(character))
    reused = json.loads(text)
    reused.setdefault("gender", casting.public_gender(reused))
    skin = puppets_dir() / reused["id"]
    if not (skin / "views_contact_sheet.png").exists():
        sheet.build_views(skin, reused.get("name", ""))
    reused["viewsContactSheetUrl"] = f"{base_url}/v1/files/puppets/{reused['id']}/views_contact_sheet.png"
    reused.setdefault("avatarPipeline", avatar_pipeline())
    warning = None
    if _score < REUSE_MIN_SCORE:
        warning = "reused the best saved rig; image QA stayed under 80, so it was not redrawn"
    return reused | {"reused": True, **({"qaWarning": warning} if warning else {})}


def create_character(req: dict[str, Any], base_url: str) -> dict[str, Any]:
    started = time.perf_counter()
    source = req.get("source") or {}
    kind = source.get("kind")
    character_type = str(req.get("type") or "rig").strip().lower()
    if character_type not in {"rig", "stick"}:
        raise ValueError("type must be rig or stick")
    if character_type == "stick":
        subject = str(source.get("prompt") or req.get("name") or "personagem stick").strip()[:400]
    elif kind == "prompt":
        subject = str(source.get("prompt") or "").strip()[:400]
        if not subject:
            raise ValueError("source.prompt is required")
    elif kind == "upload":
        if source.get("rightsConfirmed") is not True:
            raise ValueError("upload requires rightsConfirmed: true")
        raise NotImplementedError("upload source is a photo rig made by the app; the core does not cut photos yet")
    elif kind == "catalog":
        raise NotImplementedError("catalog source is a photo rig made by the app; the core does not cut photos yet")
    else:
        raise ValueError("source.kind must be prompt, catalog, or upload")

    name = (str(req.get("name") or "").strip() or subject)[:40]
    assets = settings.data_dir() / "assets"
    art_style = styles.pick(req.get("artStyle"))
    image_model = avatar.configured_model()
    stick_options: dict[str, Any] = {}
    if character_type == "stick":
        palette = req.get("palette") if isinstance(req.get("palette"), dict) else {}
        clothing = req.get("clothing") if isinstance(req.get("clothing"), dict) else {}
        preset = str(req.get("preset") or "teacher").strip().lower()
        preset = preset if preset in stick.PRESETS else "teacher"

        def rgb(value: Any, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
            try:
                got = tuple(max(0, min(255, int(v))) for v in value[:3])
                return got if len(got) == 3 else fallback
            except (TypeError, ValueError):
                return fallback

        spec = stick.PRESETS[preset]
        stick_options = {
            "preset": preset,
            "colour": rgb(palette.get("skin"), spec["skin"]),
            "outfit": rgb(palette.get("outfit") or clothing.get("color"), spec["outfit"]),
            "accessory": str(req.get("accessory", spec["accessory"])).strip().lower(),
        }
        identity = {"name": name, **stick_options}
        if image_model:
            identity = {**identity, "description": subject, "style": art_style, "model": image_model}
        fingerprint = hashlib.sha256(
            json.dumps(identity, sort_keys=True, default=list).encode()
        ).hexdigest()[:12]
        character_id = f"stick_{preset}_{fingerprint}"
    else:
        character_id = f"{_slug(name)}_{uuid.uuid4().hex[:8]}"
    skin = assets / "puppets" / character_id
    files_base = f"{base_url}/v1/files"

    def url(path: Path) -> str:
        return f"{files_base}/{path.relative_to(assets).as_posix()}"

    reuse_art = req.get("reuse", True) is not False
    if character_type == "stick" and reuse_art and (skin / "character.json").is_file():
        saved = json.loads((skin / "character.json").read_text(encoding="utf-8"))
        saved["gender"] = casting.public_gender({**saved, **req, "name": name, "prompt": subject})
        if req.get("voiceId"):
            saved["voiceId"] = str(req["voiceId"])
        if not (skin / "views_contact_sheet.png").exists():
            sheet.build_views(skin, saved.get("name", name))
        saved["viewsContactSheetUrl"] = url(skin / "views_contact_sheet.png")
        saved.setdefault("avatarPipeline", avatar_pipeline())
        text = re.sub(r"https?://[^\"\s]+?/v1/files/", f"{base_url}/v1/files/", json.dumps(saved))
        reused = json.loads(text) | {"reused": True}
        telemetry.record(
            "avatar", provider="procedural", model=f"stick:{preset}",
            latency_ms=(time.perf_counter() - started) * 1000,
            image_qa=reused.get("qa"), artifact=skin / "character.png",
            status="cached", metadata={"views": sorted((reused.get("views") or {}).keys())},
        )
        return reused
    if character_type == "rig" and not req.get("rawPrompt") and (
        req.get("reuse", True) is not False or image_cap_blocks(name, art_style)
    ):
        found = reusable(name, art_style, base_url)
        if found:
            found["gender"] = casting.public_gender({**found, **req, "name": name, "prompt": subject})
            if req.get("voiceId"):
                found["voiceId"] = str(req["voiceId"])
            if image_cap_blocks(name, art_style):
                found["qaWarning"] = (
                    found.get("qaWarning")
                    or f"image cost cap US${CHARACTER_SET_CAP_USD:.2f} reached for this character; reused the saved rig"
                )
            _LOG.info("character %s reused for %r (no image generated)", found["id"], name)
            telemetry.record(
                "avatar", provider=str(found.get("generator") or "unknown"),
                model="cached-rig", latency_ms=(time.perf_counter() - started) * 1000,
                image_qa=found.get("qa"), status="cached",
                metadata={"views": sorted((found.get("views") or {}).keys())},
            )
            return found
    if character_type == "stick" and image_model:
        mode = "image"
    elif character_type == "stick":
        mode = "procedural"
    elif settings.gemini_available():
        mode = "gemini"
    elif image_model:
        mode = "image"
    else:
        mode = "mock"
    note = None if mode in {"gemini", "image"} else _mock_note()
    with _BUILD_LOCK:
        built = None
        if mode == "procedural":
            built = stick.build(skin, url, **stick_options)
            note = "no image provider configured; mock stick for tests and offline use"
        elif mode == "image":
            try:
                built = avatar.create_avatar(
                    subject, "stick" if character_type == "stick" else art_style,
                    kind=character_type, skin=skin, url=url, model_id=image_model,
                    reuse=reuse_art,
                )
                if character_type == "stick":
                    built.setdefault("preset", stick_options.get("preset"))
                    built.setdefault("customization", {
                        "skin": stick_options.get("colour"),
                        "outfit": stick_options.get("outfit"),
                        "accessory": stick_options.get("accessory"),
                    })
                mode = str(built.get("generator") or image_model)
            except (BudgetExceeded, Exception) as exc:  # noqa: BLE001
                if not isinstance(exc, BudgetExceeded) and not settings.is_billing_error(exc):
                    raise
                _LOG.warning("character %s: image provider unavailable (%s); mock art instead", character_id, str(exc)[:160])
                shutil.rmtree(skin, ignore_errors=True)
                skin.mkdir(parents=True, exist_ok=True)
                mode, note = "mock", f"image provider unavailable: {str(exc)[:200]}"
        elif mode == "gemini":
            try:
                built = rig.build_rig(subject, skin, url, raw_prompt=req.get("rawPrompt"), style=art_style)
            except Exception as exc:  # noqa: BLE001
                if not settings.is_billing_error(exc):
                    raise
                # No credit / bad key: never a 500. Draw the mock rig and say why, so the app can show it.
                _LOG.warning("character %s: Gemini unavailable (%s); mock art instead", character_id, str(exc)[:160])
                shutil.rmtree(skin, ignore_errors=True)
                mode, note = "mock", f"Gemini indisponível: {str(exc)[:200]}"
        if built is None:
            built = (
                stick.build(skin, url, **stick_options)
                if character_type == "stick"
                else rig.build_mock_rig(name, skin, url)
            )

    character = {
        "id": character_id,
        "name": name,
        "gender": casting.public_gender({**req, "name": name, "prompt": subject}),
        **({"voiceId": str(req["voiceId"])} if req.get("voiceId") else {}),
        "type": character_type,
        "status": "ready",
        "style": "rig",
        "views": built["views"],
        "createdAt": datetime.now(timezone.utc).isoformat(),
        # Additive fields beyond lib/contract.ts v2:
        "generator": mode,
        **({"model": built.get("model")} if built.get("model") else {}),
        # Why the art is mock (no key, ANIMIND_FORCE_MOCK, or Gemini 402/depleted credit); None when Gemini drew it.
        "generatorNote": note,
        "viewStatus": built["viewStatus"],
        "mouthShots": built["mouthShots"],
        "artStyle": art_style,
        "frontShot": built.get("frontShot", "mock"),
        **({"preset": built["preset"], "customization": built["customization"]} if character_type == "stick" else {}),
        # generate-then-verify QA: score 0-100, Gemini cost, attempts, failed gates; sheet stored with the character
        "qa": built.get("qa"),
        "qaSheetUrl": url(skin / "qa_sheet.png") if (skin / "qa_sheet.png").exists() else None,
        "frontImageUrl": url(skin / "character_front.png") if (skin / "character_front.png").exists() else None,
        "imageUrl": url(skin / "character.png"),
        # Review sheet: every viseme and eye state per view, for a human check.
        "reviewSheetUrl": url(sheet.build(skin, name, art_style)),
        "viewsContactSheetUrl": url(sheet.build_views(skin, name)),
        "avatarPipeline": avatar_pipeline(),
    }
    (skin / "character.json").write_text(json.dumps(character, indent=2), encoding="utf-8")
    qa_report = character.get("qa") or {}
    if character_type == "rig" and mode not in {"procedural", "mock"}:
        record_image_spend(
            name, art_style, float(qa_report.get("costUsd") or 0),
            character_id, int(qa_report.get("score") or 0),
        )
    attempts = qa_report.get("attempts") or {}
    images = (
        sum(
            int(value)
            for key, value in attempts.items()
            if isinstance(value, int) and "fallback" not in str(key).lower() and key != "rerig"
        )
        if mode not in {"procedural", "mock"} else 0
    )
    telemetry.record(
        "avatar",
        provider="google" if mode == "gemini" else mode,
        model=(character.get("model") or "gemini-2.5-flash-image") if mode == "gemini" else (
            character.get("model") or (
                f"stick:{character.get('preset')}" if mode == "procedural" else "mock-rig"
            )
        ),
        images=images,
        cost_usd=float(qa_report.get("costUsd") or 0.0),
        latency_ms=(time.perf_counter() - started) * 1000,
        image_qa=qa_report,
        artifact=skin / "character.png",
        metadata={"views": sorted(character["views"]), "type": character_type},
    )
    return character


def _mock_note() -> str:
    if settings.gemini_dead_reason():
        return f"Gemini indisponível: {settings.gemini_dead_reason()[:200]}"
    return "sem GEMINI_API_KEY (ou ANIMIND_FORCE_MOCK=1): arte mock"


def regenerate_front(character_id: str, base_url: str) -> dict[str, Any] | None:
    """Redraw the front view of a Gemini character (frontality check + one retry) and save it."""
    saved = load(character_id)
    if not saved:
        return None
    if saved.get("generator") != "gemini" or not settings.gemini_available():
        raise NotImplementedError("front regeneration needs a Gemini character and GEMINI_API_KEY")
    assets = settings.data_dir() / "assets"
    skin = puppets_dir() / character_id

    def url(path: Path) -> str:
        return f"{base_url}/v1/files/{path.relative_to(assets).as_posix()}"

    with _BUILD_LOCK:
        built = rig.regenerate_front(skin, url)
    saved["frontShot"] = built["frontShot"]
    if "views" in built:
        saved["views"]["front"] = built["views"]["front"]
        saved["viewStatus"]["front"] = "ready (gemini front view, regenerated)"
        saved["frontImageUrl"] = url(skin / "character_front.png")
        saved["reviewSheetUrl"] = url(sheet.build(skin, saved["name"], saved.get("artStyle", "cartoon")))
        saved["viewsContactSheetUrl"] = url(sheet.build_views(skin, saved["name"]))
    (skin / "character.json").write_text(json.dumps(saved, indent=2), encoding="utf-8")
    return saved


def rerig(character_id: str, base_url: str) -> dict[str, Any] | None:
    """Rebuild a saved character on the current layered rig from its stored shots (no image API)."""
    saved = load(character_id)
    if not saved:
        return None
    assets = settings.data_dir() / "assets"
    skin = puppets_dir() / character_id

    def url(path: Path) -> str:
        return f"{base_url}/v1/files/{path.relative_to(assets).as_posix()}"

    with _BUILD_LOCK:
        built = rig.rebuild_rig(skin, url)
    saved["views"], saved["qa"] = built["views"], built["qa"]
    saved["viewStatus"] = built["viewStatus"]
    saved["style"] = "rig"
    if (skin / "qa_sheet.png").exists():
        saved["qaSheetUrl"] = url(skin / "qa_sheet.png")
    saved["reviewSheetUrl"] = url(sheet.build(skin, saved.get("name", character_id), saved.get("artStyle", "cartoon")))
    saved["viewsContactSheetUrl"] = url(sheet.build_views(skin, saved.get("name", character_id)))
    (skin / "character.json").write_text(json.dumps(saved, indent=2), encoding="utf-8")
    return saved
