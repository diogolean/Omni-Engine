# -*- coding: utf-8 -*-
"""
pinterest_engine.image_transformer
-----------------------------------
Transforms existing 3:4 library assets into 1000x1500 (2:3) Pinterest Sales Pins.

Every method draws the same card: a 2:3 frame, a bold sans title on one
solid band, a short CTA, the domain, and a fine-print license line.
``cover`` and ``center_crop`` always fill the frame. ``blurred_padding``
fills it too when a cover crop keeps most of the photo, and letterboxes
with a blurred pad only when the crop would throw the picture away.

Usage:
    from production.formats.pins.image_transformer import PinTransformer
    transformer = PinTransformer()
    pin_path = transformer.transform(record)   # record = parsed library JSON dict
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from shared.paths import windows_fonts_dir

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pin dimensions (Pinterest 2:3 standard)
# ---------------------------------------------------------------------------
PIN_W = 1000
PIN_H = 1500
# CC BY still requires the author line. It stays in fine print.
CREDIT_FONT_SIZE = 18
CREDIT_FONT_FLOOR = 14
CREDIT_BAR_HEIGHT = 28
# A cover crop that keeps at least this much of the photo fills the frame.
_CROP_MIN_KEEP = 0.70

# Colour palette (earth-toned, clinical-clean). Channel pin_theme overrides these.
_DEFAULT_THEME = {
    "vignette": (20, 18, 15, 180),
    "hook_text": (255, 251, 245),
    "button_bg": (44, 62, 44, 230),
    "button_text": (230, 220, 195),
    "url_text": (255, 248, 230),
    "divider": (180, 160, 110, 200),
}


def _theme_color(name: str) -> tuple:
    from production.formats.pins import config as cfg  # noqa: PLC0415
    raw = (getattr(cfg, "PIN_THEME", None) or {}).get(name)
    if isinstance(raw, (list, tuple)) and 3 <= len(raw) <= 4:
        return tuple(int(v) for v in raw)
    return _DEFAULT_THEME[name]


_VIGNETTE_COLOUR = _DEFAULT_THEME["vignette"]
_HOOK_TEXT_COLOUR = _DEFAULT_THEME["hook_text"]
_BUTTON_BG_COLOUR = _DEFAULT_THEME["button_bg"]
_BUTTON_TEXT_COLOUR = _DEFAULT_THEME["button_text"]
_URL_TEXT_COLOUR = _DEFAULT_THEME["url_text"]
_DIVIDER_COLOUR = _DEFAULT_THEME["divider"]

def _button_label() -> str:
    from production.formats.pins.pin_copy import card_cta_label  # noqa: PLC0415

    return card_cta_label()


def _card_domain() -> str:
    from production.formats.pins.pin_copy import card_site_domain  # noqa: PLC0415

    return card_site_domain()

# Output subdirectory under outputs/
_PINS_SUBDIR = "pinterest_pins"

# ---------------------------------------------------------------------------
# Font resolution
# Windows faces first (the PC engine), then Liberation/DejaVu in the Docker
# image, then the Montserrat and Poppins files shipped in this repo.
# ImageFont.load_default() ignores point size, so it is only a last resort.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
_warned_default_font = False

# Windows Georgia first (the PC face). The repo then ships Liberation Serif,
# the metric-compatible Georgia substitute, so Docker and a Linux checkout
# render the same hook. System fonts are only a fallback after that.
_BUNDLED_SERIF = _REPO_ROOT / "Fonts" / "LiberationSerif"
_SERIF_CANDIDATES = [
    str(windows_fonts_dir() / "georgia.ttf"),
    str(windows_fonts_dir() / "georgiab.ttf"),
    str(windows_fonts_dir() / "georgiai.ttf"),
    str(windows_fonts_dir() / "palatia.ttf"),
    str(windows_fonts_dir() / "times.ttf"),
    str(windows_fonts_dir() / "timesbd.ttf"),
    str(_BUNDLED_SERIF / "LiberationSerif-Bold.ttf"),
    str(_BUNDLED_SERIF / "LiberationSerif-Regular.ttf"),
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    str(_REPO_ROOT / "Fonts" / "Montserrat" / "static" / "Montserrat-Bold.ttf"),
    str(_REPO_ROOT / "Fonts" / "Lora" / "EBGaramond-Italic.ttf"),
]

_SANS_CANDIDATES = [
    str(windows_fonts_dir() / "calibri.ttf"),
    str(windows_fonts_dir() / "calibrib.ttf"),
    str(windows_fonts_dir() / "segoeui.ttf"),
    str(windows_fonts_dir() / "arial.ttf"),
    str(_REPO_ROOT / "Fonts" / "Montserrat" / "static" / "Montserrat-Regular.ttf"),
    str(_REPO_ROOT / "Fonts" / "Poppins" / "Poppins-Regular.ttf"),
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

# Title face. Bold sans stays readable when the pin is a thumbnail.
# DejaVu is last so a Latin title uses the bundled face, and a credit
# that those faces cannot draw can still fall through to a Unicode face.
_SANS_BOLD_CANDIDATES = [
    str(windows_fonts_dir() / "arialbd.ttf"),
    str(windows_fonts_dir() / "calibrib.ttf"),
    str(windows_fonts_dir() / "segoeuib.ttf"),
    str(_REPO_ROOT / "Fonts" / "Montserrat" / "static" / "Montserrat-Bold.ttf"),
    str(_REPO_ROOT / "Fonts" / "Poppins" / "Poppins-Bold.ttf"),
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def configured_pin_method() -> str:
    """Channel ``pin_image_method``, otherwise the original blurred-padding card."""
    from production.formats.pins import config as cfg  # noqa: PLC0415

    pack, _path = cfg.load_channel_content_pack(cfg.CHANNEL_ID)
    raw = str(
        pack.get("pin_image_method")
        or pack.get("pin_method")
        or "blurred_padding"
    ).strip().lower()
    if raw in {"blurred_padding", "center_crop", "cover"}:
        return raw
    log.warning("Unknown pin_image_method %r; using blurred_padding.", raw)
    return "blurred_padding"


_FONT_CACHE: dict[tuple[int, int], "ImageFont.FreeTypeFont | ImageFont.ImageFont"] = {}


def _find_font(
    candidates: list[str], size: int
) -> "ImageFont.FreeTypeFont | ImageFont.ImageFont":
    global _warned_default_font
    key = (id(candidates), size)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont | None = None
    for path in candidates:
        if Path(path).is_file():
            try:
                font = ImageFont.truetype(path, size)
                break
            except Exception:  # noqa: BLE001
                continue
    if font is None:
        if not _warned_default_font:
            _warned_default_font = True
            log.warning(
                "No scalable pin font found; hook text will render tiny. "
                "Install fonts-liberation or keep Fonts/Montserrat in the image."
            )
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# ---------------------------------------------------------------------------
# Canvas helpers
# ---------------------------------------------------------------------------

def _center_crop_to_size(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scale and center-crop to exactly w x h with no distortion."""
    src_w, src_h = img.size
    ratio_target = w / h
    ratio_src = src_w / src_h

    if ratio_src > ratio_target:
        new_h = h
        new_w = int(src_w * h / src_h)
    else:
        new_w = w
        new_h = int(src_h * w / src_w)

    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def _blurred_padding(img: Image.Image, w: int, h: int) -> Image.Image:
    """
    Fit image inside w x h without cropping.
    Fill letterbox/pillarbox areas with a Gaussian-blurred, dimmed background.
    """
    src_w, src_h = img.size
    scale = min(w / src_w, h / src_h)
    fit_w = int(src_w * scale)
    fit_h = int(src_h * scale)

    # Background: stretch to fill, blur, darken
    bg = img.resize((w, h), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=28))
    bg = bg.convert("RGBA")
    dark = Image.new("RGBA", (w, h), (0, 0, 0, 120))
    bg = Image.alpha_composite(bg, dark).convert("RGB")

    # Foreground: sharp, scaled to fit
    fg = img.resize((fit_w, fit_h), Image.LANCZOS)
    x_off = (w - fit_w) // 2
    y_off = (h - fit_h) // 2
    bg.paste(fg, (x_off, y_off))
    return bg


_HOOK_MAX_LINES = 3
# Three times the previous 54px. A 5–8 word hook stays here. A longer
# clause shrinks, down to a floor that still fits three lines.
_HOOK_FONT_SIZE = 162
_HOOK_FONT_FLOOR = 32
_HOOK_LINE_HEIGHT = 190
_DOMAIN_FONT_SIZE = 48
_CTA_FONT_SIZE = 48


def _hook_line_height(font_size: int) -> int:
    """The large size keeps its leading. Smaller type tightens with it."""
    if font_size >= _HOOK_FONT_SIZE:
        return _HOOK_LINE_HEIGHT
    return max(font_size + 8, int(font_size * _HOOK_LINE_HEIGHT / _HOOK_FONT_SIZE))


def _wrap_hook_words(words: list[str], font, max_w: int) -> list[str] | None:
    """Wrap onto at most 3 lines. None when a word would be dropped or overflow."""
    if not words:
        return []
    rows: list[list[str]] = [[]]
    for word in words:
        if _text_width(word, font) > max_w:
            return None
        if not rows[-1]:
            rows[-1].append(word)
            continue
        trial = " ".join(rows[-1] + [word])
        if _text_width(trial, font) <= max_w:
            rows[-1].append(word)
            continue
        if len(rows) >= _HOOK_MAX_LINES:
            return None
        rows.append([word])
    return [" ".join(row) for row in rows if row]


def _text_width(text: str, font) -> int:
    if hasattr(font, "getlength"):
        return int(font.getlength(text))
    bbox = font.getbbox(text) if hasattr(font, "getbbox") else (0, 0, len(text) * 8, 10)
    return int(bbox[2] - bbox[0])


def _trim_hook_lines(lines: list[str]) -> list[str]:
    from production.formats.pins.pin_copy import trim_dangling_hook  # noqa: PLC0415

    if not lines:
        return []
    trimmed = list(lines)
    trimmed[-1] = trim_dangling_hook(trimmed[-1])
    return [line for line in trimmed if line]


def _hook_layout(hook_text: str) -> tuple[list[str], int]:
    """Lines and font size. Shrink so the predicate stays; do not drop words.

    The panel is still at most three lines. The large size is kept when
    the hook already fits. A longer clause shrinks instead of dropping words.
    """
    from production.formats.pins.pin_copy import short_hook  # noqa: PLC0415
    from shared.pin_text import clause_prefixes, is_dangling_pin_word  # noqa: PLC0415

    words = short_hook(hook_text).upper().split()
    if not words:
        return [], _HOOK_FONT_SIZE
    max_w = PIN_W - 64
    for size in range(_HOOK_FONT_SIZE, _HOOK_FONT_FLOOR - 1, -1):
        font = _find_font(_SANS_BOLD_CANDIDATES, size)
        lines = _wrap_hook_words(words, font, max_w)
        if lines is not None:
            return _trim_hook_lines(lines), size
    font = _find_font(_SANS_BOLD_CANDIDATES, _HOOK_FONT_FLOOR)
    text = " ".join(words)
    candidates = [text, *reversed(clause_prefixes(text))]
    seen: set[tuple[str, ...]] = set()
    for prefix in candidates:
        kept = prefix.split()
        while len(kept) > 1 and is_dangling_pin_word(kept[-1]):
            kept.pop()
        key = tuple(kept)
        if not kept or key in seen:
            continue
        seen.add(key)
        lines = _wrap_hook_words(kept, font, max_w)
        if lines is not None:
            return _trim_hook_lines(lines), _HOOK_FONT_FLOOR
    kept = list(words)
    while kept:
        while len(kept) > 1 and is_dangling_pin_word(kept[-1]):
            kept.pop()
        lines = _wrap_hook_words(kept, font, max_w)
        if lines is not None:
            return _trim_hook_lines(lines), _HOOK_FONT_FLOOR
        kept.pop()
    return [], _HOOK_FONT_FLOOR


def _hook_lines(hook_text: str) -> list[str]:
    """At most 3 lines, wrapped to the card width. The predicate is kept."""
    return _hook_layout(hook_text)[0]


def _record_credit(record: dict) -> str:
    from production.formats.pins.credit_text import repair_credit_encoding  # noqa: PLC0415

    text = repair_credit_encoding(str(record.get("image_credit") or ""))
    if text:
        return text
    path = str(record.get("local_image_path") or "").strip()
    if not path:
        return ""
    try:
        from production.providers.image.free.attribution import credit_for_image  # noqa: PLC0415

        return repair_credit_encoding(credit_for_image(path))
    except Exception:  # noqa: BLE001
        return ""


def _font_covers(font, text: str) -> bool:
    """False when *font* would draw a missing-glyph box for any character."""
    if not hasattr(font, "getbbox"):
        return False
    try:
        missing = font.getbbox("\ue000")
    except Exception:  # noqa: BLE001
        return False
    for ch in text:
        if ch.isspace():
            continue
        try:
            box = font.getbbox(ch)
        except Exception:  # noqa: BLE001
            return False
        if box == missing:
            return False
    return True


def _covering_font(text: str, size: int, candidates: list[str]):
    """First face in *candidates* that can draw every character of *text*."""
    for path in candidates:
        if not Path(path).is_file():
            continue
        try:
            font = ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            continue
        if _font_covers(font, text):
            return font
    return None


def _draw_credit_footer(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    credit: str,
    *,
    bottom: int,
) -> int:
    """Fine-print author and license line just above the CTA bar."""
    from production.formats.pins.credit_text import shape_credit_line  # noqa: PLC0415

    text = shape_credit_line(credit)
    if not text:
        return 0
    zone_h = CREDIT_BAR_HEIGHT
    top = max(0, bottom - zone_h)
    bar = Image.new("RGBA", (PIN_W, zone_h), (8, 10, 12, 150))
    canvas.paste(bar, (0, top), bar)
    max_w = PIN_W - 48
    size = CREDIT_FONT_SIZE
    font = _covering_font(text, size, _SANS_CANDIDATES)
    while (
        font is not None
        and size > CREDIT_FONT_FLOOR
        and _text_width(text, font) > max_w
    ):
        size -= 1
        font = _covering_font(text, size, _SANS_CANDIDATES)
    if font is None:
        log.warning("PIN CREDIT | no font covers %r", credit)
        font = _find_font(_SANS_CANDIDATES, size)
    shown = text
    while shown and _text_width(shown, font) > max_w and " " in shown:
        shown = shown.rsplit(" ", 1)[0]
    if not shown:
        shown = text[:48]
    bbox = draw.textbbox((0, 0), shown, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (PIN_W - text_w) // 2
    y = top + max(0, (zone_h - text_h) // 2)
    draw.text((x, y), shown, font=font, fill=(232, 228, 216))
    return zone_h


def pin_text_position() -> str:
    """``top`` (top third) or ``bottom`` (solid lower band), from page_config.

    ``pin_theme.text_position`` is the same switch for a channel pack that
    has no page_config constant. Anything else stays ``top``.
    """
    from production.formats.pins import config as cfg  # noqa: PLC0415
    from shared.channel_flags import value  # noqa: PLC0415

    channel = str(getattr(cfg, "CHANNEL_ID", "") or "")
    raw = value(channel, "PIN_TEXT_POSITION", None)
    if raw is None or raw == "":
        raw = (getattr(cfg, "PIN_THEME", None) or {}).get("text_position", "top")
    text = str(raw or "top").strip().lower()
    if text in {"top", "bottom"}:
        return text
    log.warning("Unknown PIN_TEXT_POSITION %r; using top.", raw)
    return "top"


def _crop_min_keep() -> float:
    from production.formats.pins import config as cfg  # noqa: PLC0415
    from shared.channel_flags import value  # noqa: PLC0415

    channel = str(getattr(cfg, "CHANNEL_ID", "") or "")
    raw = value(channel, "PIN_CROP_MIN_KEEP", _CROP_MIN_KEEP)
    try:
        return min(1.0, max(0.0, float(raw)))
    except (TypeError, ValueError):
        log.warning("Invalid PIN_CROP_MIN_KEEP %r; using %.2f.", raw, _CROP_MIN_KEEP)
        return _CROP_MIN_KEEP


def cover_keeps_enough(image: Image.Image, w: int = PIN_W, h: int = PIN_H) -> bool:
    """True when a cover crop keeps at least ``PIN_CROP_MIN_KEEP`` of the photo."""
    src_w, src_h = image.size
    if src_w < 1 or src_h < 1 or w < 1 or h < 1:
        return False
    scale = max(w / src_w, h / src_h)
    kept = (w * h) / ((src_w * scale) * (src_h * scale))
    return kept + 1e-9 >= _crop_min_keep()


def _frame_photo(image: Image.Image, method: str) -> Image.Image:
    """Fill 1000x1500. Blur-pad only when the crop would discard too much."""
    if method in {"cover", "center_crop"} or (
        method == "blurred_padding" and cover_keeps_enough(image)
    ):
        return _center_crop_to_size(image, PIN_W, PIN_H)
    return _blurred_padding(image, PIN_W, PIN_H)


def _footer_content() -> tuple[str, str, int]:
    """Label, domain, and a bar only as tall as that text. No empty strip."""
    label = _button_label()
    url = _card_domain()
    if not label and not url:
        return "", "", 0
    height = 24
    if label:
        height += 56
    if url:
        height += 58
    return label, url, height


def _draw_hook_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    *,
    top: int,
    font_size: int = _HOOK_FONT_SIZE,
    line_height: int = _HOOK_LINE_HEIGHT,
) -> None:
    hook_text_colour = _theme_color("hook_text")
    font_large = _find_font(_SANS_BOLD_CANDIDATES, font_size)
    cursor_y = top
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_large)
        text_w = bbox[2] - bbox[0]
        x = (PIN_W - text_w) // 2
        draw.text((x + 3, cursor_y + 3), line, font=font_large, fill=(0, 0, 0, 210))
        draw.text((x, cursor_y), line, font=font_large, fill=hook_text_colour)
        cursor_y += line_height


def _draw_hook_zone(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    hook_text: str,
    *,
    anchor: str = "top",
    footer_h: int = 0,
) -> None:
    """Title on one solid dark band, in the top third or on the bottom."""
    lines, font_size = _hook_layout(hook_text)
    if not lines:
        return
    vignette = _theme_color("vignette")
    divider = _theme_color("divider")
    line_height = _hook_line_height(font_size)
    zone_h = 36 + len(lines) * line_height + 20
    if anchor == "bottom":
        zone_top = max(0, PIN_H - footer_h - zone_h)
    else:
        zone_top = 0

    rgb = tuple(int(v) for v in vignette[:3])
    panel = Image.new("RGBA", (PIN_W, zone_h), (*rgb, 235))
    canvas.paste(panel, (0, zone_top), panel)
    _draw_hook_lines(
        draw, lines, top=zone_top + 24, font_size=font_size, line_height=line_height
    )
    edge = zone_top + zone_h - 1 if anchor != "bottom" else zone_top
    draw.line([(0, edge), (PIN_W, edge)], fill=divider[:3], width=3)


def _draw_button_zone(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
) -> int:
    """Slim footer for the CTA and a larger domain. Returns the bar height."""
    label, url, zone_h = _footer_content()
    if zone_h <= 0:
        return 0
    zone_top = PIN_H - zone_h
    button_bg = _theme_color("button_bg")
    button_text = _theme_color("button_text")
    url_text = _theme_color("url_text")
    divider = _theme_color("divider")

    btn_panel = Image.new("RGBA", (PIN_W, zone_h), button_bg)
    canvas.paste(btn_panel, (0, zone_top), btn_panel)
    draw.line([(0, zone_top), (PIN_W, zone_top)], fill=divider[:3], width=3)

    cursor = zone_top + 14
    max_w = PIN_W - 64
    if label:
        size = _CTA_FONT_SIZE
        font_btn = _find_font(_SANS_BOLD_CANDIDATES, size)
        bbox = draw.textbbox((0, 0), label, font=font_btn)
        btn_w = bbox[2] - bbox[0]
        while btn_w > max_w and size > 28:
            size -= 1
            font_btn = _find_font(_SANS_BOLD_CANDIDATES, size)
            bbox = draw.textbbox((0, 0), label, font=font_btn)
            btn_w = bbox[2] - bbox[0]
        draw.text(((PIN_W - btn_w) // 2, cursor), label, font=font_btn, fill=button_text)
        cursor += 56

    if url:
        size = _DOMAIN_FONT_SIZE
        font_url = _find_font(_SANS_BOLD_CANDIDATES, size)
        url_box = draw.textbbox((0, 0), url, font=font_url)
        url_w = url_box[2] - url_box[0]
        while url_w > max_w and size > 28:
            size -= 1
            font_url = _find_font(_SANS_BOLD_CANDIDATES, size)
            url_box = draw.textbbox((0, 0), url, font=font_url)
            url_w = url_box[2] - url_box[0]
        draw.text(((PIN_W - url_w) // 2, cursor), url, font=font_url, fill=url_text)
    return zone_h


# ---------------------------------------------------------------------------
# PinTransformer
# ---------------------------------------------------------------------------

class PinTransformer:
    """
    Converts a library record dict into a 1000x1500 Pinterest Sales Pin JPEG.

    Parameters
    ----------
    outputs_dir : Path, optional
        Root outputs/ directory. Defaults to config.OUTPUTS_DIR.
    method : str
        'cover' and 'center_crop' fill the frame.
        'blurred_padding' fills the frame when the crop keeps the photo,
        and blur-pads only when it would not.
    """

    def __init__(
        self,
        outputs_dir: "Path | None" = None,
        method: str = "blurred_padding",
    ) -> None:
        if outputs_dir is None:
            from production.formats.pins import config as _cfg  # noqa: PLC0415
            outputs_dir = _cfg.OUTPUTS_DIR

        self.pins_dir: Path = outputs_dir / _PINS_SUBDIR
        self.pins_dir.mkdir(parents=True, exist_ok=True)
        self.method = method

    # ------------------------------------------------------------------
    # Public API

    def transform(self, record: dict) -> "Path | None":
        """
        Build the 2:3 pin for a single library record.
        Returns the saved pin Path, or None if the source image is missing.
        """
        img_path = record.get("local_image_path", "")
        imgbb_url = record.get("imgbb_url", "")
        from production.formats.pins import config as cfg  # noqa: PLC0415
        topic = record.get("topic") or cfg.DEFAULT_TOPIC
        from production.formats.pins.pin_copy import card_hook_text  # noqa: PLC0415

        visual_hook = card_hook_text(record) or topic
        subject_slug = record.get("subject_slug", "pin")
        variant = record.get("variant_index", 0)

        source = self._resolve_source(
            record,
            img_path,
            imgbb_url,
            record.get("local_video_path", ""),
        )
        if source is None:
            log.warning("No source image for: %s v%s", topic, variant)
            return None

        pin = self._compose(source, visual_hook, credit=_record_credit(record))

        filename = f"{subject_slug}_v{variant:02d}_pin.jpg"
        out_path = self.pins_dir / filename
        pin.save(str(out_path), format="JPEG", quality=92, optimize=True)
        log.info("Pin saved: %s", out_path.name)
        return out_path

    def transform_batch(
        self, records: list[dict]
    ) -> dict[str, "Path | None"]:
        """Process multiple records. Returns {slug_vN: Path | None}."""
        out: dict[str, "Path | None"] = {}
        for rec in records:
            key = f"{rec.get('subject_slug', 'pin')}_v{rec.get('variant_index', 0):02d}"
            out[key] = self.transform(rec)
        return out

    def get_pin_bytes(self, record: dict) -> bytes | None:
        """
        Transform a record and return the JPEG bytes in memory (no disk write).
        Used by the publisher to send base64-encoded image to Pinterest API.
        """
        from production.formats.pins import config as cfg  # noqa: PLC0415

        img_path = record.get("local_image_path", "")
        imgbb_url = record.get("imgbb_url", "")
        from production.formats.pins.pin_copy import card_hook_text  # noqa: PLC0415

        visual_hook = (
            card_hook_text(record)
            or record.get("topic")
            or cfg.DEFAULT_TOPIC
        )

        source = self._resolve_source(
            record,
            img_path,
            imgbb_url,
            record.get("local_video_path", ""),
        )
        if source is None:
            return None

        pin = self._compose(source, visual_hook, credit=_record_credit(record))
        buf = io.BytesIO()
        pin.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Private

    def _load_source(
        self,
        local_path: str,
        imgbb_url: str,
        video_path: str = "",
    ) -> "Image.Image | None":
        if local_path and Path(local_path).is_file():
            try:
                return Image.open(local_path).convert("RGB")
            except Exception as exc:  # noqa: BLE001
                log.warning("Cannot open %s: %s", local_path, exc)

        if imgbb_url and imgbb_url.startswith("http"):
            try:
                import urllib.request  # noqa: PLC0415
                with urllib.request.urlopen(imgbb_url, timeout=15) as resp:
                    data = resp.read()
                return Image.open(io.BytesIO(data)).convert("RGB")
            except Exception as exc:  # noqa: BLE001
                log.warning("Cannot fetch %s: %s", imgbb_url, exc)

        if video_path and Path(video_path).is_file():
            try:
                from shared.video_still import extract_video_still  # noqa: PLC0415
                still = extract_video_still(
                    Path(video_path),
                    self.pins_dir / "video_stills" / f"{Path(video_path).stem}.jpg",
                )
                if still:
                    return Image.open(still).convert("RGB")
            except Exception as exc:  # noqa: BLE001
                log.warning("Cannot extract still from %s: %s", video_path, exc)

        return None

    def _resolve_source(
        self,
        record: dict,
        local_path: str,
        imgbb_url: str,
        video_path: str = "",
    ) -> "Image.Image | None":
        """Owned file first. Free-license search only when that file is missing.

        Pin-sized Flux stays in the scheduler. The image returned here is
        always composited by ``_compose``. The raw download is not the pin.
        """
        source = self._load_source(local_path, imgbb_url, video_path)
        if source is not None:
            return source
        try:
            from production.providers.image.free.integrate import load_card_source

            return load_card_source(record, self.pins_dir, allow_flux=False)
        except Exception as exc:  # noqa: BLE001
            log.info("Free card source skipped (%s)", type(exc).__name__)
            return None

    def _compose(
        self,
        source: Image.Image,
        visual_hook: str,
        credit: str = "",
    ) -> Image.Image:
        """Assemble the final 1000x1500 canvas."""
        anchor = pin_text_position()
        _label, _url, button_h = _footer_content()
        credit_h = CREDIT_BAR_HEIGHT if (credit or "").strip() else 0
        footer_h = button_h + credit_h
        canvas = _frame_photo(source, self.method).convert("RGBA")
        draw = ImageDraw.Draw(canvas)
        _draw_hook_zone(canvas, draw, visual_hook, anchor=anchor, footer_h=footer_h)
        if credit_h:
            _draw_credit_footer(canvas, draw, credit, bottom=PIN_H - button_h)
        _draw_button_zone(canvas, draw)
        return canvas.convert("RGB")


# ---------------------------------------------------------------------------
# Quick standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json
    import sys
    _root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(_root))
    from production.formats.pins import config as cfg

    files = sorted(cfg.LIBRARY_DIR.glob("post_*.json"))
    if not files:
        print("No library records found.")
        sys.exit(1)

    for f in reversed(files):
        rec = json.loads(f.read_text(encoding="utf-8"))
        if rec.get("local_image_path") or rec.get("imgbb_url"):
            t = PinTransformer()
            result = t.transform(rec)
            print(f"Pin created: {result}")
            break
    else:
        print("No records with image paths found.")
