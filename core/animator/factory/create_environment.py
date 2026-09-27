"""Generate shared 16:9 reverse-angle environments for the animator."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from utils.pipeline_paths import assets_root, outputs_root

from .create_puppet import generate_character_image

PANORAMA_SIZE = (3840, 2160)
# Gemini image tiers accept these ratios. The plate is then cover-cropped
# uniformly onto the locked pixel size, so neither axis is stretched.
_GENERATION_ASPECTS: tuple[tuple[str, float], ...] = (
    ("1:1", 1.0),
    ("5:4", 1.25),
    ("4:5", 0.8),
    ("4:3", 4 / 3),
    ("3:4", 3 / 4),
    ("3:2", 1.5),
    ("2:3", 2 / 3),
    ("16:9", 16 / 9),
    ("9:16", 9 / 16),
    ("21:9", 21 / 9),
)


GHIBLI_SMOOTHNESS = (
    "Ultra-smooth Studio Ghibli animation cel, pristine flat color fills, "
    "clean digital anime painting, razor-sharp clean ink outlines, "
    "flawless smooth gradients, Makoto Shinkai clean lighting, "
    "high-budget 90s anime feature film background art, pristine surfaces."
)
ANTI_GRAIN = (
    "grain, film grain, noise, stippling, pointillism, canvas texture, "
    "rough paper, gritty, dithering, noisy, speckled, dusty, dirty, "
    "over-sharpened, chromatic noise, chalky, sandpaper texture."
)
GHIBLI_VIBRANCY = (
    "Authentic Studio Ghibli background art, Hayao Miyazaki anime cel painting, "
    "crisp dark ink linework, luminous radiant golden sunlight, rich deep contrast, "
    "vibrant warm color grading, polished varnished mahogany, clean cel shading, "
    "high dynamic range, vivid pristine colors."
)
_BANNED_PROMPT_TERMS = (
    "matte texture",
    "vintage",
    "retro",
    "antique",
    "aged",
    "dusty",
    "faded",
    "weathered",
    "sepia",
    "grainy",
)
_REQUIRED_IMAGE_MODEL = "models/gemini-2.5-flash-image"
_SCENE_03_THEME = "aiwake_arena_panorama_v3"
_KEPT_ARENAS: tuple[dict[str, str], ...] = (
    {
        "file": "aiwake_arena_panorama_v2.png",
        "title": "v2  Library",
    },
    {
        "file": "aiwake_arena_panorama_v3.png",
        "title": "v3  Royal Observatory",
    },
)
_NEW_ARENAS: tuple[dict[str, str], ...] = (
    {
        "theme_id": "arena_04_clockwork_foundry",
        "title": "04  A Grande Forja Mecânica",
        "prompt": (
            "A Grande Forja Mecânica. Vaulted brick arches. "
            "A giant brass pendulum frozen at rest and an intricate astronomical orrery, both still. "
            "Left: drafting tables with parchment blueprints. "
            "Right: glowing pressure dials and brass gauges, pistons at rest, no steam plumes. "
            "Empty standing floor. No people, no robots."
        ),
    },
    {
        "theme_id": "arena_05_botanical_conservatory",
        "title": "05  O Jardim de Inverno dos Autômatos",
        "prompt": (
            "O Jardim de Inverno dos Autômatos. Ornate iron-and-glass greenhouse dome. "
            "Brass terrariums and stationary stone statues. "
            "Arched bay windows show a distant silent mountain ridge under a smooth twilight "
            "gradient sky with no cloud forms. "
            "Empty floor between the planters. No people, no robots, no spraying water."
        ),
    },
    {
        "theme_id": "arena_06_royal_circular_vault",
        "title": "06  O Arquivo Secreto dos Filósofos",
        "prompt": (
            "O Arquivo Secreto dos Filósofos. A completely enclosed circular rotunda. "
            "Solid wood coffered dome. Absolutely no windows, no glass panes, no sky, no clouds. "
            "Multi-tier mahogany balconies, spiral staircases, a central illuminated celestial "
            "brass globe, and warm green banker lamps. Polished wood floor. No people, no robots."
        ),
    },
    {
        "theme_id": "arena_07_council_chamber",
        "title": "07  O Salão de Julgamento da Lógica",
        "prompt": (
            "O Salão de Julgamento da Lógica. Baroque wood-paneled walls, rich velvet drapery. "
            "A stained-glass rose window filters deep amber and emerald light onto a herringbone "
            "parquet floor. No open sky. Empty ceremonial hall. No people, no robots."
        ),
    },
    {
        "theme_id": "arena_08_subterranean_relay",
        "title": "08  A Central Subterrânea de Válvulas",
        "prompt": (
            "A Central Subterrânea de Válvulas. Stone-flagged warm industrial archive. "
            "Massive glowing vacuum-tube mainframe banks with warm orange filaments, "
            "copper conduit pipes, and brass teletype stations. "
            "Enclosed underground hall. No smoke, no steam, no water. No people, no robots."
        ),
    },
    {
        "theme_id": "arena_09_sky_armory_hangar",
        "title": "09  O Hangar Real de Zepelins",
        "prompt": (
            "O Hangar Real de Zepelins. A grand dirigible hangar observation deck with towering "
            "arched iron trusses and brass winches. High clerestory windows reveal a distant "
            "still fantasy mountain city of stone under a smooth gradient sky, no cloud forms. "
            "Empty deck. No airships in motion, no people, no robots."
        ),
    },
    {
        "theme_id": "arena_10_celestial_cartography",
        "title": "10  A Sala de Cartografia das Estrelas",
        "prompt": (
            "A Sala de Cartografia das Estrelas. A grand dark-wood map room. "
            "Suspended illuminated armillary spheres, brass globes, illuminated glass star-charts, "
            "and soft amber lantern light. Enclosed interior, no open sky, no clouds. "
            "Empty floor. No people, no robots."
        ),
    },
)
_FINAL_ARENAS: tuple[dict[str, str], ...] = (
    {
        "theme_id": "arena_11_alchemical_apothecary",
        "title": "11  O Laboratório de Alquimia Mecânica",
        "prompt": (
            "O Laboratório de Alquimia Mecânica. Vaulted stone walls, brass alembics, "
            "glowing emerald and amber liquid held still inside glass flasks, "
            "dark oak apothecary cabinets with hundreds of small drawer fronts and blank brass plates, "
            "stained-glass clerestory windows. No readable letters. No people, no robots."
        ),
    },
    {
        "theme_id": "arena_12_clock_tower_interior",
        "title": "12  O Interior do Grande Relógio da Torre",
        "prompt": (
            "O Interior do Grande Relógio da Torre. Inside the face of a massive gothic clock tower. "
            "Giant translucent frosted-glass clock dials backlit by golden sunset, "
            "massive interlocking brass gears locked still, copper chains at rest, parquet flooring. "
            "No moving parts, no clouds. No people, no robots."
        ),
    },
    {
        "theme_id": "arena_13_grand_greenhouse_atrium",
        "title": "13  O Átrio Vitoriano de Ferro Fundido",
        "prompt": (
            "O Átrio Vitoriano de Ferro Fundido. ONE continuous cast-iron conservatory seen from a single viewpoint. "
            "Do not mirror the room. Do not split the image. No vertical seam, no gap, no duplicated halves. "
            "Arched iron lattices, brass terrariums, one central aisle, stone basins holding still water, no falling water. "
            "Geometric glass panes show a distant silent mountain ridge under a smooth twilight "
            "gradient with no cloud forms. No people, no robots."
        ),
    },
    {
        "theme_id": "arena_14_telegraphic_exchange",
        "title": "14  A Central Telegráfica das Nações",
        "prompt": (
            "A Central Telegráfica das Nações. An enclosed dispatch hall. "
            "Copper switchboards with brass patch cords, glowing rotary dials, acoustic speaking tubes, "
            "polished mahogany dispatch desks with green banker lamps, warm incandescent globe chandeliers. "
            "No windows to the sky. No people, no robots, no readable text."
        ),
    },
    {
        "theme_id": "arena_15_chamber_of_reason",
        "title": "15  A Câmara dos Filósofos",
        "prompt": (
            "A Câmara dos Filósofos. A colonnaded dark walnut hall. "
            "Twin empty debate lecterns of carved oak and brass, high arched stained-glass crests, "
            "rich tapestry drapery, herringbone wood floor with a central inlaid compass rose. "
            "No open sky. No people, no robots, no readable letters."
        ),
    },
)
_SCENE_03_PROMPT = (
    "A grand panoramic observation bridge and clockwork library inside a sky cruiser, "
    "one continuous shared hall. "
    "Left section: polished mahogany celestial navigation tables, star charts, "
    "brass astrolabes, and green banker lamps. "
    "Center vista: grand arched bay windows filled with Art Nouveau Gothic stained glass "
    "in warm gold, amber, and olive, filtering stationary sunset light across "
    "the polished wooden floor. "
    "Right section: pressure consoles, glowing brass gauges, copper tubes, "
    "and warm amber vacuum tubes. "
    "Completely clean empty arena. No human figures. No robot figures."
)


def sanitize_environment_prompt(prompt: str) -> str:
    """Drop vocabulary that pushes the painter toward a faded, dusty plate."""
    cleaned = prompt
    for term in _BANNED_PROMPT_TERMS:
        cleaned = re.sub(rf"\b{re.escape(term)}\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    return cleaned.strip()


STATIC_SCENE_RULES = (
    "MANDATORY NO-MOTION AESTHETICS: "
    "NEVER include fluid elements that imply real-time motion (STRICTLY FORBIDDEN: rolling ocean waves, "
    "close-up fluffy wind-blown clouds, active waterfalls, and chimney smoke plumes). "
    "All exterior window vistas MUST be solid and stationary: intricate Art Nouveau / Gothic stained glass "
    "(vitrais) filtering warm light, distant stone cityscapes/citadels, silent mountain ridge silhouettes at dusk, "
    "or enclosed grand architectural interiors."
)


def environment_prompt(prompt: str) -> str:
    """Shared plate brief. Vibrancy tokens and STATIC_SCENE_RULES always apply."""
    cleaned = sanitize_environment_prompt(prompt)
    return (
        f"{GHIBLI_SMOOTHNESS} "
        f"{GHIBLI_VIBRANCY} "
        "Empty architectural environment plate for a shot-reverse-shot "
        "dialogue. No people, no characters, no robots, no text. "
        "One continuous room with a consistent horizon, generous central "
        "depth, and matching architecture across both halves. "
        f"{cleaned} "
        f"{STATIC_SCENE_RULES} "
        "Negative prompt: "
        f"{ANTI_GRAIN}"
    )


def generation_aspect_for_size(size: tuple[int, int]) -> str:
    """Nearest legal ratio. A tie prefers a vertical crop so both sides survive."""
    target = size[0] / size[1]

    def penalty(ratio: float) -> tuple[float, int]:
        if ratio >= target:
            return (1.0 - (target / ratio) + 0.02, 1)
        return (1.0 - (ratio / target), 0)

    return min(_GENERATION_ASPECTS, key=lambda item: penalty(item[1]))[0]


def _safe_theme_id(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if not normalized or any(
        not (character.isalnum() or character == "_")
        for character in normalized
    ):
        raise argparse.ArgumentTypeError(
            "theme id must contain only letters, numbers, underscores, or hyphens"
        )
    return normalized


def apply_reverse_angle_ambience(
    image: Image.Image,
    *,
    size: tuple[int, int] = PANORAMA_SIZE,
) -> Image.Image:
    """Format a panorama with cool-left and warm-right atmospheric grading.

    ``ImageOps.fit`` scales uniformly to cover ``size`` and crops the overflow.
    The two axes always share one scale, so the plate is never stretched.
    """
    panorama = ImageOps.fit(
        image.convert("RGB"),
        size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    pixels = np.asarray(panorama, dtype=np.float32)
    width = pixels.shape[1]
    position = np.linspace(-1.0, 1.0, width, dtype=np.float32)
    cool = np.clip(-position, 0.0, 1.0).reshape(1, width, 1)
    warm = np.clip(position, 0.0, 1.0).reshape(1, width, 1)
    cool_tint = np.asarray((0.94, 1.00, 1.08), dtype=np.float32).reshape(1, 1, 3)
    warm_tint = np.asarray((1.08, 1.01, 0.93), dtype=np.float32).reshape(1, 1, 3)
    graded = pixels * (1.0 + cool * (cool_tint - 1.0))
    graded *= 1.0 + warm * (warm_tint - 1.0)
    return Image.fromarray(np.clip(graded, 0, 255).astype(np.uint8))


def grade_ghibli_panorama(img: Image.Image) -> Image.Image:
    """Smooth flat color, keep ink edges, then lift contrast and saturation."""
    cv_img = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    smooth_bgr = cv2.bilateralFilter(cv_img, d=7, sigmaColor=50, sigmaSpace=50)
    smooth_pil = Image.fromarray(cv2.cvtColor(smooth_bgr, cv2.COLOR_BGR2RGB))
    graded = ImageOps.autocontrast(smooth_pil, cutoff=(0.5, 0.5))
    graded = ImageEnhance.Contrast(graded).enhance(1.14)
    graded = ImageEnhance.Color(graded).enhance(1.10)
    return graded


def create_environment(
    *,
    theme_id: str,
    prompt: str,
    image_path: Path | None = None,
    puppets_dir: Path | None = None,
    output_size: tuple[int, int] | None = None,
    aspect_ratio: str | None = None,
    reference_path: Path | None = None,
) -> dict:
    if theme_id == "aiwake_arena_panorama_v2":
        raise RuntimeError("refusing to overwrite the benchmark panorama")
    root = Path(puppets_dir) if puppets_dir else assets_root() / "puppets"
    destination_dir = root / "shared_backgrounds"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{theme_id}.png"
    raw_fd, raw_name = tempfile.mkstemp(prefix="arena_raw_", suffix=".png")
    os.close(raw_fd)
    source = Path(raw_name)
    size = output_size or PANORAMA_SIZE
    ratio = aspect_ratio or (
        "16:9" if size == PANORAMA_SIZE else generation_aspect_for_size(size)
    )
    model = "local"
    try:
        if image_path is not None:
            with Image.open(Path(image_path).expanduser()) as supplied:
                supplied.convert("RGB").save(source, format="PNG", compress_level=1)
        else:
            if not prompt.strip():
                raise ValueError("--prompt is required when --image-path is omitted")
            model = generate_character_image(
                environment_prompt(prompt),
                source,
                aspect_ratio=ratio,
                reference_path=reference_path,
            )
        with Image.open(source) as generated:
            formatted = grade_ghibli_panorama(
                apply_reverse_angle_ambience(generated, size=size)
            )
        if formatted.size != size:
            raise RuntimeError(f"environment plate is {formatted.size}, expected {size}")
        formatted.save(destination, format="PNG", compress_level=1)
    finally:
        source.unlink(missing_ok=True)
    return {
        "theme_id": theme_id,
        "path": str(destination),
        "source_path": "",
        "size": list(formatted.size),
        "aspect_ratio": ratio,
        "acquisition": model,
    }


def _benchmark_panorama() -> Path:
    return assets_root() / "puppets" / "shared_backgrounds" / "aiwake_arena_panorama_v2.png"


def _scene_theme(scene: str) -> str:
    normalized = _safe_theme_id(scene)
    if normalized in {"scene_03_observatory", "scene_03"}:
        return _SCENE_03_THEME
    return normalized


def run_color_grade_test(*, scene: str, compare: bool) -> dict:
    """Regenerate one plate with the sanitized prompt and the Ghibli grade."""
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
    built = environment_prompt(
        "vintage retro antique aged dusty faded weathered sepia grainy matte texture"
    )
    positive, marker, negative = built.partition("Negative prompt:")
    if not marker or ANTI_GRAIN not in negative:
        raise RuntimeError("anti-grain negative prompt is missing from the template")
    if GHIBLI_SMOOTHNESS not in positive or GHIBLI_VIBRANCY not in positive:
        raise RuntimeError("smoothness or vibrancy tokens are missing from the prompt template")
    leaked = [term for term in _BANNED_PROMPT_TERMS if term in positive.lower()]
    if leaked:
        raise RuntimeError(f"banned prompt terms survived sanitization: {leaked}")
    canonical = _benchmark_panorama()
    before = canonical.read_bytes()
    with Image.open(canonical) as opened:
        locked = opened.size
    theme_id = _scene_theme(scene)
    if theme_id != _SCENE_03_THEME:
        raise RuntimeError(
            f"{scene} does not map to {_SCENE_03_THEME}; the color-grade test revises scene 03"
        )
    prompt = _SCENE_03_PROMPT
    result = create_environment(
        theme_id=theme_id,
        prompt=prompt,
        output_size=locked,
    )
    if tuple(result["size"]) != locked:
        raise RuntimeError(f"{theme_id} is {result['size']}, expected {list(locked)}")
    if canonical.read_bytes() != before:
        raise RuntimeError(f"refusing to alter the benchmark panorama: {canonical}")
    comparison = None
    if compare:
        comparison = _write_benchmark_comparison(Path(result["path"]))
        result["comparison"] = str(comparison)
    print(f"{result['path']} {locked[0]}x{locked[1]}")
    if comparison is not None:
        print(comparison)
    return result


def _write_benchmark_comparison(graded_path: Path) -> Path:
    destination = (
        outputs_root()
        / "aiwake"
        / "_test_harness"
        / "scene_test"
        / "washed_out_fix_comparison.png"
    )
    with Image.open(_benchmark_panorama()) as benchmark, Image.open(graded_path) as graded:
        left = benchmark.convert("RGB")
        right = graded.convert("RGB")
    if left.size != right.size:
        right = ImageOps.fit(right, left.size, method=Image.Resampling.LANCZOS)
    sheet = Image.new("RGB", (left.width + right.width, left.height))
    sheet.paste(left, (0, 0))
    sheet.paste(right, (left.width, 0))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, format="PNG", compress_level=1)
    return destination


def _catalog_font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def official_arena_entries() -> tuple[dict[str, str], ...]:
    """Kept panoramas first, then the seven generated halls."""
    kept = tuple(
        {"file": item["file"], "title": item["title"]} for item in _KEPT_ARENAS
    )
    generated = tuple(
        {"file": f"{item['theme_id']}.png", "title": item["title"]}
        for item in _NEW_ARENAS
    )
    return kept + generated


def batch_generate_arenas(model: str) -> list[Path]:
    """Write arenas 04–10 at the v2 plate size. v2 and v3 stay untouched."""
    from dotenv import load_dotenv

    from .create_puppet import _FAST_IMAGE_MODEL

    if model != _REQUIRED_IMAGE_MODEL or _FAST_IMAGE_MODEL != _REQUIRED_IMAGE_MODEL:
        raise RuntimeError(
            f"arena batch requires {_REQUIRED_IMAGE_MODEL}, got {model} / {_FAST_IMAGE_MODEL}"
        )
    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
    canonical = _benchmark_panorama()
    before = canonical.read_bytes()
    with Image.open(canonical) as opened:
        locked = opened.size
    observatory = canonical.with_name("aiwake_arena_panorama_v3.png")
    observatory_before = observatory.read_bytes()
    written: list[Path] = []
    print(f"image model: {_FAST_IMAGE_MODEL}")
    for scene in _NEW_ARENAS:
        result = create_environment(
            theme_id=scene["theme_id"],
            prompt=scene["prompt"],
            output_size=locked,
        )
        if result["acquisition"] != _REQUIRED_IMAGE_MODEL:
            raise RuntimeError(
                f"{scene['theme_id']} used {result['acquisition']}, expected {_REQUIRED_IMAGE_MODEL}"
            )
        if tuple(result["size"]) != locked:
            raise RuntimeError(
                f"{scene['theme_id']} is {result['size']}, expected {list(locked)}"
            )
        if canonical.read_bytes() != before:
            raise RuntimeError(f"refusing to alter the benchmark panorama: {canonical}")
        if observatory.read_bytes() != observatory_before:
            raise RuntimeError(f"refusing to alter the observatory plate: {observatory}")
        print(f"{result['path']} {locked[0]}x{locked[1]} model={result['acquisition']}")
        written.append(Path(result["path"]))
    return written


def inspect_official_catalog(destination: Path | None = None) -> Path:
    """Contact sheet of the kept panoramas and the seven new arenas."""
    locked_path = _benchmark_panorama()
    with Image.open(locked_path) as opened:
        locked = opened.size
    destination = destination or (
        outputs_root()
        / "aiwake"
        / "_test_harness"
        / "scene_test"
        / "official_10_arenas_catalog.png"
    )
    entries = official_arena_entries()
    columns = 3
    thumb_w = 720
    thumb_h = int(round(locked[1] * (thumb_w / locked[0])))
    label_h = 56
    pad = 16
    rows = (len(entries) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (
            columns * thumb_w + (columns + 1) * pad,
            rows * (thumb_h + label_h) + (rows + 1) * pad,
        ),
        (24, 18, 14),
    )
    draw = ImageDraw.Draw(sheet)
    font = _catalog_font(28)
    root = locked_path.parent
    for index, entry in enumerate(entries):
        path = root / entry["file"]
        with Image.open(path) as opened:
            if opened.size != locked:
                raise RuntimeError(f"{path.name} is {opened.size}, expected {locked}")
            thumb = opened.convert("RGB").resize(
                (thumb_w, thumb_h),
                Image.Resampling.LANCZOS,
            )
        column = index % columns
        row = index // columns
        x = pad + column * (thumb_w + pad)
        y = pad + row * (thumb_h + label_h + pad)
        draw.rectangle((x, y, x + thumb_w, y + label_h), fill=(72, 48, 28))
        draw.text((x + 14, y + 12), entry["title"], fill=(245, 228, 196), font=font)
        sheet.paste(thumb, (x, y + label_h))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, format="PNG", compress_level=1)
    print(f"{destination} {sheet.size[0]}x{sheet.size[1]} plates={len(entries)}")
    return destination


def purge_raw_panorama_sources() -> list[Path]:
    """Remove washed-out raw plates from the shared backgrounds folder."""
    root = assets_root() / "puppets" / "shared_backgrounds"
    removed: list[Path] = []
    if not root.is_dir():
        return removed
    for path in sorted(root.glob("*_source.png")):
        path.unlink()
        removed.append(path)
        print(f"removed raw duplicate: {path.name}")
    return removed


def generate_final_arenas(model: str) -> list[Path]:
    """Write arenas 11–15. Only the graded plate is kept."""
    from dotenv import load_dotenv

    from .create_puppet import _FAST_IMAGE_MODEL

    if model != _REQUIRED_IMAGE_MODEL or _FAST_IMAGE_MODEL != _REQUIRED_IMAGE_MODEL:
        raise RuntimeError(
            f"arena batch requires {_REQUIRED_IMAGE_MODEL}, got {model} / {_FAST_IMAGE_MODEL}"
        )
    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
    purge_raw_panorama_sources()
    canonical = _benchmark_panorama()
    before = canonical.read_bytes()
    with Image.open(canonical) as opened:
        locked = opened.size
    written: list[Path] = []
    print(f"image model: {_FAST_IMAGE_MODEL}")
    for scene in _FINAL_ARENAS:
        result = create_environment(
            theme_id=scene["theme_id"],
            prompt=scene["prompt"],
            output_size=locked,
        )
        if result["acquisition"] != _REQUIRED_IMAGE_MODEL:
            raise RuntimeError(
                f"{scene['theme_id']} used {result['acquisition']}, expected {_REQUIRED_IMAGE_MODEL}"
            )
        if tuple(result["size"]) != locked:
            raise RuntimeError(
                f"{scene['theme_id']} is {result['size']}, expected {list(locked)}"
            )
        if result["source_path"]:
            raise RuntimeError(f"raw plate was kept for {scene['theme_id']}")
        if canonical.read_bytes() != before:
            raise RuntimeError(f"refusing to alter the benchmark panorama: {canonical}")
        print(f"{result['path']} {locked[0]}x{locked[1]} model={result['acquisition']}")
        written.append(Path(result["path"]))
    purge_raw_panorama_sources()
    return written


def all_official_entries() -> tuple[dict[str, str], ...]:
    generated = tuple(
        {"file": f"{item['theme_id']}.png", "title": item["title"]}
        for item in (*_NEW_ARENAS, *_FINAL_ARENAS)
    )
    return official_arena_entries()[: len(_KEPT_ARENAS)] + generated


def inspect_all_arenas(destination: Path | None = None) -> Path:
    """Grid of every official graded panorama."""
    locked_path = _benchmark_panorama()
    with Image.open(locked_path) as opened:
        locked = opened.size
    destination = destination or (
        outputs_root()
        / "aiwake"
        / "_test_harness"
        / "scene_test"
        / "complete_15_arenas_catalog.png"
    )
    entries = all_official_entries()
    columns = 5
    thumb_w = 432
    thumb_h = int(round(locked[1] * (thumb_w / locked[0])))
    label_h = 44
    pad = 12
    rows = (len(entries) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (
            columns * thumb_w + (columns + 1) * pad,
            rows * (thumb_h + label_h) + (rows + 1) * pad,
        ),
        (24, 18, 14),
    )
    draw = ImageDraw.Draw(sheet)
    font = _catalog_font(18)
    root = locked_path.parent
    for index, entry in enumerate(entries):
        path = root / entry["file"]
        with Image.open(path) as opened:
            if opened.size != locked:
                raise RuntimeError(f"{path.name} is {opened.size}, expected {locked}")
            thumb = opened.convert("RGB").resize(
                (thumb_w, thumb_h),
                Image.Resampling.LANCZOS,
            )
        column = index % columns
        row = index // columns
        x = pad + column * (thumb_w + pad)
        y = pad + row * (thumb_h + label_h + pad)
        draw.rectangle((x, y, x + thumb_w, y + label_h), fill=(72, 48, 28))
        draw.text((x + 8, y + 10), entry["title"], fill=(245, 228, 196), font=font)
        sheet.paste(thumb, (x, y + label_h))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, format="PNG", compress_level=1)
    print(f"{destination} {sheet.size[0]}x{sheet.size[1]} plates={len(entries)}")
    return destination


def confirm_random_rotation() -> None:
    """Check the pipeline pool and the half-frame camera crop on every arena."""
    from core.animator.pipeline import resolve_scene_panorama, verify_arena_camera_crops

    chosen = resolve_scene_panorama("random")
    named = resolve_scene_panorama("arena_15_chamber_of_reason.png")
    if named.name != "arena_15_chamber_of_reason.png":
        raise RuntimeError(f"named arena lookup returned {named.name}")
    verify_arena_camera_crops()
    print(f"[ARENA SELECTOR] sample: {chosen.name}")
    print(f"[ARENA SELECTOR] named: {named.name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a shared cool/warm animator environment."
    )
    parser.add_argument("--theme", default=None, type=_safe_theme_id)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--image-path", type=Path)
    parser.add_argument("--test-color-grade", action="store_true")
    parser.add_argument("--scene", default="scene_03_observatory")
    parser.add_argument("--compare-benchmark", action="store_true")
    parser.add_argument("--denoise-smooth", action="store_true")
    parser.add_argument("--batch-generate-arenas", action="store_true")
    parser.add_argument("--model", default="")
    parser.add_argument("--inspect-catalog", action="store_true")
    parser.add_argument("--generate-final-5-arenas", action="store_true")
    parser.add_argument("--implement-random-rotation", action="store_true")
    parser.add_argument("--inspect-all-15", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.generate_final_5_arenas or args.implement_random_rotation or args.inspect_all_15:
        if args.generate_final_5_arenas:
            if args.model and args.model != _REQUIRED_IMAGE_MODEL:
                parser.error(f"--model must be {_REQUIRED_IMAGE_MODEL}")
            generate_final_arenas(args.model or _REQUIRED_IMAGE_MODEL)
        if args.implement_random_rotation:
            confirm_random_rotation()
        if args.inspect_all_15:
            inspect_all_arenas()
        return 0
    if args.batch_generate_arenas or args.inspect_catalog:
        if args.batch_generate_arenas:
            if args.model != _REQUIRED_IMAGE_MODEL:
                parser.error(f"--model must be {_REQUIRED_IMAGE_MODEL}")
            batch_generate_arenas(args.model)
        if args.inspect_catalog:
            inspect_official_catalog()
        return 0
    if args.test_color_grade or args.compare_benchmark:
        if not args.denoise_smooth:
            parser.error("--test-color-grade requires --denoise-smooth")
        print("bilateral denoise armed")
        result = run_color_grade_test(
            scene=args.scene,
            compare=args.compare_benchmark or args.test_color_grade,
        )
        print(json.dumps(result, indent=2))
        return 0
    if not args.theme:
        parser.error("--theme is required")
    result = create_environment(
        theme_id=args.theme,
        prompt=args.prompt,
        image_path=args.image_path,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ANTI_GRAIN",
    "GHIBLI_SMOOTHNESS",
    "GHIBLI_VIBRANCY",
    "PANORAMA_SIZE",
    "STATIC_SCENE_RULES",
    "apply_reverse_angle_ambience",
    "create_environment",
    "environment_prompt",
    "generation_aspect_for_size",
    "grade_ghibli_panorama",
    "sanitize_environment_prompt",
]
