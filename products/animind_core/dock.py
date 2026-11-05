"""Seat a drawn mouth under facial hair, the way an Aiwake chin dock seats a sprite.

The head is not repainted with a flat skin fill. A mustache layer keeps only
desaturated hair, so a beige band or a brown chin oval that rode along in the
cut is dropped. Flat skin under that hair, and compact brown ovals on the chin,
are filled from the surrounding skin. The mouth sprite then tucks up under the
hair. Nothing here is specific to one character.

A solid black stage connected to the canvas edge is keyed out so the scenario
plates show around the bust. A dark suit (lighter than the stage) stays.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

STAGE_LAYERS = {"body", "torso", "neck", "head", "hair"}
_SEAT: dict[tuple[Any, ...], tuple[Any, ...]] = {}
_KEYED: dict[int, Image.Image] = {}


def _image(img: Image.Image | str) -> Image.Image:
    if isinstance(img, Image.Image):
        return img
    from .scene import layer_image  # noqa: PLC0415

    return layer_image(img)


def key_backdrop(image: Image.Image) -> Image.Image:
    """Drop a pure-black stage that touches the canvas edge.

    Flood-fills from the border through pixels at luma 8 or below. A jacket
    sitting above that, and black pupils enclosed by the iris, are not reached.
    """
    import cv2  # noqa: PLC0415

    cached = _KEYED.get(id(image))
    if cached is not None:
        return cached
    arr = np.array(image.convert("RGBA"))
    rgb = arr[..., :3].astype(np.int16)
    lum = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    dark = (arr[..., 3] > 8) & (lum <= 8) & (chroma <= 8)
    if int(dark.sum()) < 64:
        _remember(image, image)
        return image
    n, labels = cv2.connectedComponents(dark.astype(np.uint8), connectivity=8)
    if n <= 1:
        _remember(image, image)
        return image
    edge = np.zeros(labels.shape, dtype=bool)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    touch = set(np.unique(labels[edge]).tolist()) - {0}
    if not touch:
        _remember(image, image)
        return image
    kill = np.isin(labels, list(touch))
    if int(kill.sum()) < 64:
        _remember(image, image)
        return image
    arr = arr.copy()
    arr[kill, 3] = 0
    keyed = Image.fromarray(arr, "RGBA")
    _remember(image, keyed)
    return keyed


def _remember(source: Image.Image, keyed: Image.Image) -> None:
    if len(_KEYED) > 64:
        _KEYED.clear()
    _KEYED[id(source)] = keyed


def _stats(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lum = rgb.astype(np.float32).mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    sat = chroma / np.maximum(rgb.max(axis=2), 1)
    return lum, chroma.astype(np.float32), sat.astype(np.float32)


def hair_only(image: Image.Image) -> Image.Image:
    """Mustache sprite limited to desaturated hair.

    Skin-toned beige and chromatic brown blobs lose their alpha. White, grey,
    and black hair stay, which is the cut the mouth docks under.
    """
    arr = np.array(image.convert("RGBA"))
    rgb = arr[..., :3].astype(np.float32)
    lum, chroma, sat = _stats(rgb)
    opaque = arr[..., 3] > 16
    dark = (lum < 95) & (chroma < 42)
    pale = (sat < 0.22) & (lum >= 110) & (lum <= 252)
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    brown = (red > green + 10) & (red > blue + 14) & (chroma > 18)
    skin = (chroma > 22) & (lum > 90) & (lum < 235) & (sat >= 0.18)
    keep = opaque & (dark | pale) & ~brown & ~skin
    if int(keep.sum()) < 8:
        return image
    out = arr.copy()
    out[..., 3] = np.where(keep, arr[..., 3], 0)
    return Image.fromarray(out, "RGBA")


def _ink_rows(image: Image.Image, *, thresh: int = 128) -> np.ndarray:
    alpha = np.asarray(image.getchannel("A"))
    return np.flatnonzero((alpha > thresh).any(axis=1))


def _hair_bottom(image: Image.Image, xy: tuple[int, int]) -> int | None:
    rows = _ink_rows(image)
    if rows.size == 0:
        return None
    return int(xy[1] + rows.max())


def tuck_under_hair(
    mouth: Image.Image,
    mouth_xy: tuple[int, int],
    mustache: Image.Image,
    mustache_xy: tuple[int, int],
) -> tuple[Image.Image, tuple[int, int]]:
    """Pull a mouth up so its top ink sits just under the mustache.

    The mustache is drawn later, so the overlap is hair over the lip, not a
    gap of bare skin. The sprite is not scaled and is not pushed downward.
    """
    hair_bottom = _hair_bottom(mustache, mustache_xy)
    ink = _ink_rows(mouth)
    if hair_bottom is None or ink.size == 0:
        return mouth, mouth_xy
    mouth_top = int(mouth_xy[1] + ink.min())
    dy = (hair_bottom - 1) - mouth_top
    if dy >= -1:
        return mouth, mouth_xy
    span = max(4, int(ink.max() - ink.min() + 1))
    dy = max(dy, -int(0.45 * span))
    return mouth, (int(mouth_xy[0]), int(mouth_xy[1] + dy))


def _paste_alpha(
    shape: tuple[int, int],
    image: Image.Image,
    xy: tuple[int, int],
    origin: tuple[int, int],
) -> np.ndarray:
    foot = np.zeros(shape, dtype=bool)
    alpha = np.asarray(image.getchannel("A")) > 128
    y0 = int(xy[1] - origin[1])
    x0 = int(xy[0] - origin[0])
    h, w = shape
    src_y0 = max(0, -y0)
    src_x0 = max(0, -x0)
    dst_y0 = max(0, y0)
    dst_x0 = max(0, x0)
    dst_y1 = min(h, y0 + alpha.shape[0])
    dst_x1 = min(w, x0 + alpha.shape[1])
    if dst_y1 <= dst_y0 or dst_x1 <= dst_x0:
        return foot
    foot[dst_y0:dst_y1, dst_x0:dst_x1] = alpha[
        src_y0:src_y0 + (dst_y1 - dst_y0),
        src_x0:src_x0 + (dst_x1 - dst_x0),
    ]
    return foot


def _brown_ovals(arr: np.ndarray, region: np.ndarray) -> np.ndarray:
    import cv2  # noqa: PLC0415

    rgb = arr[..., :3].astype(np.int16)
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    skin_lum = lum[(arr[..., 3] > 128) & region]
    if skin_lum.size < 30:
        return np.zeros(lum.shape, dtype=bool)
    # Darker than the chin around it. Ordinary warm skin is not an oval.
    ceiling = float(np.median(skin_lum)) - 28
    brown = (
        (arr[..., 3] > 128)
        & region
        & (red > green + 8)
        & (red > blue + 12)
        & (chroma > 16)
        & (chroma < 120)
        & (lum > 30)
        & (lum < ceiling)
    )
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(brown.astype(np.uint8), connectivity=8)
    keep = np.zeros(brown.shape, dtype=bool)
    limit = 0.08 * brown.size
    for index in range(1, count):
        x, y, width, height, area = (int(v) for v in stats[index])
        if area < 24 or area > limit or height < 4 or width < 4:
            continue
        aspect = width / max(1, height)
        if not 0.35 <= aspect <= 2.8:
            continue
        if area / max(1, width * height) < 0.45:
            continue
        core = cv2.erode((labels == index).astype(np.uint8), np.ones((3, 3), np.uint8))
        pixels = rgb[core > 0] if int(core.sum()) >= 12 else rgb[labels == index]
        if pixels.size == 0 or float(pixels.astype(np.float32).std(axis=0).max()) > 18:
            continue
        keep[labels == index] = True
    return keep


def _flat_skin(arr: np.ndarray, region: np.ndarray) -> np.ndarray:
    import cv2  # noqa: PLC0415

    rgb = arr[..., :3].astype(np.float32)
    lum, chroma, _sat = _stats(rgb)
    skin = (arr[..., 3] > 128) & (lum > 95) & (lum < 230) & (chroma > 16)
    sample = skin & ~region
    if int(sample.sum()) < 30:
        sample = skin
    cheek = float(lum[sample].std()) if int(sample.sum()) > 30 else 8.0
    blur = cv2.GaussianBlur(lum, (0, 0), 2.0)
    variance = cv2.GaussianBlur((lum - blur) ** 2, (0, 0), 2.0)
    local = np.sqrt(np.maximum(variance, 0))
    limit = min(3.2, 0.45 * max(cheek, 1.0))
    return region & skin & (local < limit)


def _fill(arr: np.ndarray, hole: np.ndarray) -> np.ndarray:
    import cv2  # noqa: PLC0415

    if int(hole.sum()) < 12:
        return arr
    mask = (hole.astype(np.uint8) * 255)
    filled = cv2.inpaint(np.ascontiguousarray(arr[..., :3]), mask, 4, cv2.INPAINT_TELEA)
    out = arr.copy()
    out[..., :3] = np.where(hole[..., None], filled, arr[..., :3])
    return out


def clear_chin_stains(
    head: Image.Image,
    head_xy: tuple[int, int],
    mustache: Image.Image,
    mustache_xy: tuple[int, int],
    mouths: list[tuple[Image.Image, tuple[int, int]]],
) -> Image.Image:
    """Replace a flat skin band and brown chin ovals under the mustache.

    The mouth footprint is left alone: the sprite covers it. Hair on the head
    stays. Textured skin outside that band stays, so a shaded chin is not
    repainted into a second flat patch.
    """
    arr = np.array(head.convert("RGBA"))
    height, width = arr.shape[:2]
    hair = _paste_alpha((height, width), mustache, mustache_xy, head_xy)
    ys, xs = np.nonzero(hair)
    if xs.size < 8:
        return head
    left, right, bottom = int(xs.min()), int(xs.max()), int(ys.max())
    mx0, mx1, my1 = left, right, bottom + 12
    for mouth, xy in mouths:
        ink_y = _ink_rows(mouth)
        ink_x = np.flatnonzero((np.asarray(mouth.getchannel("A")) > 128).any(axis=0))
        if ink_y.size and ink_x.size:
            mx0 = min(mx0, int(xy[0] - head_xy[0] + ink_x.min()))
            mx1 = max(mx1, int(xy[0] - head_xy[0] + ink_x.max()))
            my1 = max(my1, int(xy[1] - head_xy[1] + ink_y.max()))
    pad = max(6, (right - left) // 8)
    x0, x1 = max(0, min(left, mx0) - pad), min(width, max(right, mx1) + pad + 1)
    y0, y1 = max(0, bottom - 2), min(height, my1 + pad + 1)
    if y1 <= y0 or x1 <= x0:
        return head
    region = np.zeros((height, width), dtype=bool)
    region[y0:y1, x0:x1] = True
    head_rgb = arr[..., :3].astype(np.float32)
    lum, chroma, sat = _stats(head_rgb)
    head_hair = (arr[..., 3] > 128) & (((lum < 95) & (chroma < 42)) | ((sat < 0.22) & (lum >= 110) & (lum <= 252)))
    # The mouth sprite covers this band, so a stain under it is cleared too.
    # Leaving it would be the colour Telea copies back into the gap.
    region &= ~hair & ~head_hair
    hole = _flat_skin(arr, region) | _brown_ovals(arr, region)
    if int(hole.sum()) < 12:
        return head
    # The rim of a flat patch has a high local deviation, so it would stay and
    # Telea would copy that stain back into the middle. Grow through the band.
    import cv2  # noqa: PLC0415

    grown = cv2.dilate(hole.astype(np.uint8), np.ones((13, 13), np.uint8)) > 0
    hole = grown & region & (arr[..., 3] > 128)
    return Image.fromarray(_fill(arr, hole), "RGBA")


def is_mouth(name: str) -> bool:
    return name == "mouth" or name.startswith("mouth_") or name.startswith("expression_")


def seat_parts(
    parts: list[tuple[str, Any, tuple[int, int], int]],
) -> list[tuple[str, Any, tuple[int, int], int]]:
    """Clean one draw list: keyed stage, hair-only mustache, docked mouth.

    ``parts`` are ``(name, image or path, xy, z)`` as the scene compositor
    already stacks them. The returned list is what gets painted.
    """
    loaded = [(name, _image(img), (int(xy[0]), int(xy[1])), z) for name, img, xy, z in parts]
    head = next((part for part in loaded if part[0] == "head"), None)
    stache = next((part for part in loaded if part[0] == "mustache"), None)
    mouths = [part for part in loaded if is_mouth(part[0])]
    cache_key = (
        id(head[1]) if head else 0,
        head[2] if head else None,
        id(stache[1]) if stache else 0,
        stache[2] if stache else None,
        tuple((id(part[1]), part[2]) for part in mouths),
    )
    hit = _SEAT.get(cache_key)
    if hit is None:
        cleaned_head = key_backdrop(head[1]) if head else None
        cleaned_stache = hair_only(stache[1]) if stache else None
        tucked = {}
        if cleaned_stache is not None and stache is not None:
            for part in mouths:
                tucked[id(part[1])] = tuck_under_hair(part[1], part[2], cleaned_stache, stache[2])
        if head and stache and cleaned_head is not None and cleaned_stache is not None and mouths:
            # Stains are cleared against the docked mouth, so the gap it leaves
            # behind is filled and the sprite itself is not painted over.
            placed = [tucked.get(id(part[1]), (part[1], part[2])) for part in mouths]
            cleaned_head = clear_chin_stains(
                cleaned_head, head[2], cleaned_stache, stache[2], placed,
            )
        hit = (cleaned_head, cleaned_stache, tucked)
        if len(_SEAT) > 48:
            _SEAT.clear()
        _SEAT[cache_key] = hit
    cleaned_head, cleaned_stache, tucked = hit
    out: list[tuple[str, Any, tuple[int, int], int]] = []
    for name, img, xy, z in loaded:
        if name in STAGE_LAYERS and name != "head":
            img = key_backdrop(img)
        elif name == "head" and cleaned_head is not None:
            img = cleaned_head
        elif name == "mustache" and cleaned_stache is not None:
            img = cleaned_stache
        elif is_mouth(name) and id(img) in tucked:
            img, xy = tucked[id(img)]
        out.append((name, img, xy, z))
    return out
