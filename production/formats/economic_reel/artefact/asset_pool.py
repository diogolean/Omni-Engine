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
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from production.formats.economic_reel.artefact.budget import nano_cap

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
    r"(?:"
    r"\b(?:jars?|batter(?:y|ies)|vessels?|artefacts?|artifacts?|mechanisms?|"
    r"gears?|pendants?|figurines?|statues?|pottery|relics?|"
    r"electrodes?|cylinders?)\b"
    r"|partherjar|elektroden"
    r"|\bpile\s+bagdad\b"
    r"|\biron\s+rods?\b"
    r"|\bcopper\s+cylinders?\b"
    r")",
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
    # Pixel verdict from still_guard: None until checked. False is a sketch,
    # a diagram, or a slide. Never the cover or the hook.
    photo: bool | None = None


class PoolTooSmall(RuntimeError):
    """The photograph pool is still below its minimum after escalation."""


@dataclass
class ShotAssignment:
    sentence: str
    mode: str
    caption: str = ""
    anchor: str = ""
    prompt: str = ""
    asset_key: str = ""
    span: int = 1


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


_DRAWN = re.compile(
    r"\b(illustrations?|diagrams?|maps?|drawings?|schematics?|engravings?|paintings?)\b",
    re.I,
)
_TEXT_PANEL = re.compile(r"\b(text panels?|slides?|posters?|title cards?)\b", re.I)
_OBJECT_NOUN = re.compile(
    r"(?:"
    r"\b(?:jars?|vessels?|pottery|batter(?:y|ies)|cylinders?|electrodes?|"
    r"copper|iron\s+rods?|relics?|artefacts?|artifacts?)\b"
    r"|partherjar|elektroden|\bpile\s+bagdad\b"
    r")",
    re.I,
)
_NOT_OBJECT_PHOTO = re.compile(
    r"\b(?:illustrations?|diagrams?|maps?|drawings?|schematics?|engravings?|"
    r"paintings?|cgi|3d\s+renders?|cartoons?|text\s+panels?|slides?|posters?|"
    r"title\s+cards?|pyramids?|sphinx|moai|sahara|scuba|airplanes?|"
    r"libraries|library|bookshelves?|car\s+batter(?:y|ies)|smartphones?|"
    r"clock\s+faces?|wristwatches?)\b",
    re.I,
)
_NEGATED = re.compile(r"\b(?:no|not|without|never)\s+$", re.I)


def _blob(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("rejection_reason", "image_description", "description", "caption")
    )


def _true_drawing(row: dict[str, Any]) -> bool:
    """An illustration, diagram, or map. A museum photograph is not one of these."""
    return _DRAWN.search(_blob(row)) is not None


def _text_panel(row: dict[str, Any]) -> bool:
    return _TEXT_PANEL.search(_blob(row)) is not None


def _bad_photo(text: str) -> bool:
    blob = text or ""
    for match in _NOT_OBJECT_PHOTO.finditer(blob):
        prefix = blob[max(0, match.start() - 16):match.start()]
        if _NEGATED.search(prefix):
            continue
        return True
    return False


def museum_object_photo(text: str) -> bool:
    """A photograph of the artefact, including one with a museum label."""
    blob = text or ""
    if _bad_photo(blob):
        return False
    return _OBJECT_NOUN.search(blob) is not None


def accept_verdict(verdict: dict[str, Any] | None, *, artefact: bool = False) -> bool:
    """One judge pass. A real museum photo of the object can pass with a label.

    A low score is ignored when the only complaints are a label, a scale bar,
    or a museum-display look. An illustration, diagram, map, or text panel
    still fails, including on an object beat.
    """
    row = verdict or {}
    reason = str(row.get("rejection_reason") or "")
    text_hit = bool(row.get("contains_text")) or "contains_text" in reason
    drawn = bool(row.get("non_photoreal")) or "non_photoreal" in reason
    kind = str(row.get("kind") or "")
    museum = bool(artefact and kind == "object" and museum_object_photo(_blob(row)))
    waived = False
    if artefact and kind == "object" and (text_hit or drawn or museum):
        if _true_drawing(row) or _text_panel(row):
            return False
        text_hit = False
        drawn = False
        waived = True
    if text_hit or drawn:
        return False
    hard = ("anatomy", "shape_mismatch") if museum else (
        "anachronism", "anatomy", "setting_contradicts", "shape_mismatch",
    )
    if any(item in reason for item in hard):
        return False
    score = row.get("relevance_score", row.get("score"))
    if not waived and score is not None and float(score) < _SCORE_FLOOR:
        return False
    if waived:
        return True
    return bool(row.get("passed") or row.get("is_relevant"))


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{4,}", (text or "").lower())
        if token not in _STOP
    }


def _overlap(sentence: str, caption: str) -> int:
    return len(_tokens(sentence) & _tokens(caption))


# Words that describe any building or photo. A line that only shares these
# with a caption has not named the place in the picture.
_GENERIC_SITE = frozenset({
    "arch", "arches", "archway", "wall", "walls", "decoration", "decorations",
    "decorative", "pattern", "patterns", "ruin", "ruins", "remains", "site",
    "sites", "temple", "temples", "palace", "palaces", "building", "buildings",
    "tower", "towers", "minaret", "minarets", "dome", "domes", "gate", "gates",
    "city", "cities", "town", "view", "views", "ancient", "century", "centuries",
    "stone", "stones", "brick", "bricks", "mudbrick", "mosque", "tomb", "tombs",
    "shrine", "shrines", "street", "market", "region", "area", "today",
    "stands", "standing", "great", "large", "old", "famous", "museum", "jpg",
    "jpeg", "png", "iraq", "iran", "mesopotamia",
})


def _name_tokens(text: str) -> set[str]:
    return {
        token for token in _tokens(text)
        if token not in _GENERIC_SITE and not token.isdigit()
    }


def _names_site(sentence: str, asset: PoolAsset) -> int:
    """How many place names the line shares with the photo's caption."""
    return len(_name_tokens(sentence) & _name_tokens(asset.caption))


_DRAWING = re.compile(
    r"(?:innersystem|inner\s*system|elektroden|cut-?away|cross-?section|"
    r"\b(?:diagrams?|sketches?|illustrations?|schematics?|drawings?)\b)",
    re.I,
)
_GALLERY = re.compile(
    r"\b(?:museums?|galleries|gallery|exhibition|exhibits?|pergamon|"
    r"display\s+cases?|visitors?)\b",
    re.I,
)
_OUTDOORS = re.compile(
    r"\b(?:soil|soils|desert|deserts|site|sites|ruins|landscape|landscapes|"
    r"sand|excavations?|fields?)\b",
    re.I,
)
_JAR_PHOTO = re.compile(
    r"(?:pile\s+bagdad|ironie\s+pile|parthian\s+jar\s+copper)",
    re.I,
)


def _asset_blob(asset: PoolAsset) -> str:
    return " ".join((asset.caption or "", asset.path or "", asset.key or ""))


def is_drawing(asset: PoolAsset) -> bool:
    """A cutaway, diagram, or sketch. A labelled jar photograph is not one."""
    return _DRAWING.search(_asset_blob(asset)) is not None


def _jar_photo(asset: PoolAsset) -> bool:
    if is_drawing(asset):
        return False
    return _JAR_PHOTO.search(_asset_blob(asset)) is not None


def _gallery_outdoors(sentence: str, asset: PoolAsset) -> bool:
    """A gallery interior is a poor picture of soil, desert, or the site."""
    if not _OUTDOORS.search(sentence or ""):
        return False
    return _GALLERY.search(_asset_blob(asset)) is not None


def is_photo(asset: PoolAsset) -> bool:
    """The pixel check when it ran, else the caption guess."""
    if asset.photo is not None:
        return bool(asset.photo)
    return not is_drawing(asset)


def _asset_rank(sentence: str, asset: PoolAsset, *, want_object: bool) -> tuple:
    photo = 1 if is_photo(asset) else 0
    jar = 1 if want_object and _jar_photo(asset) else 0
    object_hit = 1 if want_object and asset.kind == "object" and photo else 0
    outdoors = 0 if _gallery_outdoors(sentence, asset) else 1
    site_hit = 1 if (not want_object) and asset.kind == "site" and photo else 0
    # A line that names the place takes the photo whose caption names it,
    # and a curated cache photo before a search hit.
    named = _names_site(sentence, asset) if asset.kind == "site" else 0
    curated = 1 if (asset.source or "") == "cache" else 0
    return (
        photo, jar, object_hit, outdoors, named, site_hit, curated,
        _overlap(sentence, asset.caption),
    )


def _ranked_assets(
    sentence: str,
    assets: list[PoolAsset],
    *,
    want_object: bool,
) -> list[PoolAsset]:
    return sorted(
        assets,
        key=lambda asset: _asset_rank(sentence, asset, want_object=want_object),
        reverse=True,
    )


def best_asset(sentence: str, assets: list[PoolAsset]) -> PoolAsset | None:
    if not assets:
        return None
    want_object = bool(_OBJECT.search(sentence or ""))
    return _ranked_assets(sentence, assets, want_object=want_object)[0]


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


MAX_SOURCE_USES = 1
MAX_MERGED_SPAN = 3


def max_source_uses() -> int:
    """One photograph, one beat. REEL_SOURCE_MAX_USES=2 restores two crops."""
    import os

    try:
        return max(1, int((os.getenv("REEL_SOURCE_MAX_USES") or "").strip() or MAX_SOURCE_USES))
    except ValueError:
        return MAX_SOURCE_USES


def _kind_for(asset_key: str, assets: list[PoolAsset]) -> str:
    for asset in assets:
        if asset.key == asset_key:
            return asset.kind
    return ""


def _legal_asset(
    asset: PoolAsset,
    *,
    kind: str,
    counts: dict[str, int],
    previous_key: str,
    anchors_used: dict[str, set[str]],
) -> bool:
    if kind and asset.kind != kind:
        return False
    if counts.get(asset.key, 0) >= max_source_uses():
        return False
    if asset.key == previous_key:
        return False
    if len(anchors_used.get(asset.key, set())) >= len(_ANCHORS):
        return False
    return True


def _fresh_anchor(asset_key: str, anchors_used: dict[str, set[str]]) -> str:
    used = anchors_used.get(asset_key, set())
    for name in _ANCHORS:
        if name not in used:
            return name
    return ""


@dataclass
class _ShotState:
    """What a reel has already shown. Shared by the body and the CTA tail."""

    counts: dict[str, int]
    anchors_used: dict[str, set[str]]
    previous_key: str = ""
    placed: int = 0

    @classmethod
    def fresh(cls) -> "_ShotState":
        return cls(counts={}, anchors_used={})


def _object_topic(assets: list[PoolAsset]) -> bool:
    """An artefact story (the jar), not a site story (the enclosures)."""
    return sum(1 for asset in assets if asset.kind == "object") >= OBJECT_FLOOR


def _on_topic(sentence: str, asset: PoolAsset, *, object_topic: bool) -> bool:
    """On an artefact story a site photograph must be named by its line.

    The Baghdad Battery pool carries Ctesiphon, a minaret and a museum hall;
    without this they fill every line that does not mention the jar. Sharing
    a word like "wall" or "arch" is not naming the site (s23b: a minaret for
    "Wall decorations from that era").
    """
    if not object_topic or asset.kind == "object":
        return True
    return _names_site(sentence, asset) > 0


def assign_shots(
    sentences: list[str],
    assets: list[PoolAsset],
    *,
    geography: str = "",
    flux_max: int = FLUX_MAX,
    state: _ShotState | None = None,
) -> list[ShotAssignment]:
    """Real photographs for objects and the site. Flux only for the sky.

    One source is used once per reel (``REEL_SOURCE_MAX_USES``), never on
    back-to-back beats. The first beat is the cover and must be a photograph.
    A sentence that cannot take a new source is merged into the previous
    beat; after ``MAX_MERGED_SPAN`` lines only an atmosphere line gets a
    Flux landscape, any other line keeps holding the previous photo.
    """
    from production.formats.economic_reel.artefact.flux_policy import safe_flux_prompt

    shot_state = state if state is not None else _ShotState.fresh()
    counts = shot_state.counts
    anchors_used = shot_state.anchors_used
    object_topic = _object_topic(assets)
    flux_used = 0
    assigned: list[ShotAssignment] = []

    def place(asset: PoolAsset, text: str) -> None:
        anchor = _fresh_anchor(asset.key, anchors_used)
        anchors_used.setdefault(asset.key, set()).add(anchor)
        counts[asset.key] = counts.get(asset.key, 0) + 1
        shot_state.previous_key = asset.key
        shot_state.placed += 1
        assigned.append(ShotAssignment(
            sentence=text,
            mode="asset",
            caption=asset.caption,
            anchor=anchor,
            asset_key=asset.key,
        ))

    def flux_shot(text: str) -> None:
        nonlocal flux_used
        flux_used += 1
        shot_state.previous_key = ""
        shot_state.placed += 1
        assigned.append(ShotAssignment(
            sentence=text,
            mode="flux",
            prompt=safe_flux_prompt(text, geography=geography, scene="landscape"),
        ))

    def merge(text: str) -> None:
        if not assigned:
            if flux_used < int(flux_max) and geography:
                flux_shot(text)
                return
            assigned.append(ShotAssignment(sentence=text, mode="fallback"))
            return
        previous = assigned[-1]
        if (
            int(previous.span or 1) >= MAX_MERGED_SPAN
            and wants_flux(text)
            and flux_used < int(flux_max)
        ):
            flux_shot(text)
            return
        # Past the span limit a non-atmosphere line still holds the previous
        # photo: a longer hold beats a landscape or a site the line never
        # names (s23c B #4, #7, #10, #11).
        previous.sentence = f"{previous.sentence} {text}".strip()
        previous.span = int(previous.span or 1) + 1

    def legal(asset: PoolAsset, kind: str, text: str, cover: bool) -> bool:
        if cover and not is_photo(asset):
            return False
        if not _on_topic(text, asset, object_topic=object_topic):
            return False
        return _legal_asset(
            asset,
            kind=kind,
            counts=counts,
            previous_key=shot_state.previous_key,
            anchors_used=anchors_used,
        )

    for sentence in sentences:
        text = str(sentence or "").strip()
        cover = shot_state.placed == 0
        names_pool_site = any(
            asset.kind == "site" and _names_site(text, asset) > 0 for asset in assets
        )
        if (
            not cover
            and not names_pool_site
            and wants_flux(text)
            and flux_used < int(flux_max)
        ):
            flux_shot(text)
            continue
        want_object = bool(_OBJECT.search(text))
        ranked = _ranked_assets(text, assets, want_object=want_object)
        kind = "object" if want_object else ""
        chosen = next((a for a in ranked if legal(a, kind, text, cover)), None)
        if chosen is None and want_object and assigned and not cover:
            previous = assigned[-1]
            if _kind_for(previous.asset_key, assets) == "object":
                merge(text)
                continue
        if chosen is None:
            chosen = next((a for a in ranked if legal(a, "", text, cover)), None)
        if chosen is None and cover:
            # The hook still needs a photograph: any unused one beats a sketch.
            chosen = next((
                a for a in ranked
                if is_photo(a) and _legal_asset(
                    a, kind="", counts=counts,
                    previous_key=shot_state.previous_key, anchors_used=anchors_used,
                )
            ), None)
        if chosen is None:
            merge(text)
            continue
        if want_object and chosen.kind != "object" and not cover:
            merge(text)
            continue
        place(chosen, text)
    return assigned


def fit_pool_beats(
    snippets: list[str],
    durations: list[float],
    assets: list[PoolAsset],
    *,
    geography: str = "",
    flux_max: int = FLUX_MAX,
    protect_tail: int = 0,
) -> tuple[list[str], list[float], list[ShotAssignment]]:
    """Merge beats the pool cannot cover, and keep the closing CTA beat."""
    protect = max(0, int(protect_tail))
    sentences = [str(item or "") for item in snippets]
    durs = [float(item) for item in durations]
    if len(durs) < len(sentences):
        pad = durs[-1] if durs else 1.0
        durs = (durs + [pad] * len(sentences))[: len(sentences)]
    if protect and len(sentences) > protect:
        head, tail = sentences[:-protect], sentences[-protect:]
        head_d, tail_d = durs[:-protect], durs[-protect:]
    else:
        head, tail = sentences, []
        head_d, tail_d = durs, []
    state = _ShotState.fresh()
    shots = assign_shots(head, assets, geography=geography, flux_max=flux_max, state=state)
    merged_s: list[str] = []
    merged_d: list[float] = []
    cursor = 0
    for shot in shots:
        span = max(1, int(shot.span or 1))
        chunk = head_d[cursor:cursor + span]
        merged_s.append(shot.sentence)
        merged_d.append(sum(chunk) if chunk else 0.0)
        cursor += span
    if cursor < len(head_d) and merged_d:
        merged_d[-1] += sum(head_d[cursor:])
    flux_left = max(0, int(flux_max) - sum(1 for shot in shots if shot.mode == "flux"))
    tail_shots = (
        assign_shots(tail, assets, geography=geography, flux_max=flux_left, state=state)
        if tail else []
    )
    tail_s: list[str] = []
    tail_out: list[float] = []
    cursor = 0
    for shot in tail_shots:
        span = max(1, int(shot.span or 1))
        chunk = tail_d[cursor:cursor + span]
        tail_s.append(shot.sentence)
        tail_out.append(sum(chunk) if chunk else 0.0)
        cursor += span
    if cursor < len(tail_d) and tail_out:
        tail_out[-1] += sum(tail_d[cursor:])
    return merged_s + tail_s, merged_d + tail_out, shots + tail_shots


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


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def pool_short_reason(
    kept: list[PoolAsset],
    *,
    target_min: int = TARGET_MIN,
    object_floor: int = OBJECT_FLOOR,
) -> str:
    objects = sum(1 for item in kept if item.kind == "object")
    if len(kept) < int(target_min):
        return f"pool {len(kept)} < {int(target_min)}"
    if objects < int(object_floor):
        return f"objects {objects} < {int(object_floor)}"
    return ""


def nano_failure_note(exc: BaseException) -> str:
    """A short reason for PoolTooSmall. Billing names the provider."""
    text = f"{type(exc).__name__} {exc}"
    low = text.lower()
    billing = ("402", "resource_exhausted", "prepayment", "billing", "depleted")
    if any(needle in low for needle in billing):
        return "nano failed: Nano Banana 402 billing"
    if "getaddrinfo" in low or "connecterror" in low or "nameresolution" in low:
        return "nano failed: Nano Banana DNS"
    return f"nano failed: {text[:160]}"


def _crop_labels(path: Path) -> None:
    """A museum label sits in a band. The object photograph stays."""
    try:
        from PIL import Image

        from production.formats.economic_reel.artefact.crop import crop_label_bands

        with Image.open(path) as image:
            cropped = crop_label_bands(image)
        cropped.save(path, quality=90)
    except Exception as exc:  # noqa: BLE001
        logger.info("ASSET POOL label crop skipped (%s)", type(exc).__name__)


def _credit_rows(cache_dir: Path) -> dict[str, dict[str, Any]]:
    """credits.json rows by lower-case file name."""
    import json

    path = Path(cache_dir) / "credits.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for row in payload.get("photos") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("file") or "").strip().lower()
        if name:
            rows[name] = row
    return rows


def _credit_blurbs(cache_dir: Path) -> dict[str, str]:
    """Title and subject from credits.json, so a cutaway is not just a filename."""
    blurbs: dict[str, str] = {}
    for name, row in _credit_rows(cache_dir).items():
        bits = [row.get("title"), row.get("subject")]
        blurbs[name] = " ".join(str(bit) for bit in bits if bit)
    return blurbs


_REGION_WORDS = frozenset({"iraq", "iran", "mesopotamia", "syria", "turkey", "egypt"})


def view_key(asset: PoolAsset) -> frozenset[str]:
    """What a site photo shows, from its caption: the place and the structure.

    Two different photographs of one facade from a similar viewpoint (the
    Ctesiphon arch as 04, 05 and 07) defeat every pixel check, the ORB crop
    match included, so a site's view is also judged by its caption. Years,
    numbers and the country do not change the view. Curated cache files only.
    """
    if asset.kind != "site" or asset.source != "cache":
        # Search captions are too thin ("Gobekli Tepe") to tell views apart.
        return frozenset()
    view = frozenset(
        token for token in _tokens(asset.caption)
        if not token.isdigit() and token not in _REGION_WORDS
    )
    return view if len(view) >= 2 else frozenset()


def screen_pool(assets: list[PoolAsset]) -> list[PoolAsset]:
    """Pixel checks, no model: drop slides and labelled panels, drop repeats.

    Two files of one photograph (a crop, a re-upload) collapse to the first,
    and so do two site photos of one view (see ``view_key``).
    Every survivor carries its photo-or-drawing verdict for the cover rule.
    """
    from production.formats.economic_reel.artefact import still_guard

    kept: list[PoolAsset] = []
    seen: list[Any] = []
    views: set[frozenset[str]] = set()
    for asset in assets:
        found = still_guard.traits(asset.path)
        if found is None:
            kept.append(asset)
            continue
        if found.text_panel:
            logger.info("ASSET POOL dropped %s (text panel)", Path(asset.path).name)
            continue
        twin = next((prior for prior in seen if still_guard.same_picture(found, prior)), None)
        if twin is not None:
            logger.info("ASSET POOL dropped %s (same photograph)", Path(asset.path).name)
            continue
        view = view_key(asset)
        if view and view in views:
            logger.info("ASSET POOL dropped %s (same view)", Path(asset.path).name)
            continue
        if view:
            views.add(view)
        seen.append(found)
        asset.photo = bool(found.photo)
        kept.append(asset)
    return kept


def _seed_cache(cache_dir: Path | None, folder: Path) -> list[PoolAsset]:
    """Curated artefact_cache files count as object photographs.

    A credits.json row may set ``"kind": "site"`` for a context shot, or
    ``"exclude": true`` to keep a file on disk but out of every reel.
    """
    if cache_dir is None or not Path(cache_dir).is_dir():
        return []
    rows = _credit_rows(Path(cache_dir))
    blurbs = _credit_blurbs(Path(cache_dir))
    seeded: list[PoolAsset] = []
    for path in sorted(Path(cache_dir).iterdir()):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        row = rows.get(path.name.lower()) or {}
        if row.get("exclude"):
            continue
        kind = "site" if str(row.get("kind") or "").strip().lower() == "site" else "object"
        dest = folder / f"cache_{path.name}"
        if not dest.is_file():
            dest.write_bytes(path.read_bytes())
        caption = path.stem.replace("_", " ")
        extra = blurbs.get(path.name.lower(), "")
        if extra:
            caption = f"{caption}. {extra}"
        seeded.append(PoolAsset(
            path=str(dest),
            caption=caption,
            kind=kind,
            source="cache",
            key=f"cache:{path.name.lower()}",
        ))
    if seeded:
        logger.info("ASSET POOL cache seed | n=%d | dir=%s", len(seeded), cache_dir)
    return seeded


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
    cache_dir: Path | None = None,
    cache_dirs: list[Path] | None = None,
    strict: bool = False,
) -> list[PoolAsset]:
    """Download and judge each candidate once. Stop at the usable target.

    A short pool is filled from the artefact cache, then one cheap Nano
    Banana pass. ``strict`` raises when the pool is still too small.
    """
    from production.formats.economic_reel.artefact.fetch import ancient_title_reject
    from production.formats.economic_reel.artefact.nanobanana import fatal_nano_error

    folder = Path(dest)
    folder.mkdir(parents=True, exist_ok=True)
    seed_dirs: list[Path] = []
    if cache_dir is not None:
        seed_dirs.append(Path(cache_dir))
    for extra in cache_dirs or []:
        if extra is not None and Path(extra) not in seed_dirs:
            seed_dirs.append(Path(extra))
    kept: list[PoolAsset] = []
    seen_seed: set[str] = set()
    for seed in seed_dirs:
        for asset in _seed_cache(seed, folder):
            if asset.key in seen_seed:
                continue
            seen_seed.add(asset.key)
            kept.append(asset)
    kept = screen_pool(kept)
    commons: list[Any] = []
    seen: set[str] = {item.key for item in kept}

    def take(items: list[Any]) -> None:
        for item in items or []:
            caption = _candidate_caption(item)
            categories_text = " ".join(
                str(part) for part in (getattr(item, "categories", None) or [])
            )
            reason = ancient_title_reject(caption, categories_text)
            if reason:
                logger.info("ASSET POOL dropped %s (%s)", caption[:80], reason)
                continue
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

    room = max(0, int(target_max) - len(kept))
    if room:
        kept.extend(_judge_candidates(
            commons, folder, download, judge, source="commons", limit=room,
        ))
    if len(kept) < int(openverse_floor):
        extra: list[Any] = []
        for query in [item for item in queries if str(item).strip()][:3]:
            for item in openverse(query) or []:
                caption = _candidate_caption(item)
                categories_text = " ".join(
                    str(part) for part in (getattr(item, "categories", None) or [])
                )
                reason = ancient_title_reject(caption, categories_text)
                if reason:
                    logger.info("ASSET POOL dropped %s (%s)", caption[:80], reason)
                    continue
                key = _candidate_key(item)
                if key and key not in seen:
                    seen.add(key)
                    extra.append(item)
        room = int(target_max) - len(kept)
        if room > 0 and extra:
            kept.extend(_judge_candidates(
                extra, folder, download, judge, source="openverse", limit=room,
            ))
    kept = screen_pool(kept)
    objects = sum(1 for item in kept if item.kind == "object")
    prompts = [item for item in (object_prompts or []) if str(item).strip()] or ["the object"]
    calls = 0
    nano_dead = False
    nano_note = ""
    while (
        objects < int(object_floor)
        and calls < int(nano_max)
        and nano is not None
        and not nano_dead
    ):
        prompt = prompts[calls % len(prompts)]
        calls += 1
        path = folder / f"nano_{calls:02d}.jpg"
        try:
            written = nano(prompt, path)
        except Exception as exc:  # noqa: BLE001
            logger.info("ASSET POOL nano failed (%s)", type(exc).__name__)
            if fatal_nano_error(exc) or "nanobanana_failed" in str(exc).lower():
                nano_dead = True
                nano_note = nano_failure_note(exc)
            continue
        if written is None or not Path(written).is_file():
            continue
        verdict = judge(Path(written), prompt, "object") or {}
        verdict.setdefault("kind", "object")
        if not accept_verdict(verdict, artefact=True):
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
    if strict:
        reason = pool_short_reason(
            kept, target_min=int(target_min), object_floor=int(object_floor),
        )
        if reason:
            detail = f"asset pool too small ({reason}) after cache and search"
            if nano_note:
                detail = f"{detail}; {nano_note}"
            raise PoolTooSmall(detail)
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
        verdict.setdefault("kind", kind)
        if not accept_verdict(verdict, artefact=(kind == "object")):
            continue
        reason = str(verdict.get("rejection_reason") or "")
        if kind == "object" and (verdict.get("contains_text") or "contains_text" in reason):
            _crop_labels(Path(path))
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

    from production.formats.economic_reel.artefact.crop import prepare_reel_frame

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
    chosen = _asset_by_key(pool, assignment.asset_key)
    if chosen is not None:
        claim_key(chosen.key)
    else:
        chosen = _unused_asset(assignment.sentence, pool)
    if chosen is None:
        if pool:
            logger.warning(
                "ASSET POOL | beat %d fallback found no unused photograph", int(beat_index) + 1,
            )
        return StillGateResult(
            path=None,
            extra_generations=0,
            publishable=False,
            blocked_reason="pool_exhausted" if pool else "empty_pool",
        )
    anchor = assignment.anchor or pick_anchor(chosen.key, None, {})
    if assignment.mode == "flux":
        anchor = pick_anchor(chosen.key, (chosen.key, anchor), {(chosen.key, anchor): 1})
    write_asset_crop(chosen.path, target, anchor)
    provider = "fallback" if assignment.mode == "flux" else (assignment.mode or "asset")
    _write_pool_source(target, chosen, provider=provider)
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


_CLAIM_LOCK = threading.Lock()
_PLANNED: set[str] = set()
_CLAIMED: set[str] = set()


def reset_claims() -> None:
    """Forget the photographs claimed by the last reel. Call at reel start."""
    with _CLAIM_LOCK:
        _PLANNED.clear()
        _CLAIMED.clear()


def claim_key(key: str) -> None:
    if key:
        with _CLAIM_LOCK:
            _CLAIMED.add(key)


def _unused_asset(sentence: str, pool: list[PoolAsset]) -> PoolAsset | None:
    """The best pool photograph no other beat has planned or used.

    Pool jobs run in parallel, so the pick and the claim share one lock.
    """
    object_topic = _object_topic(pool)
    with _CLAIM_LOCK:
        taken = _PLANNED | _CLAIMED
        free = [asset for asset in pool if asset.key not in taken]
        # On an artefact story a generic line takes an unused object photo,
        # not a site the line never names (s23b B #5 and #9: Ctesiphon).
        fitting = [
            asset for asset in free
            if _on_topic(sentence, asset, object_topic=object_topic)
        ]
        want_object = object_topic or bool(_OBJECT.search(sentence or ""))
        # No fitting photo left: report pool_exhausted rather than show an
        # unnamed site (s23c B #4 and #10 came through this fallback).
        ranked = _ranked_assets(sentence, fitting, want_object=want_object)
        chosen = ranked[0] if ranked else None
        if chosen is not None:
            _CLAIMED.add(chosen.key)
        return chosen


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

    if assignment.asset_key:
        with _CLAIM_LOCK:
            _PLANNED.add(assignment.asset_key)
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


def _write_pool_source(path: Path, asset: PoolAsset, *, provider: str) -> None:
    """Sidecar so the cost line names the photograph's source, not Flux."""
    from production.formats.economic_reel.artefact.source import write_source_sidecar

    source = (asset.source or "").strip().lower()
    if source == "nano":
        billed = "nanobanana"
    elif source in {"commons", "openverse", "cache", "met", "wikimedia", "europeana"}:
        billed = source
    elif provider == "fallback":
        billed = "commons"
    else:
        billed = source or "commons"
    write_source_sidecar(path, {
        "provider": billed,
        "model_id": "",
        "source": source,
        "pool_key": asset.key,
    })


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
    from production.formats.economic_reel.artefact.fetch import (
        RequestsClient,
        _commons_category,
        _commons_search,
        _openverse_search,
        commons_subcategories,
        download_candidate_bytes,
    )
    from production.formats.economic_reel.artefact.preview import resolve_entity
    from modules.reel_qa.relevance import evaluate_chunk_relevance
    from production.formats.economic_reel.shot_planner.entities import entity_for_line, load_entities

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
        reason = str(verdict.get("rejection_reason") or "")
        return {
            "passed": bool(verdict.get("is_relevant")),
            "contains_text": bool(verdict.get("contains_text")) or "contains_text" in reason,
            "non_photoreal": bool(verdict.get("non_photoreal")) or "non_photoreal" in reason,
            "image_description": str(verdict.get("image_description") or ""),
            "relevance_score": verdict.get("relevance_score"),
            "kind": kind,
            "rejection_reason": reason,
            "caption": caption,
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
        from production.formats.economic_reel.artefact.nanobanana import generate_artefact_image

        generate_artefact_image(prompt, path)
        return path if path.is_file() else None

    cache_dirs: list[Path] = []
    if entity is not None and getattr(entity, "id", ""):
        from production.formats.economic_reel.artefact.fetch import cache_seed_dirs

        cache_dirs = cache_seed_dirs(channel, str(entity.id))
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
            nano=nano if nano_cap() > 0 else None,
            object_prompts=prompts,
            nano_max=min(NANO_MAX, nano_cap()),
            cache_dirs=cache_dirs,
            strict=True,
        )
    except PoolTooSmall:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("ASSET POOL skipped (%s)", type(exc).__name__)
        return []


def unique_stills(
    paths: list[Path],
    pool: list[PoolAsset],
    *,
    flux_generate: Callable[..., Any] | None = None,
    geography: str = "",
    flux_max: int = FLUX_MAX,
) -> tuple[list[Path], list[int]]:
    """Last pass before export: one picture once per reel, a photo for a cover.

    A later copy of a picture already on screen (md5, pHash within
    ``REEL_NEAR_DUP_BITS``, the same padded-or-cropped core, an ORB crop
    match, or the same pool key) is replaced by an unused pool photograph, then a
    Flux landscape. A cover that is a sketch or a labelled panel is replaced
    the same way. Returns the new paths and the beats left unresolved.
    """
    from production.formats.economic_reel.artefact import still_guard
    from production.formats.economic_reel.artefact.flux_policy import safe_flux_prompt

    out = [Path(item) for item in paths]
    if not out:
        return out, []
    flux_left = [max(0, int(flux_max))]

    def fresh_for(index: int, used: list[Any]) -> Path | None:
        target = out[index].with_name(f"{out[index].stem}_unique{out[index].suffix or '.jpg'}")
        for asset in sorted(pool, key=lambda a: 0 if is_photo(a) else 1):
            if index == 0 and not is_photo(asset):
                continue
            found = still_guard.with_source_key(still_guard.traits(asset.path), asset.key)
            if found is None or found.text_panel:
                continue
            if any(still_guard.same_picture(found, other) for other in used):
                continue
            written = write_asset_crop(asset.path, target, "center")
            _write_pool_source(written, asset, provider="asset")
            crop = still_guard.traits(written)
            if crop is not None and not any(still_guard.same_picture(crop, o) for o in used):
                claim_key(asset.key)
                return written
        if flux_generate is not None and flux_left[0] > 0 and index > 0:
            flux_left[0] -= 1
            shot = ShotAssignment(
                sentence="",
                mode="flux",
                prompt=safe_flux_prompt("", geography=geography, scene="landscape"),
            )
            try:
                written = _call_flux(flux_generate, shot.prompt, target)
            except Exception as exc:  # noqa: BLE001
                logger.info("REPEAT GUARD | flux failed (%s)", type(exc).__name__)
                return None
            return written if Path(written).is_file() else None
        return None

    cover = still_guard.traits(out[0])
    if cover is not None and (cover.text_panel or not cover.photo):
        others = [still_guard.traits(item) for item in out[1:]]
        swap = fresh_for(0, [item for item in others if item is not None])
        if swap is not None:
            logger.info("COVER GUARD | cover was not a photograph; replaced (%s)", swap.name)
            out[0] = swap
        else:
            logger.warning("COVER GUARD | no unused photograph for the cover")
    return still_guard.enforce_unique(out, fresh_for)

