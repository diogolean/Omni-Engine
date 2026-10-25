# -*- coding: utf-8 -*-
"""
pinterest_engine.image_transformer
-----------------------------------
Transforms existing 3:4 library assets into 1000x1500 (2:3) Pinterest Sales Pins.

The VM drip uses method ``cover``: the source photo is cover-cropped to
1000x1500 and the hook sits on a top gradient. There is no empty band.
``blurred_padding`` and ``center_crop`` keep the older hook panel and
bottom button bar.

Usage:
    from production.formats.pins.image_transformer import PinTransformer
    transformer = PinTransformer()
    pin_path = transformer.transform(record)   # record = parsed library JSON dict
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from shared.paths import windows_fonts_dir

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pin dimensions (Pinterest 2:3 standard)
# ---------------------------------------------------------------------------
PIN_W = 1000
PIN_H = 1500

# Colour palette (earth-toned, clinical-clean). Channel pin_theme overrides these.
_DEFAULT_THEME = {
    "vignette": (20, 18, 15, 180),
    "hook_text": (255, 251, 245),
    "button_bg": (44, 62, 44, 230),
    "button_text": (230, 220, 195),
    "url_text": (200, 195, 180),
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

# Layout zones (fractions of PIN_H)
_HOOK_ZONE_TOP_FRAC = 0.04

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


def _find_font(
    candidates: list[str], size: int
) -> "ImageFont.FreeTypeFont | ImageFont.ImageFont":
    global _warned_default_font
    for path in candidates:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except Exception:  # noqa: BLE001
                continue
    if not _warned_default_font:
        _warned_default_font = True
        log.warning(
            "No scalable pin font found; hook text will render tiny. "
            "Install fonts-liberation or keep Fonts/Montserrat in the image."
        )
    return ImageFont.load_default()


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
_DOMAIN_FONT_SIZE = 36


def _text_width(text: str, font) -> int:
    if hasattr(font, "getlength"):
        return int(font.getlength(text))
    bbox = font.getbbox(text) if hasattr(font, "getbbox") else (0, 0, len(text) * 8, 10)
    return int(bbox[2] - bbox[0])


def _hook_lines(hook_text: str) -> list[str]:
    """At most 8 words and 3 lines, wrapped to the card width on whole words."""
    from production.formats.pins.pin_copy import short_hook, trim_dangling_hook  # noqa: PLC0415

    words = short_hook(hook_text).upper().split()
    if not words:
        return []
    font = _find_font(_SERIF_CANDIDATES, 54)
    max_w = PIN_W - 80
    rows: list[list[str]] = [[]]
    for word in words:
        trial = rows[-1] + [word]
        overflows = bool(rows[-1]) and _text_width(" ".join(trial), font) > max_w
        if overflows:
            if len(rows) >= _HOOK_MAX_LINES:
                break
            rows.append([word])
        else:
            rows[-1].append(word)
    lines = [" ".join(row) for row in rows if row]
    if lines:
        lines[-1] = trim_dangling_hook(lines[-1])
        lines = [line for line in lines if line]
    return lines[:_HOOK_MAX_LINES]


def _record_credit(record: dict) -> str:
    text = str(record.get("image_credit") or "").strip()
    if text:
        return text
    path = str(record.get("local_image_path") or "").strip()
    if not path:
        return ""
    try:
        from production.providers.image.free.attribution import credit_for_image  # noqa: PLC0415

        return credit_for_image(path)
    except Exception:  # noqa: BLE001
        return ""


def _draw_credit_footer(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    credit: str,
    *,
    bottom: int,
) -> int:
    """Small author and license line just above the CTA bar."""
    text = (credit or "").strip()
    if not text:
        return 0
    zone_h = 28
    top = max(0, bottom - zone_h)
    bar = Image.new("RGBA", (PIN_W, zone_h), (8, 10, 12, 220))
    canvas.paste(bar, (0, top), bar)
    max_w = PIN_W - 48
    size = 18
    font = _find_font(_SANS_CANDIDATES, size)
    while size > 12 and _text_width(text, font) > max_w:
        size -= 1
        font = _find_font(_SANS_CANDIDATES, size)
    shown = text
    while shown and _text_width(shown, font) > max_w and " " in shown:
        shown = shown.rsplit(" ", 1)[0]
    if not shown:
        shown = text[:48]
    bbox = draw.textbbox((0, 0), shown, font=font)
    text_w = bbox[2] - bbox[0]
    draw.text(((PIN_W - text_w) // 2, top + 4), shown, font=font, fill=(232, 228, 216))
    return zone_h


def _focal_in_upper_band(image: Image.Image) -> bool:
    """True when the photo's detail sits in the top band, so the hook should drop."""
    small = image.convert("L").resize((32, 48))
    pixels = list(small.getdata())
    width, height = small.size
    if not pixels or width <= 0:
        return False
    mean = sum(pixels) / len(pixels)
    upper = 0.0
    lower = 0.0
    split = max(1, int(height * 0.4))
    for index, value in enumerate(pixels):
        energy = abs(value - mean)
        row = index // width
        if row < split:
            upper += energy
        elif row >= height - split:
            lower += energy
    return upper > lower * 1.25 and upper > 0


def _footer_content() -> tuple[str, str, int]:
    """Label, domain, and a bar only as tall as that text. No empty strip."""
    label = _button_label()
    url = _card_domain()
    if not label and not url:
        return "", "", 0
    height = 36
    if label:
        height += 48
    if url:
        height += 52
    return label, url, height


def _draw_hook_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    *,
    top: int,
) -> None:
    hook_text_colour = _theme_color("hook_text")
    font_large = _find_font(_SERIF_CANDIDATES, 54)
    line_height = 64
    cursor_y = top
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font_large)
        text_w = bbox[2] - bbox[0]
        x = (PIN_W - text_w) // 2
        draw.text((x + 2, cursor_y + 2), line, font=font_large, fill=(0, 0, 0, 180))
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
    """Short hook on a panel that is only as tall as the lines."""
    lines = _hook_lines(hook_text)
    if not lines:
        return
    vignette = _theme_color("vignette")
    divider = _theme_color("divider")
    line_height = 64
    zone_h = 28 + len(lines) * line_height + 18
    if anchor == "bottom":
        zone_top = max(0, PIN_H - footer_h - zone_h - 8)
    else:
        zone_top = int(PIN_H * _HOOK_ZONE_TOP_FRAC)

    panel = Image.new("RGBA", (PIN_W, zone_h), vignette)
    fade = _fade_mask(PIN_W, min(48, zone_h), vignette[3], top=(anchor != "bottom"))
    if anchor == "bottom":
        panel.paste(Image.new("RGBA", (PIN_W, fade.height), (0, 0, 0, 0)), (0, 0))
        # Keep a solid bar and fade only its top edge into the photo.
        edge = Image.new("RGBA", (PIN_W, fade.height), vignette)
        edge.putalpha(fade)
        panel.paste(edge, (0, 0))
    else:
        edge = Image.new("RGBA", (PIN_W, fade.height), vignette)
        edge.putalpha(fade)
        panel.paste(edge, (0, zone_h - fade.height))
    canvas.paste(panel, (0, zone_top), panel)
    _draw_hook_lines(draw, lines, top=zone_top + 16)
    div_y = zone_top + zone_h - 2
    draw.line([(60, div_y), (PIN_W - 60, div_y)], fill=divider[:3], width=2)


def _fade_mask(width: int, height: int, max_alpha: int, *, top: bool) -> Image.Image:
    """Grayscale mask, strongest at the top or the bottom edge."""
    ramp = Image.linear_gradient("L").resize((width, height))
    if top:
        ramp = ImageOps.invert(ramp)
    return ramp.point(lambda value: int(value * max_alpha / 255))


def _draw_cover_hook(
    canvas: Image.Image,
    hook_text: str,
    *,
    anchor: str = "top",
    footer_h: int = 0,
) -> None:
    """Short hook on a gradient, opposite the photo's focal band when needed."""
    lines = _hook_lines(hook_text)
    if not lines:
        return
    fade_h = 36 + len(lines) * 64 + 28
    top = 0 if anchor != "bottom" else max(0, PIN_H - footer_h - fade_h)
    mask = _fade_mask(PIN_W, fade_h, 185, top=(anchor != "bottom"))
    overlay = Image.new("RGBA", (PIN_W, fade_h), (8, 12, 14, 0))
    overlay.putalpha(mask)
    canvas.paste(overlay, (0, top), overlay)
    cursor = 28 if anchor != "bottom" else top + fade_h - (len(lines) * 64) - 8
    _draw_hook_lines(ImageDraw.Draw(canvas), lines, top=cursor)


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

    cursor = zone_top + 16
    if label:
        font_btn = _find_font(_SERIF_CANDIDATES, 34)
        bbox = draw.textbbox((0, 0), label, font=font_btn)
        btn_w = bbox[2] - bbox[0]
        draw.text(((PIN_W - btn_w) // 2, cursor), label, font=font_btn, fill=button_text)
        cursor += 48

    if url:
        margin_left = 48
        margin_right = 180
        max_w = PIN_W - margin_left - margin_right
        size = _DOMAIN_FONT_SIZE
        font_url = _find_font(_SANS_CANDIDATES, size)
        url_box = draw.textbbox((0, 0), url, font=font_url)
        url_w = url_box[2] - url_box[0]
        while url_w > max_w and size > 22:
            size -= 1
            font_url = _find_font(_SANS_CANDIDATES, size)
            url_box = draw.textbbox((0, 0), url, font=font_url)
            url_w = url_box[2] - url_box[0]
        draw.text((margin_left, cursor), url, font=font_url, fill=url_text)
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
        'cover' fills the 2:3 frame and lays the hook on a gradient.
        'blurred_padding' and 'center_crop' keep the older band layout.
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
        anchor = "bottom" if _focal_in_upper_band(source) else "top"
        _label, _url, button_h = _footer_content()
        credit_h = 28 if (credit or "").strip() else 0
        footer_h = button_h + credit_h
        if self.method == "cover":
            canvas = _center_crop_to_size(source, PIN_W, PIN_H).convert("RGBA")
            _draw_cover_hook(canvas, visual_hook, anchor=anchor, footer_h=footer_h)
            if credit_h:
                _draw_credit_footer(
                    canvas,
                    ImageDraw.Draw(canvas),
                    credit,
                    bottom=PIN_H - button_h,
                )
            _draw_button_zone(canvas, ImageDraw.Draw(canvas))
            return canvas.convert("RGB")

        if self.method == "center_crop":
            canvas = _center_crop_to_size(source, PIN_W, PIN_H)
        else:
            canvas = _blurred_padding(source, PIN_W, PIN_H)

        canvas = canvas.convert("RGBA")
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
