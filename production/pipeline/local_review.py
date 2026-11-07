# -*- coding: utf-8 -*-
"""One Ancient Knowledge reel, with distribution forced off.

``--local-review`` is the switch. It is not an environment variable.
``.env`` loads with ``override=True``, so a process env value for
``ENABLE_YOUTUBE_UPLOAD`` is not a guarantee. This module is what the
YouTube, B2, ImgBB, and Post Planner branches consult.

The output stays under the existing ``OUTPUT_PATH/<page>/`` tree, in a new
``clips/local_review/<stamp>/`` directory. It does not write an approval
file and it does not replace a protected path.
"""
from __future__ import annotations

import argparse
import json
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from production.media.text_utils import subject_slug

DOGON_STEM = "the_sirius_mystery_dogon_4ed43c"
DOGON_MP4_NAME = "reel_did_an_african_tribe_map_an_invi_v01.mp4"
DOGON_POST_TYPE = "ECONOMIC_REEL"


def dogon_page() -> str:
    """The documentary channel. Resolved from the flag, not a hardcoded id."""
    from production.pipeline.channel_flags import sole_page

    return sole_page("DOCUMENTARY_PIPELINE")

_CURRENT: ContextVar["LocalReview | None"] = ContextVar("omni_local_review", default=None)


@dataclass(frozen=True)
class LocalReview:
    stamp: str
    root: Path
    protected: tuple[Path, ...]
    expect_stem: str


def current() -> LocalReview | None:
    return _CURRENT.get()


def active() -> bool:
    return _CURRENT.get() is not None


def distribution_enabled() -> bool:
    """False for the whole process once a local review has begun."""
    return not active()


def youtube_publish_enabled(cli_flag: bool, env_flag: bool) -> bool:
    """YouTube runs only when a flag or the env asks, and review mode is off."""
    if active():
        return False
    return bool(cli_flag) or bool(env_flag)


def isolate_dir(legacy: Path, relative: str) -> Path:
    """Legacy path, or a directory inside the review root when review is on."""
    state = current()
    if state is None:
        return legacy
    return state.root / relative


def begin(page_outputs: Path, *, expect_stem: str, protected: list[Path]) -> LocalReview:
    """Create the unique review directory and arm the process gate."""
    if current() is not None:
        raise SystemExit("[local-review] already active in this process")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(page_outputs) / "clips" / "local_review" / stamp
    if root.exists():
        raise SystemExit(f"[local-review] output already exists: {root}")
    root.mkdir(parents=True)
    state = LocalReview(
        stamp=stamp,
        root=root,
        protected=tuple(Path(path) for path in protected),
        expect_stem=expect_stem,
    )
    _CURRENT.set(state)
    print(f"[local-review] output={root}")
    print(
        "[local-review] distribution off: YouTube, B2, ImgBB, Post Planner, "
        "content library, asset library"
    )
    print("[local-review] no approval file will be written")
    return state


def end() -> None:
    _CURRENT.set(None)


def refuse_output(target: Path) -> None:
    """Refuse a path outside the review root, a protected file, or any existing file."""
    state = current()
    if state is None:
        return
    resolved = target.resolve()
    root = state.root.resolve()
    if resolved != root and root not in resolved.parents:
        raise SystemExit(f"[local-review] refusing output outside {root}: {resolved}")
    for protected in state.protected:
        try:
            protected_resolved = protected.resolve()
        except OSError:
            protected_resolved = protected
        if resolved == protected_resolved:
            raise SystemExit(
                f"[local-review] refusing to overwrite protected file {protected}"
            )
    if resolved.exists():
        raise SystemExit(f"[local-review] refusing to overwrite {resolved}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def validate_args(args: argparse.Namespace) -> None:
    """Reject a local-review invocation before any upload or render. No-op otherwise."""
    if not getattr(args, "local_review", False):
        return
    topic = str(getattr(args, "topic", "") or "").strip()
    expect = str(getattr(args, "expect_stem", "") or "").strip()
    post_type = str(getattr(args, "post_type", "") or "").upper()
    page = str(getattr(args, "page", "") or "").strip().lower()
    _require(int(getattr(args, "quantity", 1) or 1) == 1, "[local-review] requires --quantity 1")
    _require(bool(topic), "[local-review] requires a topic argument")
    _require(bool(expect), "[local-review] requires --expect-stem")
    _require(
        subject_slug(topic) == expect,
        f"[local-review] topic slug {subject_slug(topic)!r} is not {expect!r}",
    )
    _require(page == dogon_page(), "[local-review] requires the documentary channel")
    _require(post_type == DOGON_POST_TYPE, "[local-review] requires --post-type ECONOMIC_REEL")
    _require(not getattr(args, "publish_youtube", False), "[local-review] refuses --publish-youtube")
    _require(not getattr(args, "schedule_uploads", False), "[local-review] refuses --schedule-uploads")
    _require(
        not getattr(args, "resume_youtube_queue", False),
        "[local-review] refuses --resume-youtube-queue",
    )


def begin_from_args(args: argparse.Namespace, page_outputs: Path) -> None:
    """Arm the gate after the page output root is bound. No-op otherwise."""
    if not getattr(args, "local_review", False):
        return
    validate_args(args)
    expect = str(getattr(args, "expect_stem", "") or "").strip()
    protected = [Path(raw) for raw in (getattr(args, "protect_path", None) or []) if str(raw).strip()]
    begin(page_outputs, expect_stem=expect, protected=protected)


def dogon_argv(topic: str, *protected: Path | str) -> list[str]:
    """Argument vector for one Dogon local review. Distribution flags are absent."""
    slug = subject_slug(topic)
    if slug != DOGON_STEM:
        raise SystemExit(
            f"[local-review] topic slug {slug!r} is not {DOGON_STEM!r}"
        )
    argv = [
        topic,
        "--channel",
        dogon_page(),
        "--post-type",
        DOGON_POST_TYPE,
        "--quantity",
        "1",
        "--local-review",
        "--expect-stem",
        DOGON_STEM,
    ]
    for path in protected:
        argv.extend(["--protect-path", str(path)])
    forbidden = {
        "--publish-youtube",
        "--upload-youtube",
        "--schedule-uploads",
        "--resume-youtube-queue",
        "--dry-run",
    }
    found = forbidden.intersection(argv)
    if found:
        raise SystemExit(f"[local-review] argv must not contain {sorted(found)}")
    return argv


def _qa_record(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"[local-review] QA json is not a file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[local-review] QA json is not readable: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("[local-review] QA json must be an object")
    return data


def topic_from_qa(data: dict[str, Any]) -> str:
    raw = str(data.get("topic") or data.get("resolved_subject") or "").strip()
    if not raw:
        return DOGON_STEM
    slug = subject_slug(raw)
    if slug != DOGON_STEM:
        raise SystemExit(
            f"[local-review] QA topic slug {slug!r} is not {DOGON_STEM!r}. "
            "Refusing to render a different episode."
        )
    return raw


def protected_video_from_qa(data: dict[str, Any], qa_path: Path) -> Path:
    raw = str(data.get("video_path") or "").strip()
    if not raw:
        raise SystemExit("[local-review] QA json has no video_path")
    video = Path(raw)
    if not video.is_file():
        raise SystemExit(f"[local-review] protected video is not a file: {video}")
    if video.name != DOGON_MP4_NAME:
        raise SystemExit(
            f"[local-review] protected video is {video.name!r}, expected {DOGON_MP4_NAME!r}"
        )
    if video.resolve() == qa_path.resolve():
        raise SystemExit("[local-review] video_path points at the QA json")
    return video


def render_argv_from_qa(qa_path: Path) -> list[str]:
    data = _qa_record(qa_path)
    topic = topic_from_qa(data)
    video = protected_video_from_qa(data, qa_path)
    return dogon_argv(topic, video, qa_path)


def sample_video(
    video: Path,
    dest: Path,
    *,
    duration_s: float,
    grab: Callable[[float, Path], Path | None],
) -> dict[str, Any]:
    """Technical frames only. Does not upload and does not write an approval."""
    video = Path(video)
    dest = Path(dest)
    if not video.is_file():
        raise SystemExit(f"[local-review] video is not a file: {video}")
    if dest.resolve() == video.resolve():
        raise SystemExit("[local-review] sample manifest must not replace the video")
    if dest.exists():
        raise SystemExit(f"[local-review] refusing to overwrite {dest}")
    from production.qa.mp4_gate import frame_fault

    times = _sample_times(duration_s)
    frames: list[dict[str, Any]] = []
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent / f"{dest.stem}_frames"
    work.mkdir(parents=True, exist_ok=True)
    for index, at in enumerate(times):
        shot = work / f"sample_{index:02d}.jpg"
        written = grab(at, shot)
        fault = "frame_extract_failed" if written is None else (frame_fault(written) or "")
        frames.append(
            {
                "at_s": round(at, 3),
                "fault": fault,
                "frame_path": "" if written is None else str(written),
            }
        )
    manifest = {
        "schema": 1,
        "video_path": str(video),
        "duration_s": duration_s,
        "frames": frames,
        "semantic_status": "needs_human_review",
        "certifies_truth": False,
        "distribution": "off",
        "approval_written": False,
        "note": (
            "Opening and timeline samples for flat, black, or unreadable frames. "
            "This does not judge narration, facts, or captions."
        ),
    }
    dest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def sample_dest_for(video: Path) -> Path:
    return video.parent / "local_review_samples" / f"{video.stem}.sample.json"


def _sample_times(duration_s: float) -> list[float]:
    if duration_s <= 0.2:
        return [0.0]
    times = [0.15]
    step = max(duration_s / 15.0, 5.0)
    at = step
    while at < duration_s - 0.25 and len(times) < 16:
        times.append(at)
        at += step
    if duration_s - 0.25 not in times and len(times) < 16:
        times.append(max(0.15, duration_s - 0.25))
    return times


def _ffmpeg_duration(video: Path) -> float:
    import shutil
    import subprocess

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise SystemExit("[local-review] ffprobe is not on PATH")
    proc = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(video),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    raw = (proc.stdout or "").strip()
    try:
        return float(raw)
    except ValueError as exc:
        raise SystemExit("[local-review] ffprobe did not return a duration") from exc


def _ffmpeg_grab(video: Path, at: float, dest: Path) -> Path | None:
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg, "-y", "-v", "error",
            "-ss", f"{at:.3f}",
            "-i", str(video),
            "-frames:v", "1",
            str(dest),
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0 or not dest.is_file():
        return None
    return dest


def sample_cli(video: Path, dest: Path | None = None) -> dict[str, Any]:
    target = dest or sample_dest_for(video)
    duration = _ffmpeg_duration(video)

    def grab(at: float, path: Path) -> Path | None:
        return _ffmpeg_grab(video, at, path)

    return sample_video(video, target, duration_s=duration, grab=grab)


def render_from_qa(qa_path: Path) -> None:
    """Build the reviewed argv and hand it to the pipeline CLI."""
    from production.pipeline.cli_parser import parse_cli_args
    from production.pipeline.cli_run import run_parsed_cli

    argv = render_argv_from_qa(qa_path)
    print("[local-review] invocation:")
    print(" ".join(argv))
    run_parsed_cli(parse_cli_args(argv))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ancient Knowledge one-video local review. Distribution stays off.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render", help="Render one Dogon reel from its QA json.")
    render.add_argument("--qa", type=Path, required=True)
    sample = sub.add_parser("sample", help="Sample an existing mp4. No upload, no approval.")
    sample.add_argument("--video", type=Path, required=True)
    sample.add_argument("--dest", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "render":
        render_from_qa(args.qa)
        return 0
    manifest = sample_cli(args.video, args.dest)
    print(f"[local-review] sample frames={len(manifest['frames'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
