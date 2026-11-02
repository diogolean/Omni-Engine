"""Frame-by-frame mouth QA for rendered videos.

The scene records, per frame, every mouth layer it drew (``Scene.trace``) with a reference measurement
of that one mouth taken on the stage right after drawing it. ``check`` decodes every video frame,
re-measures the mouth where the trace puts it, and fails frames that show two mouth layers, a blended
camera dissolve, more stacked mouth cavities than the drawn sprite has, or an opening well above the
drawn sprite's own (or above ``CAV_ABS``).

When the trace carries ``speech`` spans (``{start, end, speaker}`` in seconds, one per voiced line), it
also fails a mouth that does not talk while its line is heard: the speaker's drawn mouth never changes
shape, or its measured opening never reaches ``OPEN_MIN``; and a whole video with speech whose mouths
never measure open at all (``maxOpen`` 0).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

import cv2
import numpy as np
from PIL import Image

CAV_ABS = 0.50  # cavity height / mouth width, any viseme or emotional mouth
# Left-view happy rest is an open smile. A closed seam measures ~0 and then
# the encoded frame, which still shows the open shape, fails against it.
HAPPY_REST_CAV = 0.38
HAPPY_REST_FLOOR = 0.30
# Measured cavity (dark rows / padded width). Closed shapes stay near 0.
# Open shapes, including left-view E, must stay open. These are asset floors,
# not a change to CAV_ABS or the speech gates.
VISEME_CAV_MIN = {
    "X": 0.0, "A": 0.0, "B": 0.08, "C": 0.12, "D": 0.16,
    "E": 0.32, "F": 0.24, "G": 0.10, "H": 0.12,
}
VISEME_CAV_MAX = {"X": 0.08, "A": 0.08}
CAV_GROW = 1.3  # x the drawn sprite's own cavity, plus CAV_SLACK, before a frame counts as over-open
CAV_SLACK = 0.08
NORM_W = 64  # mouth crops on the video are measured at this width
MIN_QA_REST_W = 28  # a side-view X upscaled a few times is narrower than this; its shadow is not a cavity
SPEECH_MIN_S = 0.4  # shorter voiced lines are not judged for a frozen mouth
OPEN_MIN = 0.05  # measured cavity / mouth width that counts as a visibly open mouth
MIN_MEASURED = 6  # measured frames of the speaker a line needs before its openness is judged
BLINK_RATE_MIN = 8.0
BLINK_RATE_MAX = 36.0


def measure(img: np.ndarray, box: Any, *, norm_w: int | None = None) -> dict[str, Any] | None:
    """Cavity rows per mouth width and stacked-cavity count inside box (x0, y0, x1, y1) of an RGB array.

    Video frames stay at ``NORM_W``. A drawn sprite can pass its own width so a
    real D is not quantized to 10/64, under the 0.16 floor.
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    H, W = img.shape[:2]
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
        return None  # mouth partly off-frame: not measurable
    if x1 - x0 < 6 or y1 - y0 < 6:
        return None
    nw = NORM_W if norm_w is None else max(6, int(norm_w))
    # one scale for every measurement (stage reference at 2x supersampling, frames at any zoom)
    crop = Image.fromarray(np.ascontiguousarray(img[y0:y1, x0:x1]))
    c = np.asarray(crop.resize((nw, max(6, round(nw * (y1 - y0) / (x1 - x0)))), Image.LANCZOS)).astype(np.float32)
    x0, y0, x1, y1 = 0, 0, c.shape[1], c.shape[0]
    lum = c @ np.array([0.299, 0.587, 0.114], np.float32)
    border = np.concatenate([lum[0], lum[-1], lum[:, 0], lum[:, -1]])
    dark = lum < 0.55 * float(np.median(border))
    dark = cv2.morphologyEx(dark.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _lab, stats, _centres = cv2.connectedComponentsWithStats(dark, connectivity=8)
    if n <= 1:
        return {"cav": 0.0, "blobs": 0}
    bh, bw = dark.shape
    def inner(i: int) -> bool:  # jaw lines, hair and nose strokes enter from the crop edge; a mouth cavity does not
        x, y, w, h, _area = stats[i]
        cx = x + w / 2
        return y > 0 and y + h < bh and x > 0 and x + w < bw and 0.15 * bw < cx < 0.85 * bw
    keep = [i for i in range(1, n) if inner(i)]
    if not keep:
        return {"cav": 0.0, "blobs": 0}
    amax = max(stats[i, cv2.CC_STAT_AREA] for i in keep)
    big = [i for i in keep if stats[i, cv2.CC_STAT_AREA] >= max(6, 0.12 * amax)]
    rows = np.zeros(dark.shape[0], bool)
    spans = []
    for i in big:
        x, y, w, h, _area = stats[i]
        rows[y:y + h] = True
        spans.append((y, y + h, x, x + w))
    # stacked = two blobs overlapping horizontally but separated vertically by a gap
    stacked = 0
    for a in range(len(spans)):
        for b in range(a + 1, len(spans)):
            p, q = sorted([spans[a], spans[b]])
            ov = min(p[3], q[3]) - max(p[2], q[2])
            wide = min(p[3] - p[2], q[3] - q[2]) >= 0.25 * bw
            if wide and ov > 0.5 * min(p[3] - p[2], q[3] - q[2]) and q[0] - p[1] > 1:
                stacked += 1
    return {"cav": float(rows.sum()) / (x1 - x0), "blobs": 1 + stacked}



def _frames(mp4: Path, w: int, h: int) -> Iterator[np.ndarray]:
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(mp4), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = w * h * 3
    try:
        while (b := p.stdout.read(n)) and len(b) == n:  # type: ignore[union-attr]
            yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    finally:
        p.kill()
        p.wait()


def speech_check(trace: dict[str, Any], opens: dict[int, dict[int, float]]) -> list[tuple[int, str]]:
    """Frozen or shut mouths while a line is heard. ``opens``: frame index -> {char: measured cavity}."""
    frames = trace["frames"]
    bad: list[tuple[int, str]] = []
    for k, sp in enumerate(trace.get("speech") or []):
        start, end, who = float(sp["start"]), float(sp["end"]), int(sp["speaker"])
        if end - start < SPEECH_MIN_S:
            continue
        idx = [i for i, f in enumerate(frames) if start <= f["t"] < end]
        drawn = [m["v"] for i in idx for m in frames[i]["mouths"] if m["char"] == who]
        if not drawn:
            continue  # speaker off screen, or drawn without a mouth rig (silhouette)
        first = idx[0] if idx else 0
        if len(set(drawn)) < 2:
            bad.append((first, f"line {k} char {who}: mouth frozen on {drawn[0]} for {end - start:.1f}s of speech"))
            continue
        seen = [opens[i][who] for i in idx if who in opens.get(i, {})]
        if len(seen) >= MIN_MEASURED and max(seen) < OPEN_MIN:
            bad.append((first, f"line {k} char {who}: mouth never opens while speaking (max {max(seen):.2f})"))
    return bad


def facial_check(trace: dict[str, Any]) -> tuple[list[tuple[int, str]], dict[str, Any]]:
    """Validate actual rendered face states recorded by :class:`scene.Scene`.

    Blink starts are counted from open -> half transitions.  Brow poses must
    leave neutral during an emotional line and must differ when the script
    carries more than one emotion.
    """
    frames = trace.get("frames") or []
    duration = (float(frames[-1]["t"]) - float(frames[0]["t"])) if len(frames) > 1 else 0.0
    chars = sorted({int(face["char"]) for frame in frames for face in frame.get("faces", [])})
    bad: list[tuple[int, str]] = []
    blink_rates: dict[int, float] = {}
    brow_states: dict[int, set[tuple[float, ...]]] = {char: set() for char in chars}
    brow_emotions: dict[int, set[str]] = {char: set() for char in chars}
    for char in chars:
        states: list[tuple[int, dict[str, Any]]] = [
            (index, face)
            for index, frame in enumerate(frames)
            for face in frame.get("faces", [])
            if int(face["char"]) == char
        ]
        starts = sum(
            1 for (_ia, before), (_ib, after) in zip(states, states[1:])
            if before.get("blink") == "open" and after.get("blink") == "half"
        )
        if duration >= 6.0:
            rate = starts * 60.0 / duration
            blink_rates[char] = round(rate, 2)
            if not BLINK_RATE_MIN <= rate <= BLINK_RATE_MAX:
                bad.append((states[0][0], f"char {char}: blink rate {rate:.1f}/min outside "
                                          f"{BLINK_RATE_MIN:.0f}-{BLINK_RATE_MAX:.0f}"))
        for _index, face in states:
            emotion = str(face.get("emotion") or "neutro")
            pose = tuple(float(v) for v in face.get("brows") or ())
            if emotion != "neutro":
                brow_emotions[char].add(emotion)
                brow_states[char].add(pose)
        if brow_emotions[char] and not any(any(abs(v) >= 0.3 for v in pose) for pose in brow_states[char]):
            bad.append((states[0][0], f"char {char}: brows frozen at neutral during emotional speech"))
        if len(brow_emotions[char]) > 1 and len(brow_states[char]) < 2:
            bad.append((states[0][0], f"char {char}: brows do not vary across emotions"))
        for index, face in states:
            for eye in face.get("eyes") or []:
                lid, iris = tuple(eye["lid"]), tuple(eye["iris"])
                if not lids_on_iris(lid, iris, tol=4):
                    bad.append((index, f"char {char}: lid centre off the iris by more than 4px"))
                    break
            else:
                continue
            break
    report = {
        "blinkRatesPerMin": blink_rates,
        "browEmotions": {char: sorted(values) for char, values in brow_emotions.items()},
        "browPoseCounts": {char: len(values) for char, values in brow_states.items()},
    }
    return bad, report


def happy_cavity(sprite: Image.Image, *, norm_w: int | None = None) -> float:
    """Cavity ratio of one resting mouth on a skin plate, measured like a frame.

    The plate is larger than the sprite so the QA padding is skin, the same
    way the stage surrounds the mouth. A sprite that fills its own canvas
    would otherwise look closed because the cavity touches the crop edge.
    """
    ink = sprite.getchannel("A").point(lambda value: 255 if value > 128 else 0).getbbox()
    if ink is None:
        return 0.0
    x0, y0, x1, y1 = ink
    width, height = x1 - x0, y1 - y0
    pad_x, pad_y = max(3, round(width * 0.2)), max(3, round(height * 0.35))
    margin = max(pad_x, pad_y) + 4
    plate = Image.new("RGBA", (sprite.width + 2 * margin, sprite.height + 2 * margin), (236, 200, 170, 255))
    plate.alpha_composite(sprite, (margin, margin))
    box = (margin + x0 - pad_x, margin + y0 - pad_y, margin + x1 + pad_x, margin + y1 + pad_y)
    measured = measure(np.asarray(plate.convert("RGB")), box, norm_w=norm_w)
    return float(measured["cav"]) if measured else 0.0


def sprite_cavity(sprite: Image.Image) -> float:
    """Cavity of a drawn mouth at its own resolution.

    ``happy_cavity`` still uses the 64px frame scale. A real D is about 16px
    open on a wider sprite; at 64px that becomes 10/64 and loses to the 0.16 floor.
    """
    ink = sprite.getchannel("A").point(lambda value: 255 if value > 128 else 0).getbbox()
    if ink is None:
        return 0.0
    pad_x = max(3, round((ink[2] - ink[0]) * 0.2))
    return happy_cavity(sprite, norm_w=max(NORM_W, (ink[2] - ink[0]) + 2 * pad_x))


def preflight_happy(view_dir: Path) -> str | None:
    """Fail before encode when the saved happy rest is still a closed line."""
    path = view_dir / "expressions" / "expression_happy.png"
    if not path.is_file():
        data_path = view_dir / "view.json"
        if not data_path.is_file():
            return None
        try:
            rel = (json.loads(data_path.read_text(encoding="utf-8")).get("expressions") or {}).get("happy", {}).get("url")
        except (OSError, json.JSONDecodeError, AttributeError):
            return None
        if not rel:
            return None
        path = view_dir / rel
    if not path.is_file():
        return None
    with Image.open(path) as opened:
        cavity = happy_cavity(opened.convert("RGBA"))
    if cavity < HAPPY_REST_FLOOR:
        return (
            f"{view_dir.name} happy rest cavity {cavity:.2f} is closed; "
            f"frame QA expects about {HAPPY_REST_CAV:.2f}"
        )
    return None


_SAFE_COLOURS = {
    "outline": (40, 30, 30, 255),
    "cavity": (20, 12, 14, 255),
    "teeth": (247, 244, 236, 255),
    "tongue": (190, 86, 92, 255),
    "lip": (142, 72, 78, 255),
}


def repair_black_bar_visemes(view_dir: Path) -> list[str]:
    """Replace a flat black mouth sticker with an open lipped shape.

    Cached front views (Einstein) can already have the bar baked into the PNG.
    The canvas size stays the same so the rig anchor does not move. Frame QA
    thresholds are unchanged.
    """
    mouths = view_dir / "mouths"
    if not mouths.is_dir():
        return []
    from . import mouthset  # noqa: PLC0415

    notes: list[str] = []
    for path in sorted(mouths.glob("*.png")):
        code = path.stem
        if code in "XA":
            continue
        with Image.open(path) as image:
            sprite = image.convert("RGBA")
            if not mouthset._is_black_bar(sprite):
                continue
            box = sprite.getchannel("A").getbbox() or (0, 0, sprite.width, sprite.height)
            width = max(12, box[2] - box[0])
            drawn = mouthset._lipped_open(width, _SAFE_COLOURS)
            canvas = Image.new("RGBA", sprite.size, (0, 0, 0, 0))
            canvas.paste(drawn, ((sprite.width - drawn.width) // 2, (sprite.height - drawn.height) // 2))
        canvas.save(path)
        notes.append(f"{view_dir.name} {code}: flat black mouth redrawn")
    return notes


def repair_closed_visemes(view_dir: Path) -> list[str]:
    """Replace open visemes that drew shut (a moustache often measures as 0) with the safe mouth set.

    Viseme B is the usual failure: the sprite's own cavity is 0.00 and the encoded
    frame, which still shows a little dark hair, measures about 0.09.
    """
    mouths = view_dir / "mouths"
    if not mouths.is_dir():
        return []
    from . import mouthset  # noqa: PLC0415

    notes: list[str] = []
    safe: dict[str, Image.Image] | None = None
    for path in sorted(mouths.glob("*.png")):
        code = path.stem
        floor = VISEME_CAV_MIN.get(code)
        if floor is None or floor <= 0:
            continue
        with Image.open(path) as image:
            cavity = sprite_cavity(image.convert("RGBA"))
        if cavity + 1e-6 >= floor:
            continue
        if safe is None:
            safe = mouthset.draw_set(96, _SAFE_COLOURS)
        sprite = safe.get(code)
        if sprite is None:
            continue
        sprite.save(path)
        notes.append(f"{view_dir.name} {code}: cavity {cavity:.2f} replaced with a safe mouth")
    return notes


def rest_blank_has_ink(image: Image.Image, box: tuple[int, int, int, int]) -> bool:
    """A rest frame whose blank skin already contains a mouth."""
    from . import qa as qa_mod  # noqa: PLC0415

    return qa_mod.blank_area_has_ink(image, box)


def lids_on_iris(lid_box: tuple[int, int, int, int], iris: tuple[int, int], tol: int = 4) -> bool:
    """Lids within ``tol`` px of the iris centre."""
    from . import qa as qa_mod  # noqa: PLC0415

    return qa_mod.lids_within_iris(lid_box, iris, tol)


def feature_positions(boxes: dict[str, Any], irises: list[Any] | None = None, *, tol: int = 6) -> dict[str, Any]:
    """Mouth inside the face under the nose, eyes within ``tol`` px of the irises."""
    from . import qa as qa_mod  # noqa: PLC0415

    return qa_mod.features_on_face(boxes, irises, tol=tol)


def check(mp4: Path, trace: dict[str, Any]) -> dict[str, Any]:
    placed = feature_positions(trace["boxes"], trace.get("irises")) if trace.get("boxes") else None
    if placed is not None and not placed["pass"]:
        return {
            "frames": len(trace.get("frames") or []),
            "mouthsMeasured": 0,
            "badFrames": 1,
            "pass": False,
            "maxOpen": 0.0,
            "examples": [(0, placed.get("note") or "features off the face")],
        }
    w, h = trace["size"]
    bad: list[tuple[int, str]] = []
    measured, worst = 0, 0.0
    opens: dict[int, dict[int, float]] = {}
    for i, img in enumerate(_frames(mp4, w, h)):
        if i >= len(trace["frames"]):
            break
        f = trace["frames"][i]
        if f["takes"] > 1 and f["blur"] == 0:
            bad.append((i, "dissolve: two framings blended"))
        for m in f["mouths"]:
            who = f"char {m['char']} {m['view']} {m['v']}"
            if m["layers"] != 1:
                bad.append((i, f"{who}: {m['layers']} mouth layers"))
            ref = m.get("ref")
            tiny = m["v"][:1] in "XA" and m["box"][2] - m["box"][0] < MIN_QA_REST_W
            r = measure(img, m["box"]) if f["blur"] == 0 and not tiny else None
            if not r or not ref:
                continue
            measured += 1
            worst = max(worst, r["cav"])
            opens.setdefault(i, {})[m["char"]] = max(r["cav"], opens.get(i, {}).get(m["char"], 0.0))
            # The absolute ceiling wins even when an intrinsically tall sprite
            # was supplied.  Otherwise the reference itself could raise the
            # limit and let the old 1.07-height O mouth pass.
            if r["cav"] > CAV_ABS:
                bad.append((i, f"{who}: open {r['cav']:.2f} > {CAV_ABS:.2f}"))
            elif r["cav"] > CAV_GROW * ref["cav"] + CAV_SLACK:
                bad.append((i, f"{who}: open {r['cav']:.2f} vs drawn {ref['cav']:.2f}"))
            if r["blobs"] >= 2 and r["blobs"] > ref["blobs"]:
                bad.append((i, f"{who}: {r['blobs']} stacked mouths vs drawn {ref['blobs']}"))
    speaking = [sp for sp in trace.get("speech") or [] if sp["end"] - sp["start"] >= SPEECH_MIN_S]
    if speaking and measured and worst <= 0.0:
        bad.append((0, f"maxOpen 0 over {measured} measured mouths although {len(speaking)} lines are voiced"))
    bad += speech_check(trace, opens)
    face_bad, face_report = facial_check(trace)
    bad += face_bad
    return {"frames": len(trace["frames"]), "mouthsMeasured": measured, "badFrames": len({b[0] for b in bad}),
            "pass": not bad, "maxOpen": round(worst, 3), "examples": bad[:12], **face_report}


if __name__ == "__main__":  # python -m products.animind_core.frameqa OUT.mp4 [frames.json]
    video = Path(sys.argv[1])
    tr = Path(sys.argv[2]) if len(sys.argv) > 2 else video.with_suffix(".frames.json")
    out = check(video, json.loads(tr.read_text()))
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0 if out["pass"] else 1)
