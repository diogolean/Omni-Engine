# -*- coding: utf-8 -*-
"""stills steps moved out of main.py. Bodies are unchanged."""
from __future__ import annotations

from engine.runtime import (
    Any,
    CostTracker,
    PageContext,
    Path,
    _MIN_CACHED_STILL_BYTES,
    os,
    outputs_root,
    shutil,
)
from engine.log import _LOG
def _export_scene_assets_sequential(
    image_paths: "list[Path | str]",
    assets_dir: "Path | str",
) -> list[Path]:
    """
    Copy scene images into ``outputs/<page>/assets/<episode_id>/`` as
    ``scene_01.png``, ``scene_02.png``, … Creating the directory when missing.
    """
    out_dir = Path(assets_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exported: list[Path] = []
    for i, src in enumerate(image_paths, start=1):
        src_p = Path(src)
        if not src_p.is_file():
            _LOG.warning(
                "Scene export skipped | missing source for scene_%02d: %s",
                i, src_p,
            )
            continue
        suffix = src_p.suffix.lower() if src_p.suffix else ".png"
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = ".png"
        dest = out_dir / f"scene_{i:02d}{suffix}"
        try:
            if dest.resolve() != src_p.resolve():
                shutil.copy2(src_p, dest)
            else:
                # Already the canonical scene path
                pass
            if dest.is_file():
                exported.append(dest.resolve())
        except OSError as exc:
            _LOG.warning("Scene export failed for scene_%02d (%s): %s", i, src_p, exc)
    return exported
def _ensure_sequence_image(
    img_path: "Path | str | None",
    *,
    fallback: "Path | str | None",
    target_path: "Path | None" = None,
) -> Path:
    """
    Guarantee a readable act image for MoviePy/PIL.

    If *img_path* is missing/invalid, ``shutil.copy2`` the previous valid
    *fallback* (Act 1 / base) into *target_path*. Never raises
    ``FileNotFoundError`` — last resort is a solid-color placeholder so
    API depletion never crashes compilation.
    """
    from PIL import Image as _PILImage

    fb = Path(fallback) if fallback else None
    candidate = Path(img_path) if img_path else None

    if candidate is not None and candidate.is_file() and os.path.exists(candidate):
        return candidate.resolve()

    dest = Path(target_path) if target_path is not None else (
        candidate if candidate is not None else (
            (fb.parent / f"{fb.stem}_fallback{fb.suffix}") if fb is not None
            else outputs_root() / "seq_act_placeholder.png"
        )
    )
    os.makedirs(os.path.dirname(str(dest)) or ".", exist_ok=True)

    if fb is not None and fb.is_file() and os.path.exists(fb):
        try:
            if dest.resolve() != fb.resolve():
                shutil.copy2(fb, dest)
                _LOG.warning(
                    "Act image missing (%s) — copied fallback %s → %s",
                    candidate, fb.name, dest,
                )
            if dest.is_file() and os.path.exists(dest):
                return dest.resolve()
        except OSError as exc:
            _LOG.warning("Fallback copy failed (%s) — using prior image path %s", exc, fb)
            return fb.resolve()

    # Blinded last resort — solid frame so MoviePy always completes
    try:
        _PILImage.new("RGB", (1080, 1920), (10, 10, 14)).save(str(dest))
        _LOG.warning("Blinded placeholder act image written → %s", dest)
        return dest.resolve()
    except Exception as exc:  # noqa: BLE001
        _LOG.error("Could not write placeholder act image (%s)", exc)
        # Absolute last ditch: reuse any existing path we still hold
        if fb is not None and fb.is_file():
            return fb.resolve()
        raise RuntimeError(f"Unable to guarantee sequence act image at {dest}") from exc
def _is_valid_cached_still(path: Path) -> bool:
    """True when *path* looks like a real generated PNG/JPEG, not an empty stub."""
    try:
        p = Path(path)
        if not p.is_file():
            return False
        if p.stat().st_size < _MIN_CACHED_STILL_BYTES:
            return False
        return p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    except OSError:
        return False
def _sequence_cache_search_roots(
    *,
    work_dir: "Path | None" = None,
    extra_dirs: "list[Path] | None" = None,
) -> list[Path]:
    """Only the current work dir, its episode folder, and explicit extras.

    Never crawl sibling ``ep_*`` folders — those belong to other topics.
    """
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(candidate: "Path | None") -> None:
        if candidate is None:
            return
        try:
            resolved = Path(candidate).resolve()
            key = str(resolved)
        except OSError:
            resolved = Path(candidate)
            key = str(resolved)
        if key in seen or not resolved.is_dir():
            return
        seen.add(key)
        roots.append(resolved)

    _add(work_dir)
    if work_dir is not None:
        _add(Path(work_dir).parent)
    for extra in extra_dirs or []:
        _add(extra)
    return roots
def _score_cached_still(path: Path) -> tuple:
    name = path.name.lower()
    fallback_pen = 1 if "fallback" in name or "placeholder" in name else 0
    regen_pen = 1 if "regen" in name else 0
    try:
        mtime = -float(path.stat().st_mtime)
    except OSError:
        mtime = 0.0
    return (fallback_pen, regen_pen, mtime)
def _stem_act_globs(stem: str, act_num: int) -> list[str]:
    """Filename globs that belong to *this* reel stem only."""
    s = (stem or "").strip()
    if not s or act_num < 1:
        return []
    nn = f"{act_num:02d}"
    return [f"{s}_act{nn}*.png", f"{s}_act{nn}*.jpg", f"{s}_act{nn}*.webp"]
def _find_cached_sequence_still(
    act_num: int,
    *,
    work_dir: "Path | None" = None,
    stem: str = "",
    extra_dirs: "list[Path] | None" = None,
) -> "Path | None":
    """Return a still for 1-based *act_num* that belongs to *stem*, or None.

    Refuses generic ``scene_XX.png`` / ``*_actNN*`` from other episodes so a
    new topic cannot inherit another reel's frames.
    """
    patterns = _stem_act_globs(stem, act_num)
    if not patterns:
        return None
    hits: list[Path] = []
    for root in _sequence_cache_search_roots(
        work_dir=work_dir, extra_dirs=extra_dirs,
    ):
        for pat in patterns:
            try:
                hits.extend(p for p in root.glob(pat) if _is_valid_cached_still(p))
            except OSError:
                continue
    if not hits:
        return None
    uniq: dict[str, Path] = {}
    for p in hits:
        try:
            uniq[str(p.resolve())] = p.resolve()
        except OSError:
            uniq[str(p)] = p
    return sorted(uniq.values(), key=_score_cached_still)[0]
def _collect_cached_act_stills(
    n_acts: int,
    *,
    start: int = 0,
    work_dir: "Path | None" = None,
    stem: str = "",
    extra_dirs: "list[Path] | None" = None,
) -> dict[int, Path]:
    """Map 0-based act index → existing still for this stem. Partial hits OK."""
    found: dict[int, Path] = {}
    for act_i in range(start, max(0, int(n_acts))):
        hit = _find_cached_sequence_still(
            act_i + 1,
            work_dir=work_dir,
            stem=stem,
            extra_dirs=extra_dirs,
        )
        if hit is not None:
            found[act_i] = hit
    return found
def _materialize_cached_still(src: Path, dest: Path) -> Path:
    dest = Path(dest)
    src = Path(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dest.resolve() != src.resolve():
            shutil.copy2(src, dest)
        return dest.resolve()
    except OSError:
        return src.resolve()
def _apply_still_cache_to_jobs(
    *,
    jobs: dict[int, Any],
    meta: dict[int, dict[str, Any]],
    n_acts: int,
    start: int,
    work_dir: Path,
    stem: str,
    extra_dirs: "list[Path] | None" = None,
    spoken_by_act: "dict[int, str] | None" = None,
) -> int:
    """Drop cached acts from *jobs* and stash materialized paths on *meta*.

    When *spoken_by_act* is set, a file is reused only if a sidecar says it
    already passed QA for that exact spoken line. Older topic-slug stills
    have no sidecar and are generated again.
    """
    cached = _collect_cached_act_stills(
        n_acts,
        start=start,
        work_dir=work_dir,
        stem=stem,
        extra_dirs=extra_dirs,
    )
    reused = 0
    for act_i, src in cached.items():
        if spoken_by_act is not None:
            from modules.reel_grounding.still_cache import cache_matches_spoken

            if not cache_matches_spoken(src, spoken_by_act.get(act_i, "")):
                continue
        dest = Path(work_dir) / f"{stem}_act{act_i + 1:02d}.png"
        materialized = _materialize_cached_still(src, dest)
        meta.setdefault(act_i, {})["cached_path"] = materialized
        jobs.pop(act_i, None)
        reused += 1
    if reused:
        _LOG.info(
            "IMAGE CACHE HIT | reused %d/%d stills — skipped image API for cached acts",
            reused, n_acts,
        )
    else:
        _LOG.info("IMAGE CACHE MISS | generating %d stills", max(0, n_acts - start))
    return reused
def _track_adapter_image(
    cost_tracker: "CostTracker | None",
    adapter: "Any",
) -> int:
    """
    Record one generated image at the official FLUX Schnell rate ($0.003).
    Remote GPU seconds are logged separately and excluded from pipeline totals.
    """
    # Official formula is $0.003 per generated image, not per retry hop.
    n = 1
    if cost_tracker is None:
        return n
    _cost_key = getattr(adapter, "last_cost_key", None) or "image_flux_schnell"
    _gpu_s = float(getattr(adapter, "last_gpu_seconds", 0) or 0)
    if _gpu_s > 0 or str(_cost_key).startswith("image_remote"):
        try:
            from core.remote_gpu_manager import get_manager  # noqa: PLC0415

            mode = str(getattr(get_manager().client, "mode", None) or "comfyui")
        except Exception:
            mode = "comfyui"
        if _gpu_s <= 0:
            try:
                _gpu_s = float(get_manager().client.last_job_seconds or 0)
            except Exception:
                _gpu_s = 0.0
        if _gpu_s > 0:
            cost_tracker.track_gpu_seconds(_gpu_s, mode=mode, jobs=n)
    _img_key = _cost_key if str(_cost_key).startswith("image_") else "image_flux_schnell"
    if str(_img_key).startswith("image_remote"):
        _img_key = "image_flux_schnell"
    cost_tracker.track_image(model_key=_img_key, count=n)
    _LOG.debug("CostTracker | image hit key=%s n=%d", _img_key, n)
    return n
def _track_remote_gpu_batch(
    cost_tracker: "CostTracker | None",
    *,
    seconds_before: float,
    jobs: int,
) -> float:
    """Bill GPU-seconds accumulated on the shared remote client since *seconds_before*."""
    if cost_tracker is None:
        return 0.0
    try:
        from core.remote_gpu_manager import get_manager  # noqa: PLC0415

        client = get_manager().client
        after = float(getattr(client, "total_gpu_seconds", 0) or 0)
        delta = max(0.0, after - float(seconds_before or 0))
        if delta <= 0:
            return 0.0
        cost_tracker.track_gpu_seconds(
            delta, mode=str(getattr(client, "mode", "comfyui")), jobs=max(1, jobs),
        )
        return delta
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("GPU batch cost skipped: %s", exc)
        return 0.0
def _image_act_max_workers(default_legacy: int = 3) -> int:
    """
    Concurrency for sequence-reel act image generation.

    - remote_gpu: ``remote_gpu_max_parallel()`` (shared with audio/video batches).
    - Together legacy: small pool; adapter semaphore still serializes HTTP.
    """
    try:
        from core.remote_gpu_manager import (  # noqa: PLC0415
            is_remote_gpu_enabled,
            remote_gpu_max_parallel,
        )

        if is_remote_gpu_enabled("image"):
            n = remote_gpu_max_parallel()
            _LOG.info(
                "Act image concurrency | remote_gpu max_workers=%d "
                "(REMOTE_GPU_MAX_PARALLEL)",
                n,
            )
            return n
    except Exception:
        pass
    return max(1, int(default_legacy))
def _run_acts_parallel(
    jobs: "dict[int, Any]",
    *,
    max_workers: int | None = None,
) -> "dict[int, Any]":
    """
    Concurrent act jobs — delegates to ``remote_gpu_manager.run_parallel_jobs``.

    Together AI (default image path): one HTTP call per prompt; Together does
    not accept a list of different prompts in a single request, so this is the
    batch. RunPod Flux (IMAGE_PROVIDER=remote_gpu): still one Comfy job per
    still — images no longer share the video worker pool when Together is on.
    """
    from core.remote_gpu_manager import run_parallel_jobs  # noqa: PLC0415

    if max_workers is None:
        max_workers = _image_act_max_workers()
    return run_parallel_jobs(jobs, max_workers=max_workers)
def _maybe_convert_to_video(
    image_path: Path,
    *,
    duration: int = 7,
    page_ctx: PageContext | None = None,
) -> str:
    """
    Attempt to convert a generated image into a 7-second Ken Burns zoom loop.
    Returns the video path string, or empty string if unavailable / failed.
    Only called when --format HYBRID_VIDEO is active.
    """
    if page_ctx is None or not page_ctx.is_hybrid_video:
        return ""
    try:
        from agents.media.video_converter import make_zoom_loop
        video_path = make_zoom_loop(image_path, duration_seconds=duration)
        return str(video_path)
    except ImportError:
        _LOG.warning("video_converter not available; HYBRID_VIDEO skipped.")
        return ""
    except Exception as vexc:  # noqa: BLE001
        _LOG.warning("HYBRID_VIDEO conversion failed: %s", vexc, exc_info=True)
        return ""


