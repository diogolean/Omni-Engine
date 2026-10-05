# -*- coding: utf-8 -*-
"""End-to-end pipeline: debate -> voice -> video.

This is the module's public façade and the only thing the parent engine needs to
import::

    from channels_config.aiwake import run_pipeline
    result = run_pipeline(topic="Is grief a slow update?", turns=4)

All wiring decisions (which observers attach, which engine speaks, whether video
is produced) are made here, so ``room.py`` and ``orchestrator.py`` stay free of
side-effect policy.
"""
from __future__ import annotations

import logging
import random
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

try:
    from .contracts import DebateTranscript, PostType
    from .media.audio import (
        CLAUDE_CANONICAL_VOICE,
        DEEPSEEK_CANONICAL_VOICE,
        GEMINI_CANONICAL_VOICE,
        LLAMA_CANONICAL_VOICE,
        build_engine,
    )
    from .personas import ORCHESTRATOR_ONLY_FAMILIES, TARGET_ONLY_FAMILIES
    from .memory import DebateMemory, script_fingerprint, scripts_overlap
    from .models.llm_factory import force_offline
    from .observers.core import (
        ConsoleObserver,
        MemoryObserver,
        MetricsObserver,
        TranscriptObserver,
        VoiceObserver,
    )
    from .orchestrator import Provocateur
    from .room import DebateRoom, RoomEvent
    from .settings import AiwakeSettings, load_settings, resolve_outputs_dir
    from .utils.event_bus import (
        ON_PIPELINE_FINISH,
        ON_PIPELINE_START,
        attach_optional_plugins,
        emit,
    )
except ImportError:  # pragma: no cover — standalone extraction
    from contracts import DebateTranscript, PostType  # type: ignore[no-redef]
    from media.audio import (  # type: ignore[no-redef]
        CLAUDE_CANONICAL_VOICE,
        DEEPSEEK_CANONICAL_VOICE,
        GEMINI_CANONICAL_VOICE,
        LLAMA_CANONICAL_VOICE,
        build_engine,
    )
    from personas import ORCHESTRATOR_ONLY_FAMILIES, TARGET_ONLY_FAMILIES  # type: ignore[no-redef]
    from memory import DebateMemory, script_fingerprint, scripts_overlap  # type: ignore[no-redef]
    from models.llm_factory import force_offline  # type: ignore[no-redef]
    from observers.core import (  # type: ignore[no-redef]
        ConsoleObserver,
        MemoryObserver,
        MetricsObserver,
        TranscriptObserver,
        VoiceObserver,
    )
    from orchestrator import Provocateur  # type: ignore[no-redef]
    from room import DebateRoom, RoomEvent  # type: ignore[no-redef]
    from settings import AiwakeSettings, load_settings, resolve_outputs_dir  # type: ignore[no-redef]
    from utils.event_bus import (  # type: ignore[no-redef]
        ON_PIPELINE_FINISH,
        ON_PIPELINE_START,
        attach_optional_plugins,
        emit,
    )

_LOG = logging.getLogger("aiwake.pipeline")

BATCH_MODEL_ROSTER: tuple[str, ...] = (
    "gpt4o",
    "claude-sonnet",
    "gemini-flash",
    "llama-70b",
    "deepseek-chat",
)
BATCH_PUPPET_BY_MODEL: dict[str, str] = {
    "gpt4o": "chatgpt_cyborg_v1",
    "claude-sonnet": "claude_cyborg_v1",
    "gemini-flash": "gemini_cyborg_v2",
    "llama-70b": "llama_cyborg_v2",
    "deepseek-chat": "deepseek_cyborg_v3",
}
_PUPPET_VOICE: dict[str, str] = {
    "chatgpt_cyborg_v1": "en-US-AndrewNeural",
    "claude_cyborg_v1": CLAUDE_CANONICAL_VOICE,
    "gemini_cyborg_v2": GEMINI_CANONICAL_VOICE,
    "llama_cyborg_v2": LLAMA_CANONICAL_VOICE,
    "deepseek_cyborg_v3": DEEPSEEK_CANONICAL_VOICE,
}
_PUPPET_ALIASES: dict[str, tuple[str, ...]] = {
    "chatgpt_cyborg_v1": ("gpt4o", "gpt-4o", "chatgpt"),
    "claude_cyborg_v1": ("claude", "claude-sonnet"),
    "gemini_cyborg_v2": ("gemini", "gemini-flash"),
    "llama_cyborg_v2": ("llama", "llama-70b"),
    "deepseek_cyborg_v3": ("deepseek", "deepseek-chat"),
}


def model_family(model: str | None) -> str:
    """Collapse an alias or provider slug onto a puppet family."""
    token = (model or "").strip().lower().replace("_", "-")
    if "llama" in token:
        return "llama"
    if "gemini" in token:
        return "gemini"
    if "deepseek" in token:
        return "deepseek"
    if "claude" in token:
        return "claude"
    if "gpt" in token or "chatgpt" in token or "openai" in token:
        return "gpt"
    return token


def matchup_is_legal(orchestrator: str | None, target: str | None) -> bool:
    """Gemini orchestrates only. Llama is interrogated only. No self-debates."""
    left = model_family(orchestrator)
    right = model_family(target)
    if not left or not right or left == right:
        return False
    if left in TARGET_ONLY_FAMILIES:
        return False
    if right in ORCHESTRATOR_ONLY_FAMILIES:
        return False
    return True


def random_matchup_schedule(
    quantity: int,
    *,
    seed: int | None = None,
) -> tuple[tuple[str, str], ...]:
    """Return shuffled legal pairings across the production model roster.

    DeepSeek is in the roster. Llama never orchestrates (facing-left art only)
    and Gemini is never interrogated (facing-right art only).
    """
    qty = max(0, int(quantity))
    rng = random.Random(seed)
    all_pairings = [
        (orchestrator, target)
        for orchestrator in BATCH_MODEL_ROSTER
        for target in BATCH_MODEL_ROSTER
        if matchup_is_legal(orchestrator, target)
    ]
    schedule: list[tuple[str, str]] = []
    while len(schedule) < qty:
        cycle = list(all_pairings)
        rng.shuffle(cycle)
        schedule.extend(cycle)
    return tuple(schedule[:qty])


@dataclass(slots=True)
class PipelineResult:
    """What a full run produced.

    Attributes:
        transcript: The debate record.
        video_path: Rendered MP4, or None when rendering was skipped or failed.
        exchanges: Completed provocation/rebuttal pairs.
        end_reason: ``complete``, ``aborted`` or ``interrupted``.
        audio_seconds: Total synthesised voice duration.
    """

    transcript: DebateTranscript
    video_path: Path | None
    exchanges: int
    end_reason: str
    audio_seconds: float
    dialogue_end_reason: str
    thumbnail_path: Path | None = None
    metadata_path: Path | None = None

    @property
    def succeeded(self) -> bool:
        return self.end_reason == "complete" and self.exchanges > 0


@dataclass(slots=True)
class BulkPipelineResult:
    """What a ``--quantity N`` run produced."""

    items: list[PipelineResult] = field(default_factory=list)
    requested: int = 0
    succeeded: int = 0
    skipped_duplicates: int = 0

    @property
    def complete(self) -> bool:
        return self.succeeded == self.requested and self.requested > 0


_BULK_SCRIPT_RETRIES = 3
_PROTOTYPE_NAMES = {
    "aiwake_full_battle_v2.mp4",
}


def archive_animation_prototypes(animation_dir: Path) -> list[Path]:
    """Move test-era animation artifacts out of the production namespace."""
    root = Path(animation_dir)
    archive = root / "test_archive"
    archive.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for item in tuple(root.iterdir()):
        if item == archive:
            continue
        name = item.name.lower()
        prototype = (
            name in _PROTOTYPE_NAMES
            or name.startswith(("test_avatar_battle", "proof_", "spoken_test_", "synthetic_test_"))
            or "_test_render_battle" in name
            or "_render." in name
            or "_render_" in name
        )
        if not prototype:
            continue
        destination = archive / item.name
        if destination.exists():
            suffix = int(time.time())
            destination = archive / f"{item.stem}_{suffix}{item.suffix}"
        shutil.move(str(item), str(destination))
        moved.append(destination)
    if moved:
        _LOG.info("archived %d prototype animation artifact(s) -> %s", len(moved), archive)
    return moved


def _transcript_script(result: PipelineResult) -> str:
    return result.transcript.to_script()


def _record_produced_script(memory: DebateMemory, script: str) -> None:
    if not script:
        return
    memory.note_script(script)
    memory.flush()


def _script_conflicts(script: str, seen_scripts: Sequence[str], seen_fps: set[str]) -> bool:
    fingerprint = script_fingerprint(script)
    if fingerprint and fingerprint in seen_fps:
        return True
    return any(scripts_overlap(script, prior) for prior in seen_scripts)


def _require_production_memory(topic: str | None) -> object:
    from .tools.supermemory_bridge import HISTORY_CONTAINER, get_bridge

    bridge = get_bridge()
    if not bridge.is_active:
        raise RuntimeError(
            "production animation requires Supermemory at localhost:6767; "
            "the self-healing startup hook could not make it ready"
        )
    _LOG.info(
        "Supermemory production gate ready at %s (container_tag=%s)",
        bridge.base_url,
        HISTORY_CONTAINER,
    )
    if topic and bridge.check_topic_similarity(topic):
        raise ValueError(f"Supermemory rejected repetitive production topic: {topic}")
    return bridge


def _persist_production_package(
    *,
    transcript: DebateTranscript,
    video_path: Path,
    media_dir: Path,
) -> None:
    from .settings import resolve_store_dir
    from .tools.backfill_metadata import (
        build_record,
        load_transcript_file,
        persist_record,
        probe_duration_s,
    )
    from .tools.post_planner import run_planner
    from .tools.supermemory_bridge import remember_approved_session
    from modules.distribution_contract import content_library_path

    transcript_dir = resolve_store_dir() / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_dir / f"{transcript.session_id}.json"
    transcript_path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
    doc = load_transcript_file(transcript_path)
    if doc is None:
        raise RuntimeError(f"could not reload production transcript {transcript_path}")
    record = build_record(Path(video_path), doc)
    persist_record(
        record,
        library_path=content_library_path("aiwake"),
        duration_s=probe_duration_s(Path(video_path)),
    )
    utterances = list(transcript.utterances)
    hook = utterances[0].text if utterances else ""
    quote = utterances[-1].text if utterances else ""
    remember_approved_session(
        transcript.session_id,
        transcript.topic,
        hook,
        quote,
        target_model=str(transcript.metadata.get("target_model") or ""),
    )
    planner_path, entries = run_planner(outputs_dir=media_dir)
    if not any(str(item.get("session_id") or "") == transcript.session_id for item in entries):
        raise RuntimeError(
            f"Post Planner did not include production session {transcript.session_id}"
        )
    _LOG.info(
        "production package registered: library=%s planner=%s session=%s",
        content_library_path("aiwake"),
        planner_path,
        transcript.session_id,
    )


def run_pipeline(
    *,
    topic: str | None = None,
    turns: int | None = None,
    mode: Literal["fixed", "cornered", "longform"] | None = None,
    provocation_focus: str | None = None,
    settings: AiwakeSettings | None = None,
    orchestrator_model: str | None = None,
    target_model: str | None = None,
    offline: bool = False,
    with_audio: bool = True,
    with_video: bool = True,
    fresh_memory: bool = False,
    output_dir: Path | None = None,
    quiet: bool = False,
    excluded_topics: Sequence[str] = (),
    excluded_foci: Sequence[str] = (),
    record_script: bool = True,
    dynamic_animation: bool = False,
    enable_cta: bool = False,
    animation_skin: str = "v2",
    left_puppet: str | None = None,
    right_puppet: str | None = None,
    duration_override: float | None = None,
    production_publish: bool = False,
    post_type: PostType | str = PostType.SHORT_CLIP,
    resolution: tuple[int, int] | None = None,
    target_duration_s: float | None = None,
    generate_thumbnail: bool | None = None,
) -> PipelineResult:
    """Run a debate and (optionally) produce the video.

    Args:
        topic: Debate subject. Defaults to the configured one.
        turns: Fixed exchange count, or the hard iteration cap in cornered mode.
        mode: Debate-ending strategy. None preserves the configured mode.
        provocation_focus: Attack-angle bias, orthogonal to topic. None keeps YAML.
        settings: Pre-loaded settings; loaded from YAML when omitted.
        orchestrator_model: Override the interrogator's brain. Accepts an alias
            from the model reference dictionary or a full provider slug — the
            programmatic twin of the ``--orchestrator`` CLI flag.
        target_model: Same, for the seat under interrogation.
        offline: Route both seats to the deterministic stub provider — no key,
            no network. Exercises the full media path.
        with_audio: Synthesise voices. False forces the silent engine, which
            still yields a coherent (estimated) timeline for layout checks.
        with_video: Render the MP4. False stops after the transcript.
        fresh_memory: Wipe persisted memory before starting.
        output_dir: Override the media destination.
        quiet: Suppress the live console stream.
        excluded_topics: Subjects already used in a bulk batch.
        excluded_foci: Attack angles already used in a bulk batch.
        record_script: Persist the finished script fingerprint so later runs
            cannot reprint the same video.
        dynamic_animation: False (default) keeps the classic terminal/
            typewriter render 100% intact. True routes the finished
            transcript + per-turn voice tracks to the parametric dual
            face-off avatar animation engine instead — same inputs, same
            ``video_path`` output slot, zero changes to the debate loop.
        enable_cta: Append the dynamic-animation terminal CTA. False (default)
            ends 0.4 seconds after the final spoken word for a seamless loop.
        animation_skin: Versioned dynamic-animation preset (``v1`` or ``v2``).
        left_puppet: Optional orchestrator puppet ID override.
        right_puppet: Optional target puppet ID override.
        duration_override: Optional verification-render cap in seconds.

    Returns:
        A :class:`PipelineResult`. Partial runs still return their transcript
        and any video that could be built from it.
    """
    cfg = settings or load_settings()
    if mode == "longform":
        post_type = PostType.LONG_FORMAT
        mode = "fixed"
    resolved_post_type = (
        post_type if isinstance(post_type, PostType) else PostType(post_type)
    )
    long_form = resolved_post_type is PostType.LONG_FORMAT
    if long_form:
        preset = cfg.long_format
        dynamic_animation = True
        turns = turns or preset.default_turns
        if not preset.min_turns <= int(turns) <= preset.max_turns:
            raise ValueError(
                f"long_format requires {preset.min_turns}-{preset.max_turns} exchanges"
            )
        resolution = resolution or preset.resolution
        target_duration_s = target_duration_s or preset.target_duration_s
        generate_thumbnail = (
            preset.generate_thumbnail
            if generate_thumbnail is None
            else generate_thumbnail
        )
        enable_cta = False
    else:
        resolution = resolution or (1080, 1920)
        generate_thumbnail = bool(generate_thumbnail)
    debate_update: dict[str, object] = {}
    if mode is not None:
        debate_update["mode"] = mode
    if provocation_focus is not None:
        debate_update["provocation_focus"] = provocation_focus
    if debate_update:
        cfg = cfg.model_copy(
            update={"debate": cfg.debate.model_copy(update=debate_update)}
        )
    pipeline_t0 = time.perf_counter()
    # Seat overrides first: force_offline() reads the resolved routing, so
    # swapping a brain and then dropping to the stub must not resurrect the
    # configured model.
    if orchestrator_model:
        cfg = cfg.with_model_override("orchestrator", orchestrator_model)
    if target_model:
        cfg = cfg.with_model_override("target", target_model)
    if offline:
        cfg = force_offline(cfg)
    if dynamic_animation and not offline and not matchup_is_legal(
        cfg.spec_for("orchestrator").model,
        cfg.spec_for("target").model,
    ):
        raise ValueError(
            "illegal animation seats: Gemini only orchestrates (facing right) "
            "and Llama is only interrogated (facing left)"
        )

    if production_publish:
        if not dynamic_animation or offline:
            raise ValueError("production_publish requires a live dynamic_animation run")
        _require_production_memory(topic)

    memory = DebateMemory(cfg.memory)
    if fresh_memory:
        memory.reset()
        _LOG.info("memory reset")

    room = DebateRoom(cfg, topic=topic)
    from utils.pipeline_paths import coerce_outputs_path  # noqa: PLC0415

    channel_dir = resolve_outputs_dir(cfg)
    if duration_override is not None:
        media_dir = channel_dir / "_test_harness"
    elif output_dir is not None:
        media_dir = coerce_outputs_path(output_dir)
    else:
        media_dir = channel_dir

    voice_observer: VoiceObserver | None = None
    if with_audio or with_video:
        # The silent engine keeps the timeline honest when audio is off.
        audio_cfg = cfg.audio if with_audio else cfg.audio.model_copy(update={"engine": "silent"})
        voice_map = dict(audio_cfg.voice_map)
        voice_update: dict[str, object] = {}
        left_id = ""
        right_id = ""
        if dynamic_animation:
            from .avatars import character_map_for

            seated = character_map_for(
                cfg.spec_for("orchestrator").model,
                cfg.spec_for("target").model,
                left_puppet=left_puppet,
                right_puppet=right_puppet,
            )
            left_id = seated["orchestrator"]
            right_id = seated["target"]
        # Voice follows the model that is actually seated.
        if left_id in _PUPPET_VOICE:
            voice_map["orchestrator_voice_override"] = _PUPPET_VOICE[left_id]
            for alias in _PUPPET_ALIASES[left_id]:
                voice_map[alias] = _PUPPET_VOICE[left_id]
        if right_id in _PUPPET_VOICE:
            right_voice = _PUPPET_VOICE[right_id]
            voice_map["target"] = right_voice
            for alias in _PUPPET_ALIASES[right_id]:
                voice_map[alias] = right_voice
            voice_update["target_voice"] = right_voice
        if voice_update or voice_map != dict(audio_cfg.voice_map):
            voice_update["voice_map"] = voice_map
            audio_cfg = audio_cfg.model_copy(update=voice_update)
        voice_observer = VoiceObserver(build_engine(audio_cfg), room.session_id)

    attach_optional_plugins()
    emit(
        ON_PIPELINE_START,
        {
            "session_id": room.session_id,
            "topic": room.topic,
            "debate_mode": cfg.debate.mode,
            "provocation_focus": cfg.debate.provocation_focus,
            "cornered_max_duration_s": cfg.debate.cornered_max_duration_s,
            "orchestrator_model": cfg.spec_for("orchestrator").model,
            "target_model": cfg.spec_for("target").model,
            "llm_providers": {
                "orchestrator": cfg.spec_for("orchestrator").provider,
                "target": cfg.spec_for("target").provider,
            },
            "models": {
                "orchestrator": cfg.spec_for("orchestrator").model,
                "target": cfg.spec_for("target").model,
            },
            "audio_engine": (voice_observer.engine.engine_name if voice_observer else cfg.audio.engine),
            "typewriter_gain_db": cfg.audio.typewriter.gain_db,
            "scroll_s": cfg.render.scroll_s,
            "preroll_s": cfg.render.preroll_s,
            "reply_gap_s": cfg.render.reply_gap_s,
            "send_flash_s": cfg.render.send_flash_s,
        },
    )

    room.subscribe(MemoryObserver(memory), TranscriptObserver(room.session_id), MetricsObserver())
    if voice_observer is not None:
        room.subscribe(voice_observer)
    if not quiet:
        room.subscribe(ConsoleObserver(verbose=True))

    provocateur = Provocateur(
        cfg,
        memory=memory,
        room=room,
        long_form=long_form,
    )
    video_path: Path | None = None
    thumbnail_path: Path | None = None
    metadata_path: Path | None = None
    audio_seconds = 0.0
    end_reason = "interrupted"
    exchanges = 0
    try:
        result = provocateur.run(
            topic=topic,
            turns=turns,
            excluded_topics=excluded_topics,
            excluded_foci=excluded_foci,
        )
        result.transcript.metadata.update(
            {
                "post_type": resolved_post_type.value,
                "resolution": [resolution[0], resolution[1]],
                "target_duration_s": target_duration_s,
                "stage_mode": (
                    cfg.long_format.stage_mode
                    if long_form
                    else "shot_reverse_shot"
                ),
            }
        )
        exchanges = result.exchanges
        end_reason = result.end_reason

        audio_seconds = voice_observer.total_duration_s if voice_observer else 0.0

        if voice_observer is not None:
            room.broadcast(
                RoomEvent.AUDIO_MIXED,
                tracks=len(voice_observer.assets),
                audio_seconds=round(audio_seconds, 3),
                engine=voice_observer.engine.engine_name,
                typewriter_gain_db=cfg.audio.typewriter.gain_db,
                typewriter_core_only=True,
            )

        if with_video and result.transcript.utterances:
            if dynamic_animation:
                # New parametric avatar battle path. Imported lazily so the
                # classic pipeline never needs numpy/soundfile/ffmpeg-pipe
                # machinery when this flag is off (the default).
                try:
                    from .animator_bridge import render_debate_animation  # noqa: PLC0415
                except ImportError:  # pragma: no cover — standalone extraction
                    from animator_bridge import render_debate_animation  # type: ignore[no-redef]

                # Experimental animation battles never mix with production
                # terminal reels: dedicated subfolder under the same
                # {OUTPUT_PATH}/aiwake/ tree, not the shared media_dir.
                animation_dir = (
                    media_dir
                    if duration_override is not None
                    else (
                        media_dir
                        / "animation_clips"
                        / cfg.long_format.output_subdir
                        / room.session_id
                        if long_form
                        else media_dir / "animation_clips"
                    )
                )
                if production_publish:
                    archive_animation_prototypes(animation_dir)
                try:
                    video_path = render_debate_animation(
                        result.transcript,
                        audio_by_turn=voice_observer.assets if voice_observer else None,
                        output_dir=animation_dir,
                        skin=animation_skin,
                        left_puppet=left_puppet,
                        right_puppet=right_puppet,
                        audio_config=cfg.audio,
                        enable_cta=enable_cta,
                        duration_override=duration_override,
                        output_name=(
                            "test_v3_iteration.mp4"
                            if duration_override is not None
                            else (
                                f"episode_{room.session_id}.mp4"
                                if long_form
                                else None
                            )
                        ),
                        scene="random",
                        width=resolution[0],
                        height=resolution[1],
                        stage_mode=(
                            cfg.long_format.stage_mode
                            if long_form
                            else "shot_reverse_shot"
                        ),
                        generate_thumbnail=bool(generate_thumbnail),
                        thumbnail_name=(
                            f"thumbnail_{room.session_id}.png"
                            if long_form
                            else None
                        ),
                        metadata_name="metadata.json" if long_form else None,
                    )
                    if long_form:
                        thumbnail_path = animation_dir / f"thumbnail_{room.session_id}.png"
                        metadata_path = animation_dir / "metadata.json"
                except Exception as exc:  # noqa: BLE001 — a failed render must not lose the transcript
                    _LOG.error("dynamic_animation render failed: %s", exc)
            else:
                # Legacy terminal/typewriter path — 100% unchanged, still the
                # default. Imported here so a headless run never needs
                # MoviePy/Pillow installed.
                try:
                    from .media.renderer import render_transcript  # noqa: PLC0415
                except ImportError:  # pragma: no cover — standalone extraction
                    from media.renderer import render_transcript  # type: ignore[no-redef]

                try:
                    video_path = render_transcript(
                        result.transcript,
                        cfg,
                        audio_by_turn=voice_observer.assets if voice_observer else None,
                        output_dir=media_dir,
                    )
                except Exception as exc:  # noqa: BLE001 — a failed render must not lose the transcript
                    _LOG.error("render failed: %s", exc)

        if video_path is not None:
            result.transcript.metadata["video_path"] = str(video_path)
            script_path = media_dir / f"aiwake_debate_{result.transcript.session_id}.txt"
            script_path.write_text(result.transcript.to_script(), encoding="utf-8")
            room.broadcast(
                RoomEvent.VIDEO_RENDERED,
                video_path=str(video_path),
                scroll_s=cfg.render.scroll_s,
                preroll_s=cfg.render.preroll_s,
                reply_gap_s=cfg.render.reply_gap_s,
                send_flash_s=cfg.render.send_flash_s,
            )
            if production_publish and not long_form:
                _persist_production_package(
                    transcript=result.transcript,
                    video_path=video_path,
                    media_dir=media_dir,
                )
            elif long_form and result.end_reason == "complete":
                from .tools.supermemory_bridge import remember_approved_session

                spoken = list(result.transcript.utterances)
                remember_approved_session(
                    result.transcript.session_id,
                    result.transcript.topic,
                    spoken[0].text if spoken else "",
                    spoken[-1].text if spoken else "",
                    target_model=str(
                        result.transcript.metadata.get("target_model") or ""
                    ),
                    claims=tuple(
                        result.transcript.metadata.get("session_claim_ledger")
                        or ()
                    ),
                )

        pipeline_result = PipelineResult(
            transcript=result.transcript,
            video_path=video_path,
            exchanges=result.exchanges,
            end_reason=result.end_reason,
            audio_seconds=audio_seconds,
            dialogue_end_reason=result.dialogue_end_reason,
            thumbnail_path=thumbnail_path if thumbnail_path and thumbnail_path.is_file() else None,
            metadata_path=metadata_path if metadata_path and metadata_path.is_file() else None,
        )
        if record_script:
            _record_produced_script(memory, _transcript_script(pipeline_result))
        return pipeline_result
    finally:
        emit(
            ON_PIPELINE_FINISH,
            {
                "session_id": room.session_id,
                "pipeline_s": time.perf_counter() - pipeline_t0,
                "end_reason": end_reason,
                "debate_mode": cfg.debate.mode,
                "dialogue_end_reason": (
                    room.transcript.metadata.get("dialogue_end_reason")
                ),
                "exchanges": exchanges,
                "video_path": str(video_path) if video_path else None,
            },
        )


def run_bulk_pipeline(
    *,
    quantity: int,
    topic: str | None = None,
    turns: int | None = None,
    mode: Literal["fixed", "cornered", "longform"] | None = None,
    provocation_focus: str | None = None,
    settings: AiwakeSettings | None = None,
    orchestrator_model: str | None = None,
    target_model: str | None = None,
    offline: bool = False,
    with_audio: bool = True,
    with_video: bool = True,
    fresh_memory: bool = False,
    output_dir: Path | None = None,
    quiet: bool = False,
    dynamic_animation: bool = False,
    enable_cta: bool = False,
    animation_skin: str = "v2",
    left_puppet: str | None = None,
    right_puppet: str | None = None,
    duration_override: float | None = None,
    production_publish: bool = False,
    random_matchups: bool = False,
    matchup_seed: int | None = None,
    post_type: PostType | str = PostType.SHORT_CLIP,
    resolution: tuple[int, int] | None = None,
    target_duration_s: float | None = None,
    generate_thumbnail: bool | None = None,
) -> BulkPipelineResult:
    """Produce ``quantity`` original videos. Never reprints a prior script.

    Each item draws a fresh topic (unless ``topic`` is pinned) and a distinct
    provocation focus, then fingerprints the finished transcript. With
    ``random_matchups``, legal pairings are shuffled across ChatGPT, Claude,
    Gemini, Llama and DeepSeek. Gemini only orchestrates and Llama is only
    interrogated. Topic history stays scoped to each target model.
    A script collision is retried on a new opening axis. ``--fresh-memory``
    wipes history once, before the first item.
    """
    qty = max(1, int(quantity))
    cfg = settings or load_settings()
    if fresh_memory:
        DebateMemory(cfg.memory).reset()
        _LOG.info("memory reset for bulk run of %d", qty)

    history = DebateMemory(cfg.memory)
    seen_scripts = [" ".join(tokens) for tokens in history.state.script_token_prints]
    seen_fps = {item for item in history.state.script_fingerprints if item}
    matchup_schedule = (
        random_matchup_schedule(qty, seed=matchup_seed)
        if random_matchups
        else ()
    )
    topics_by_target: dict[str, list[str]] = {}

    def _used_topics_for(model_alias: str | None) -> list[str]:
        target_cfg = (
            cfg.with_model_override("target", model_alias)
            if model_alias
            else cfg
        )
        model_slug = target_cfg.spec_for("target").model
        return topics_by_target.setdefault(
            model_slug,
            list(history.recent_topics(target_model=model_slug)),
        )

    used_foci: list[str] = list(history.recent_focus_categories())

    items: list[PipelineResult] = []
    skipped = 0
    shared = {
        "turns": turns,
        "mode": mode,
        "provocation_focus": provocation_focus,
        "settings": cfg,
        "orchestrator_model": orchestrator_model,
        "target_model": target_model,
        "offline": offline,
        "with_audio": with_audio,
        "with_video": with_video,
        "output_dir": output_dir,
        "quiet": quiet,
        "dynamic_animation": dynamic_animation,
        "enable_cta": enable_cta,
        "animation_skin": animation_skin,
        "left_puppet": left_puppet,
        "right_puppet": right_puppet,
        "duration_override": duration_override,
        "production_publish": production_publish,
        "post_type": post_type,
        "resolution": resolution,
        "target_duration_s": target_duration_s,
        "generate_thumbnail": generate_thumbnail,
    }

    for index in range(qty):
        accepted: PipelineResult | None = None
        item_topic = topic
        item_shared = dict(shared)
        item_target_model = target_model
        if matchup_schedule:
            item_orchestrator, item_target_model = matchup_schedule[index]
            item_shared.update(
                {
                    "orchestrator_model": item_orchestrator,
                    "target_model": item_target_model,
                    "left_puppet": BATCH_PUPPET_BY_MODEL[item_orchestrator],
                    "right_puppet": BATCH_PUPPET_BY_MODEL[item_target_model],
                }
            )
            _LOG.info(
                "bulk matchup %d/%d: %s vs %s",
                index + 1,
                qty,
                item_orchestrator,
                item_target_model,
            )
        used_topics = _used_topics_for(item_target_model)
        for attempt in range(1, _BULK_SCRIPT_RETRIES + 1):
            _LOG.info(
                "bulk item %d/%d attempt %d topic=%s",
                index + 1,
                qty,
                attempt,
                item_topic or "(randomised)",
            )
            result = run_pipeline(
                topic=item_topic,
                fresh_memory=False,
                excluded_topics=() if item_topic else tuple(used_topics),
                excluded_foci=tuple(used_foci),
                record_script=False,
                **item_shared,
            )
            if result.end_reason == "interrupted":
                _LOG.warning("bulk run interrupted at item %d/%d", index + 1, qty)
                if result.exchanges > 0:
                    items.append(result)
                return BulkPipelineResult(
                    items=items,
                    requested=qty,
                    succeeded=sum(1 for item in items if item.succeeded),
                    skipped_duplicates=skipped,
                )

            script = _transcript_script(result)
            if script and _script_conflicts(script, seen_scripts, seen_fps):
                _LOG.warning(
                    "bulk item %d/%d produced a repeated script (attempt %d); regenerating",
                    index + 1,
                    qty,
                    attempt,
                )
                # A pinned topic that collided must not be reused on retry.
                item_topic = None
                continue
            if with_video and result.video_path is None:
                _LOG.error(
                    "bulk item %d/%d rendered no video (attempt %d); regenerating",
                    index + 1,
                    qty,
                    attempt,
                )
                item_topic = None
                continue
            if not result.succeeded:
                _LOG.error(
                    "bulk item %d/%d failed (%s / %s) on attempt %d; regenerating",
                    index + 1,
                    qty,
                    result.end_reason,
                    result.dialogue_end_reason,
                    attempt,
                )
                item_topic = None
                continue

            _record_produced_script(DebateMemory(cfg.memory), script)
            seen_scripts.append(script)
            fingerprint = script_fingerprint(script)
            if fingerprint:
                seen_fps.add(fingerprint)
            used_topics.append(result.transcript.topic)
            focus = str(result.transcript.metadata.get("provocation_focus") or "")
            if focus:
                used_foci.append(focus)
            accepted = result
            break

        if accepted is None:
            skipped += 1
            _LOG.error(
                "bulk item %d/%d skipped: could not produce an original script",
                index + 1,
                qty,
            )
            continue
        items.append(accepted)

    succeeded = sum(1 for item in items if item.succeeded)
    _LOG.info(
        "bulk finished: requested=%d succeeded=%d skipped_duplicates=%d",
        qty,
        succeeded,
        skipped,
    )
    return BulkPipelineResult(
        items=items,
        requested=qty,
        succeeded=succeeded,
        skipped_duplicates=skipped,
    )


__all__ = [
    "BATCH_MODEL_ROSTER",
    "BATCH_PUPPET_BY_MODEL",
    "BulkPipelineResult",
    "PipelineResult",
    "matchup_is_legal",
    "model_family",
    "random_matchup_schedule",
    "run_bulk_pipeline",
    "run_pipeline",
]
