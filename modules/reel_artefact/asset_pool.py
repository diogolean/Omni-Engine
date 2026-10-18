# -*- coding: utf-8 -*-
"""Photographs first. The script and the shots are built from what we can show.

Commons (search, the topic category, one level of subcategories) is the pool.
Openverse is only the backup. Each file is judged once. Nano Banana fills a
key object only when fewer than three real object photographs survived.
Flux is reserved for an explicit sky, landscape, or atmosphere line.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

TARGET_MIN = 8
TARGET_MAX = 15
OBJECT_FLOOR = 3
NANO_MAX = 4
FLUX_MAX = 6
OPENVERSE_FLOOR = 8
_SCORE_FLOOR = 0.70

_ATMOSPHERE = re.compile(
    r"\b(skies|sky|clouds?|sunset|sunrise|dawn|dusk|atmosphere|horizon|"
    r"landscapes?|storms?|moonlight|starlit|weather)\b",
    re.I,
)
_OBJECT = re.compile(
    r"\b(jars?|batter(?:y|ies)|vessels?|artefacts?|artifacts?|mechanisms?|"
    r"gears?|pendants?|figurines?|statues?|pottery|relics?)\b",
    re.I,
)
_SITE = re.compile(
    r"\b(pillars?|enclosures?|ruins|temples?|excavations?|mounds?|"
    r"t-shaped|stone circles?)\b",
    re.I,
)
_STOP = frozenset({
    "this", "that", "with", "from", "into", "over", "under", "near",
    "were", "was", "were", "have", "been", "they", "them", "their",
    "about", "after", "before", "where", "when", "which", "while",
    "file", "photo", "image", "photograph",
})
_ANCHORS = ("center", "left", "right", "upper", "lower")


@dataclass
class PoolAsset:
    path: str
    caption: str
    kind: str
    source: str
    key: str


@dataclass
class ShotAssignment:
    sentence: str
    mode: str
    caption: str = ""
    anchor: str = ""
    prompt: str = ""
    asset_key: str = ""


def asset_prompt_block(captions: list[str] | None) -> str:
    """The photographs the writer is allowed to describe."""
    lines = [str(item).strip() for item in (captions or []) if str(item).strip()]
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines[:TARGET_MAX])
    return (
        "\nPHOTOGRAPHS AVAILABLE (write the story around these; "
        "do not describe an object or a site we cannot show):\n"
        f"{body}\n"
    )


def classify_kind(text: str) -> str:
    if _OBJECT.search(text or ""):
        return "object"
    if _SITE.search(text or "") or _ATMOSPHERE.search(text or ""):
        return "site"
    return "site"


def wants_flux(sentence: str) -> bool:
    """Sky, landscape, or weather, and not the object or the site itself."""
    if _OBJECT.search(sentence or "") or _SITE.search(sentence or ""):
        return False
    return bool(_ATMOSPHERE.search(sentence or ""))


def accept_verdict(verdict: dict[str, Any] | None) -> bool:
    """One judge pass. Text panels fail. A roof over the stones can pass."""
    row = verdict or {}
    if row.get("contains_text"):
        return False
    reason = str(row.get("rejection_reason") or "")
    if "contains_text" in reason:
        return False
    score = row.get("relevance_score", row.get("score"))
    if score is not None and float(score) < _SCORE_FLOOR:
        return False
    return bool(row.get("passed") or row.get("is_relevant"))


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{4,}", (text or "").lower())
        if token not in _STOP
    }


def _overlap(sentence: str, caption: str) -> int:
    return len(_tokens(sentence) & _tokens(caption))


def best_asset(sentence: str, assets: list[PoolAsset]) -> PoolAsset | None:
    if not assets:
        return None
    prefer = "object" if _OBJECT.search(sentence or "") else ""
    if not prefer and _SITE.search(sentence or ""):
        prefer = "site"
    if not prefer and wants_flux(sentence or ""):
        prefer = "site"
    ranked = sorted(
        assets,
        key=lambda asset: (
            _overlap(sentence, asset.caption),
            1 if prefer and asset.kind == prefer else 0,
        ),
        reverse=True,
    )
    return ranked[0]


def pick_anchor(
    asset_key: str,
    previous: tuple[str, str] | None,
    used: dict[tuple[str, str], int],
) -> str:
    """A different window of the same photograph is a different shot.

    The same window on the next beat is not.
    """
    order = sorted(_ANCHORS, key=lambda name: used.get((asset_key, name), 0))
    for name in order:
        if previous == (asset_key, name):
            continue
        return name
    return "left" if previous == (asset_key, "center") else "center"


def assign_shots(
    sentences: list[str],
    assets: list[PoolAsset],
    *,
    geography: str = "",
    flux_max: int = FLUX_MAX,
) -> list[ShotAssignment]:
    """Real photographs for objects and the site. Flux only for the sky."""
    from modules.reel_artefact.flux_policy import safe_flux_prompt

    flux_used = 0
    previous: tuple[str, str] | None = None
    used: dict[tuple[str, str], int] = {}
    assigned: list[ShotAssignment] = []
    for sentence in sentences:
        text = str(sentence or "").strip()
        if wants_flux(text) and flux_used < int(flux_max):
            flux_used += 1
            assigned.append(ShotAssignment(
                sentence=text,
                mode="flux",
                prompt=safe_flux_prompt(text, geography=geography, scene="landscape"),
            ))
            previous = None
            continue
        asset = best_asset(text, assets)
        if asset is None:
            assigned.append(ShotAssignment(sentence=text, mode="fallback"))
            continue
        anchor = pick_anchor(asset.key, previous, used)
        used[(asset.key, anchor)] = used.get((asset.key, anchor), 0) + 1
        previous = (asset.key, anchor)
        assigned.append(ShotAssignment(
            sentence=text,
            mode="asset",
            caption=asset.caption,
            anchor=anchor,
            asset_key=asset.key,
        ))
    return assigned


def fallback_assignment(
    sentence: str,
    assets: list[PoolAsset],
    previous: tuple[str, str] | None = None,
) -> ShotAssignment:
    """A different crop of a relevant pool photograph. Not a random landscape."""
    asset = best_asset(sentence, assets)
    if asset is None:
        return ShotAssignment(sentence=sentence, mode="fallback")
    used: dict[tuple[str, str], int] = {}
    if previous is not None:
        used[previous] = 1
    anchor = pick_anchor(asset.key, previous, used)
    return ShotAssignment(
        sentence=sentence,
        mode="fallback",
        caption=asset.caption,
        anchor=anchor,
        asset_key=asset.key,
    )


def _candidate_key(item: Any) -> str:
    for attr in ("key", "download_url", "title", "caption"):
        value = str(getattr(item, attr, "") or "")
        if value:
            return value.split("?")[0].lower()
    return str(id(item))


def _candidate_caption(item: Any) -> str:
    title = str(getattr(item, "title", "") or getattr(item, "caption", "") or "")
    description = str(getattr(item, "description", "") or "")
    return " ".join(part for part in (title, description) if part).strip() or title


def build_asset_pool(
    *,
    queries: list[str],
    categories: list[str],
    dest: Path,
    search: Callable[[str], list[Any]],
    category: Callable[[str], list[Any]],
    subcategories: Callable[[str], list[str]],
    openverse: Callable[[str], list[Any]],
    download: Callable[[Any, Path], Path | None],
    judge: Callable[[Path, str, str], dict[str, Any]],
    nano: Callable[[str, Path], Path | None] | None = None,
    object_prompts: list[str] | None = None,
    target_min: int = TARGET_MIN,
    target_max: int = TARGET_MAX,
    object_floor: int = OBJECT_FLOOR,
    nano_max: int = NANO_MAX,
    openverse_floor: int = OPENVERSE_FLOOR,
) -> list[PoolAsset]:
    """Download and judge each candidate once. Stop at the usable target."""
    folder = Path(dest)
    folder.mkdir(parents=True, exist_ok=True)
    commons: list[Any] = []
    seen: set[str] = set()

    def take(items: list[Any]) -> None:
        for item in items or []:
            key = _candidate_key(item)
            if not key or key in seen:
                continue
            seen.add(key)
            commons.append(item)

    for query in [item for item in queries if str(item).strip()][:8]:
        take(search(query))
    for name in [item for item in categories if str(item).strip()]:
        take(category(name))
        for sub in list(subcategories(name) or [])[:6]:
            take(category(sub))

    kept = _judge_candidates(
        commons, folder, download, judge, source="commons", limit=int(target_max),
    )
    if len(kept) < int(openverse_floor):
        extra: list[Any] = []
        for query in [item for item in queries if str(item).strip()][:3]:
            for item in openverse(query) or []:
                key = _candidate_key(item)
                if key and key not in seen:
                    seen.add(key)
                    extra.append(item)
        room = int(target_max) - len(kept)
        if room > 0 and extra:
            kept.extend(_judge_candidates(
                extra, folder, download, judge, source="openverse", limit=room,
            ))
    objects = sum(1 for item in kept if item.kind == "object")
    prompts = [item for item in (object_prompts or []) if str(item).strip()] or ["the object"]
    calls = 0
    while objects < int(object_floor) and calls < int(nano_max) and nano is not None:
        prompt = prompts[calls % len(prompts)]
        calls += 1
        path = folder / f"nano_{calls:02d}.jpg"
        try:
            written = nano(prompt, path)
        except Exception as exc:  # noqa: BLE001
            logger.info("ASSET POOL nano failed (%s)", type(exc).__name__)
            continue
        if written is None or not Path(written).is_file():
            continue
        verdict = judge(Path(written), prompt, "object") or {}
        if not accept_verdict(verdict):
            continue
        kept.append(PoolAsset(
            path=str(written),
            caption=prompt,
            kind="object",
            source="nano",
            key=f"nano:{calls}",
        ))
        objects += 1
        if len(kept) >= int(target_max):
            break
    logger.info(
        "ASSET POOL | kept=%d object=%d min=%d",
        len(kept),
        sum(1 for item in kept if item.kind == "object"),
        target_min,
    )
    return kept[: int(target_max)]


def _judge_candidates(
    candidates: list[Any],
    folder: Path,
    download: Callable[[Any, Path], Path | None],
    judge: Callable[[Path, str, str], dict[str, Any]],
    *,
    source: str,
    limit: int,
) -> list[PoolAsset]:
    kept: list[PoolAsset] = []
    for index, item in enumerate(candidates):
        if len(kept) >= limit:
            break
        caption = _candidate_caption(item)
        kind = str(getattr(item, "kind", "") or "") or classify_kind(caption)
        try:
            path = download(item, folder)
        except Exception as exc:  # noqa: BLE001
            logger.info("ASSET POOL download failed (%s)", type(exc).__name__)
            continue
        if path is None or not Path(path).is_file():
            continue
        verdict = judge(Path(path), caption, kind) or {}
        if not accept_verdict(verdict):
            continue
        judged_kind = str(verdict.get("kind") or kind or "site")
        kept.append(PoolAsset(
            path=str(path),
            caption=caption or Path(path).stem,
            kind=judged_kind if judged_kind in {"object", "site", "landscape"} else kind,
            source=source,
            key=_candidate_key(item) or f"{source}:{index}",
        ))
    return kept


def write_asset_crop(source: Path | str, dest: Path | str, anchor: str) -> Path:
    from PIL import Image

    from modules.reel_artefact.crop import prepare_reel_frame

    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        frame, _how = prepare_reel_frame(image, anchor or "center")
    frame.save(target, quality=90)
    return target


def place_assignment(
    assignment: ShotAssignment,
    dest: Path | str,
    pool: list[PoolAsset],
    *,
    flux_generate: Callable[..., Any] | None = None,
    geography: str = "",
    judge_fn: Callable[..., dict[str, Any]] | None = None,
    beat_index: int = 0,
) -> Any:
    """One still. A failed Flux sky falls back to a relevant crop."""
    from modules.reel_qa.models import StillGateResult

    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    if assignment.mode == "flux" and flux_generate is not None:
        written = _call_flux(flux_generate, assignment.prompt or assignment.sentence, target)
        passed = written.is_file()
        if passed:
            verdict = _judge_flux(written, assignment.sentence, geography, judge_fn)
            passed = accept_verdict(verdict)
        if passed and written.is_file():
            return StillGateResult(
                path=written,
                extra_generations=1,
                publishable=True,
                first_pass=True,
                attempts=[{
                    "provider": "flux",
                    "qa_status": "pass",
                    "relevance_score": 0.9,
                }],
            )
    chosen = _asset_by_key(pool, assignment.asset_key) or best_asset(assignment.sentence, pool)
    if chosen is None and pool:
        chosen = pool[beat_index % len(pool)]
    if chosen is None:
        return StillGateResult(
            path=None,
            extra_generations=0,
            publishable=False,
            blocked_reason="empty_pool",
        )
    anchor = assignment.anchor or pick_anchor(chosen.key, None, {})
    if assignment.mode == "flux":
        anchor = pick_anchor(chosen.key, (chosen.key, anchor), {(chosen.key, anchor): 1})
    write_asset_crop(chosen.path, target, anchor)
    provider = "fallback" if assignment.mode == "flux" else (assignment.mode or "asset")
    return StillGateResult(
        path=target,
        extra_generations=0,
        publishable=True,
        first_pass=True,
        attempts=[{
            "provider": provider,
            "qa_status": "pass",
            "relevance_score": 0.9,
            "model_id": anchor,
        }],
    )


def make_pool_job(
    assignment: ShotAssignment,
    dest: Path | str,
    pool: list[PoolAsset],
    flux_generate: Callable[..., Any] | None,
    geography: str,
    beat_index: int,
    judge_fn: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[], Any]:
    import functools

    return functools.partial(
        place_assignment,
        assignment,
        dest,
        pool,
        flux_generate=flux_generate,
        geography=geography,
        judge_fn=judge_fn,
        beat_index=beat_index,
    )


def _asset_by_key(pool: list[PoolAsset], key: str) -> PoolAsset | None:
    if not key:
        return None
    for asset in pool:
        if asset.key == key:
            return asset
    return None


def _call_flux(flux_generate: Callable[..., Any], prompt: str, dest: Path) -> Path:
    try:
        result = flux_generate(
            prompt,
            output_stem=dest.stem,
            output_directory=dest.parent,
        )
    except TypeError:
        result = flux_generate(prompt, dest)
    written = Path(result) if result else dest
    if written.is_file() and written.resolve() != dest.resolve():
        dest.write_bytes(written.read_bytes())
        return dest
    return written if written.is_file() else dest


def _judge_flux(
    path: Path,
    sentence: str,
    geography: str,
    judge_fn: Callable[..., dict[str, Any]] | None,
) -> dict[str, Any]:
    if judge_fn is not None:
        return judge_fn(path, sentence) or {}
    from modules.reel_qa.relevance import evaluate_chunk_relevance

    verdict = evaluate_chunk_relevance(
        path,
        sentence,
        sentence,
        score_against="shot",
        geography=geography,
        scene_type="landscape",
        shot_subject=sentence,
    )
    return {
        "passed": bool(verdict.get("is_relevant")),
        "contains_text": "contains_text" in str(verdict.get("rejection_reason") or ""),
        "relevance_score": verdict.get("relevance_score"),
    }


def build_topic_pool(topic: str, channel: str, dest: Path) -> list[PoolAsset]:
    """Production pool. A search or judge failure returns what was already kept."""
    from modules.reel_artefact.fetch import (
        RequestsClient,
        _commons_category,
        _commons_search,
        _openverse_search,
        commons_subcategories,
        download_candidate_bytes,
    )
    from modules.reel_artefact.preview import resolve_entity
    from modules.reel_qa.relevance import evaluate_chunk_relevance
    from modules.shot_planner.entities import entity_for_line, load_entities

    entities = load_entities(channel)
    entity = entity_for_line(topic, entities, topic=topic) or resolve_entity(topic, channel)
    queries = list(entity.commons_search) if entity and entity.commons_search else [topic]
    categories = list(entity.commons_categories) if entity else []
    geography = entity.geography if entity else ""
    prompts = []
    if entity is not None:
        prompts = [entity.canonical_visual or entity.canonical_shape or entity.name]
    client = RequestsClient(timeout=12.0)

    def judge(path: Path, caption: str, kind: str) -> dict[str, Any]:
        against = "narration" if kind == "object" else "site"
        verdict = evaluate_chunk_relevance(
            path,
            caption,
            caption,
            score_against=against,
            topic=topic,
            geography=geography,
            scene_type=kind or "site",
            shot_subject=caption,
            shot_setting=geography,
        )
        return {
            "passed": bool(verdict.get("is_relevant")),
            "contains_text": bool(verdict.get("contains_text"))
            or "contains_text" in str(verdict.get("rejection_reason") or ""),
            "relevance_score": verdict.get("relevance_score"),
            "kind": kind,
            "rejection_reason": verdict.get("rejection_reason"),
        }

    def download(item: Any, folder: Path) -> Path | None:
        data = download_candidate_bytes(item, client)
        if not data:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"real_{len(list(folder.glob('real_*'))):02d}.jpg"
        path.write_bytes(data)
        return path

    def nano(prompt: str, path: Path) -> Path | None:
        from modules.reel_artefact.nanobanana import generate_artefact_image

        generate_artefact_image(prompt, path)
        return path if path.is_file() else None

    try:
        return build_asset_pool(
            queries=queries,
            categories=categories,
            dest=dest,
            search=lambda query: _commons_search(query, client),
            category=lambda name: _commons_category(name, client),
            subcategories=lambda name: commons_subcategories(name, client),
            openverse=lambda query: _openverse_search(query, client),
            download=download,
            judge=judge,
            nano=nano,
            object_prompts=prompts,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ASSET POOL skipped (%s)", type(exc).__name__)
        return []
