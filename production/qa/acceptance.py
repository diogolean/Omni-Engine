# -*- coding: utf-8 -*-
"""One-video acceptance. Automatic checks do not certify truth.

A reel is not ready to distribute because a QA report says pass. The Dogon
seal said pass while seven beats were ``reuse=fallback`` with score 0 and a
rejection reason. This module keeps that from shipping, and it keeps a
human approval tied to the file that was watched. A new render is a new hash.
YouTube cannot replace the media of an existing video id.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REJECTION_MARKERS = (
    "documentary_fail",
    "non_photoreal",
    "anatomy",
    "reject",
    "blur",
    "duplicate",
    "off-topic",
    "unreadable",
    "missing_still",
    "static_fallback",
    "held_duplicate",
    "local:",
)

# These block distribution even if a person liked an earlier cut.
# ``needs_human_review`` is the one gap a person can acknowledge: the
# semantic judge did not return a verdict. Acknowledging it does not
# claim the picture is true.
_HARD_BLOCKERS = frozenset({
    "static_fallback",
    "held_duplicate",
    "rejection_marked_passed",
    "unreadable_source",
    "missing_still",
    "repeated_still",
    "repeated_frame",
    "frame_flat",
    "frame_black",
    "frame_glitch",
    "frame_overlay",
    "audio_missing",
    "audio_too_quiet",
    "loudness_unmeasured",
    "duration_below_minimum",
    "narration_truncated",
    "captions_missing",
    "captions_late",
    "captions_gap",
    "no_frame_samples",
    "frame_subtitle_mismatch",
    "frame_extract_failed",
    "empty_subtitle",
    "beat_qa_failed",
    "missing_video",
})


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _readable_image(path: Path) -> bool:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:  # noqa: BLE001
        return False


def _is_rejection(reason: str) -> bool:
    text = reason or ""
    return any(marker in text for marker in _REJECTION_MARKERS)


def beat_release_blockers(beats: list[dict[str, Any]] | None) -> list[str]:
    """Reasons a beat list must not become ``verdict=pass``.

    ``passed=True`` is ignored when the row still carries a rejection or
    a fallback/hold marker. That is the Dogon shape.
    """
    reasons: list[str] = []
    seen_hash: dict[str, Any] = {}

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    for beat in beats or []:
        if beat.get("is_cta"):
            continue
        reuse = str(beat.get("reuse") or "")
        if reuse == "fallback":
            add("static_fallback")
        elif reuse == "hold_previous":
            add("held_duplicate")
        reason = str(beat.get("reason") or "")
        if beat.get("passed") is True and _is_rejection(reason):
            add("rejection_marked_passed")
        raw = str(beat.get("image_path") or "").strip()
        if not raw:
            continue
        path = Path(raw)
        if not path.is_file():
            add("missing_still")
            continue
        if not _readable_image(path):
            add("unreadable_source")
            continue
        digest = file_sha256(path)
        prior = seen_hash.get(digest)
        if prior is not None and prior != beat.get("index"):
            add("repeated_still")
        else:
            seen_hash.setdefault(digest, beat.get("index"))
    return reasons


def approval_path(video: Path | str) -> Path:
    path = Path(video)
    return path.with_name(path.stem + ".approval.json")


def read_approval(video: Path | str) -> dict[str, Any] | None:
    path = approval_path(video)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_approval(
    video: Path | str,
    *,
    approver: str,
    qa_report: Path | str | None = None,
    acknowledged: list[str] | None = None,
) -> dict[str, Any]:
    """Record that a person accepted this exact file.

    The hash is of the bytes on disk. Replacing those bytes makes the
    record stale. This does not upload, and it does not certify that the
    pictures or the narration are true.
    """
    media = Path(video)
    if not media.is_file():
        raise FileNotFoundError(str(media))
    report = Path(qa_report) if qa_report else None
    record = {
        "schema": 1,
        "video_sha256": file_sha256(media),
        "qa_report_sha256": file_sha256(report) if report and report.is_file() else "",
        "video_path": str(media),
        "approver": (approver or "").strip(),
        "approved_utc": datetime.now(timezone.utc).isoformat(),
        "acknowledged": [str(item) for item in (acknowledged or []) if str(item).strip()],
        "note": (
            "Human approval of these exact bytes. A new encode invalidates it. "
            "Automatic checks do not establish semantic relevance or factual truth."
        ),
    }
    if not record["approver"]:
        raise ValueError("approver is required")
    dest = approval_path(media)
    dest.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def approval_state(
    video: Path | str,
    *,
    qa_report: Path | str | None = None,
) -> str:
    """``missing``, ``stale``, or ``current``."""
    media = Path(video)
    if not media.is_file():
        return "missing"
    record = read_approval(media)
    if not record:
        return "missing"
    if str(record.get("video_sha256") or "") != file_sha256(media):
        return "stale"
    report = Path(qa_report) if qa_report else None
    if report and report.is_file():
        if str(record.get("qa_report_sha256") or "") != file_sha256(report):
            return "stale"
    return "current"


def _load_report(path: str) -> dict[str, Any] | None:
    if not path:
        return None
    file = Path(path)
    if not file.is_file():
        return None
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def distribution_ready(row: dict[str, Any] | None) -> bool:
    """Upload gate. QA pass is not enough, and a hard defect cannot be waived.

    Non-reel rows keep the old QA rule. A reel needs a current approval
    bound to the media hash. ``needs_human_review`` alone (the judge did
    not answer) can be acknowledged in that approval. Flat frames, fallback
    cards, rejections, missing audio, and repeated stills cannot.
    """
    from production.qa.publish import is_reel_post_type, row_publishable

    data = dict(row or {})
    if not is_reel_post_type(str(data.get("post_type") or "")):
        return row_publishable(data)
    report = _load_report(str(data.get("qa_report_path") or ""))
    verdict = str(
        (report or {}).get("verdict") or data.get("qa_verdict") or ""
    ).strip().lower()
    blockers = list((report or {}).get("blockers") or data.get("qa_blockers") or [])
    if not blockers and report:
        blocked = str(report.get("blocked_reason") or "")
        blockers = [part.strip() for part in blocked.split(";") if part.strip()]
    hard = [item for item in blockers if item in _HARD_BLOCKERS or item.startswith("frame_")]
    if hard:
        return False
    video = str(data.get("video_path") or (report or {}).get("video_path") or "")
    report_path = str(data.get("qa_report_path") or "")
    state = approval_state(video, qa_report=report_path or None)
    if state != "current":
        return False
    record = read_approval(video) or {}
    acknowledged = {str(item) for item in (record.get("acknowledged") or [])}
    review_only = [item for item in blockers if item == "needs_human_review"]
    if verdict == "pass" and not hard:
        return True
    if review_only and not hard and "needs_human_review" in acknowledged:
        return True
    return False


def write_review_manifest(
    report: dict[str, Any],
    *,
    video_path: Path | str | None,
    output_dir: Path | str | None,
    stem: str,
) -> str:
    """JSON manifest beside the QA report. Does not modify the media file.

    A contact sheet is written only from frames the gate already extracted.
    Semantic and factual judgments are left for a person.
    """
    video = Path(video_path) if video_path else None
    manifest = {
        "schema": 1,
        "channel": report.get("channel") or "",
        "video_path": str(video) if video else "",
        "video_sha256": file_sha256(video) if video and video.is_file() else "",
        "technical_verdict": report.get("verdict") or "",
        "blockers": list(report.get("blockers") or []),
        "semantic_status": "needs_human_review",
        "certifies_truth": False,
        "semantic_note": (
            "Frame timing, flat-color, fallback, duplicate, audio, and caption-coverage "
            "checks do not establish that a picture matches the narration or that the "
            "script is factually right. A person has to watch the whole cut."
        ),
        "distribution_allowed": False,
        "beats": [
            {
                "index": beat.get("index"),
                "reuse": beat.get("reuse") or "",
                "score": beat.get("score"),
                "passed": beat.get("passed"),
                "reason": beat.get("reason") or "",
                "image_path": beat.get("image_path") or "",
            }
            for beat in (report.get("beats") or [])
            if isinstance(beat, dict)
        ],
        "frames": [
            {
                "at_s": frame.get("at_s"),
                "reason": frame.get("reason") or "",
                "passed": frame.get("passed"),
                "beat_index": frame.get("beat_index"),
                "frame_path": frame.get("frame_path") or "",
            }
            for frame in ((report.get("mp4") or {}).get("frames") or [])
            if isinstance(frame, dict)
        ],
    }
    sheet = _contact_sheet(manifest["frames"], video, stem)
    if sheet:
        manifest["contact_sheet"] = sheet
    targets: list[Path] = []
    if output_dir:
        targets.append(Path(output_dir) / f"{stem or 'reel'}.review.json")
    if video:
        targets.append(video.with_name(video.stem + ".review.json"))
    written = ""
    payload = json.dumps(manifest, indent=2) + "\n"
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        written = written or str(path)
    report["review_path"] = written
    report["semantic_status"] = "needs_human_review"
    report["certifies_truth"] = False
    return written


def _contact_sheet(
    frames: list[dict[str, Any]],
    video: Path | None,
    stem: str,
) -> str:
    thumbs: list[Any] = []
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return ""
    for row in frames:
        raw = str(row.get("frame_path") or "")
        if not raw or not Path(raw).is_file():
            continue
        try:
            with Image.open(raw) as opened:
                image = opened.convert("RGB")
        except Exception:  # noqa: BLE001
            continue
        image.thumbnail((160, 284))
        thumbs.append((row.get("at_s"), image))
        if len(thumbs) >= 16:
            break
    if not thumbs:
        return ""
    width = sum(image.width for _at, image in thumbs)
    height = max(image.height for _at, image in thumbs) + 24
    sheet = Image.new("RGB", (width, height), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    x = 0
    for at, image in thumbs:
        sheet.paste(image, (x, 0))
        draw.text((x + 4, image.height + 4), f"{float(at or 0):.1f}s", fill=(240, 220, 80))
        x += image.width
    dest_dir = video.parent if video else Path(".")
    dest = dest_dir / f"{stem or 'reel'}.contact.jpg"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(dest, format="JPEG", quality=80)
    except OSError:
        return ""
    return str(dest)
