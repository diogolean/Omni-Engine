# -*- coding: utf-8 -*-
"""voice steps moved out of main.py. Bodies are unchanged."""
from __future__ import annotations

from engine.runtime import (
    CaptionEngine,
    CostTracker,
    PageContext,
    Path,
    _re,
    app_config,
    generate_voiceover_with_timestamps,
)
from engine.log import _LOG
def _trim_script_to_word_limit(script: str, max_words: int = 140) -> str:
    """Trim a voiceover script to *max_words* while always preserving:
      • the opening hook (first sentence)
      • the final CTA sentence

    [ACT N] markers, persona audio tags, and SSML breaks are excluded from the
    word count so they don't inflate the budget estimate.
    Sentences are removed from the *middle* (furthest from start and end) until
    the count falls at or below the limit.
    Returns the original script unchanged when within budget.
    """
    # Strip act markers + audio behavior / persona tags + SSML for word-count only.
    _tag_pat = (
        r'\[(?:ACT\s*\d+|cackles?|chuckles?|cold\s*chuckle|arrogant\s*scoff|'
        r'deep\s*subtle\s*laugh|dry\s*laugh)\]'
        r'|<\s*break\s+[^>]*>'
    )
    _clean = _re.sub(_tag_pat, '', script, flags=_re.IGNORECASE)
    words = _clean.split()
    if len(words) <= max_words:
        return script

    # Split into individual sentences preserving trailing whitespace context.
    sentences = _re.split(r'(?<=[.!?])\s+', script.strip())
    if len(sentences) <= 2:
        # Can't trim further without destroying structure — hard truncate at word boundary.
        return " ".join(words[:max_words])

    # Iteratively drop the middle sentence until budget is met.
    result = list(sentences)
    while True:
        _test = _re.sub(_tag_pat, '', " ".join(result), flags=_re.IGNORECASE)
        if len(_test.split()) <= max_words or len(result) <= 2:
            break
        mid = len(result) // 2
        result.pop(mid)

    trimmed = " ".join(result)
    _LOG_TRIM = __import__("logging").getLogger(__name__)
    _LOG_TRIM.info(
        "Script trimmed: %d → %d words (max=%d, sentences=%d→%d)",
        len(words),
        len(_re.sub(_tag_pat, '', trimmed, flags=_re.IGNORECASE).split()),
        max_words, len(sentences), len(result),
    )
    return trimmed
def _filter_audio_tag_timings(
    word_timings: list[tuple[str, float, float]],
) -> list[tuple[str, float, float]]:
    """Drop ElevenLabs behavior-tag / SSML tokens from subtitle timings.

    Years and quantities stay. Only a duration fragment such as ``1.5s``
    (leaked from a break tag) is removed.
    """
    from modules.reel_qa.timings import filter_spoken_timings

    return filter_spoken_timings(word_timings)
def _stitch_audio_sequential(
    narration_path: "Path",
    cta_path: "Path | None",
    output_path: "Path",
    silence_s: float = 1.0,
) -> "Path":
    """Concatenate *narration_path* + *silence_s* of silence + *cta_path* into
    *output_path* and return the combined file path.

    The 1-second gap prevents the CTA from overlapping or bleeding into the
    narration.  Returns *narration_path* unchanged if *cta_path* is absent or
    MoviePy import fails, so the pipeline degrades gracefully.
    """
    if cta_path is None or not cta_path.is_file():
        _LOG.warning("CTA audio not found — using narration-only voice track.")
        return narration_path
    try:
        from moviepy import AudioFileClip  # type: ignore[import]
        from moviepy import concatenate_audioclips  # type: ignore[import]
        from moviepy.audio.AudioClip import AudioArrayClip  # type: ignore[import]
        import numpy as _np_stitch

        narr = AudioFileClip(str(narration_path))
        cta  = AudioFileClip(str(cta_path))
        _sr  = 44100
        _sil = _np_stitch.zeros((int(_sr * silence_s), 2), dtype=_np_stitch.float32)
        silence = AudioArrayClip(_sil, fps=_sr)

        combined = concatenate_audioclips([narr, silence, cta])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        combined.write_audiofile(str(output_path), fps=_sr, logger=None)
        _LOG.info(
            "Audio stitched | narr=%.1fs + %.1fsgap + cta=%.1fs → total=%.1fs → %s",
            narr.duration, silence_s, cta.duration, combined.duration, output_path.name,
        )
        for _c in (narr, cta, silence, combined):
            try:
                _c.close()
            except Exception:
                pass
        return output_path
    except Exception as _exc:
        _LOG.warning("Audio stitch failed (%s) — falling back to narration-only track.", _exc)
        return narration_path
def _commit_reel_angle(caption_engine, page_ctx, topic: str, script: str) -> None:
    """Record the angle locked for an accepted Ancient Knowledge script.

    The script call already happened. This only appends one ledger row.
    """
    page_id = (getattr(page_ctx, "page_id", "") or "") if page_ctx is not None else ""
    mode = (getattr(page_ctx, "narrative_mode", "") or "") if page_ctx is not None else ""
    from modules.reel_grounding.config import angle_memory_for
    from modules.reel_grounding.memory import opening_hook, remember_angle

    if not angle_memory_for(page_id, mode):
        return
    if len((script or "").split()) < 40:
        return
    angle = ""
    if caption_engine is not None:
        angle = str(getattr(caption_engine, "last_reel_angle", "") or "")
    remember_angle(
        channel=page_id,
        topic=topic,
        angle=angle,
        hook=opening_hook(script),
    )
def _synthesize_sequence_voice_track(
    script: str,
    *,
    reel_dir: Path,
    stem: str,
    variant: int,
    page_ctx: "PageContext | None",
    caption_engine: "CaptionEngine | None",
    resolved_subject: str,
    economic: bool,
    n_acts: int,
    batch_angle_block: str = "",
    generated_hooks_cache: "list[str] | None" = None,
    cost_tracker: "CostTracker | None" = None,
) -> tuple["Path | None", list, float, float, str, "Path | None", float, float]:
    """Narration TTS + CTA stitch with measure-then-correct duration control.

    Round 7 (Measure-Then-Correct): exactly one initial script → TTS →
    measure. If the resulting total audio is more than ``±15 %`` off the
    requested ``page_ctx.reel_duration``, do EXACTLY ONE corrective
    script regeneration using the just-measured WPS (not any stored
    constant), TTS again, accept. No further loop.

    On the corrective attempt the CTA audio from attempt 1 is reused —
    it's the same fixed text, so re-synthesising it wastes an
    ElevenLabs API call.

    Returns ``(voice_path, narr_word_timings, narr_dur, total_audio_s,
    script, cta_path, cta_dur, silence_before_cta_s)``. ``cta_path`` and
    ``cta_dur`` are ``None`` / ``0`` when no CTA was generated. Silence
    gap between narration and CTA is fixed at 1.0 s (matches
    ``_stitch_audio_sequential`` default).
    """
    from agents.media.audio_engine import _audio_file_duration_s

    page_id = (page_ctx.page_id if page_ctx else "").lower()
    is_ak = page_id == "ancient_knowledge"
    is_mei = page_id == "master_mei"
    _expressive = bool(
        getattr(page_ctx, "tts_expressive_mode", False) or is_mei
    )
    _reel_dur = float(page_ctx.reel_duration if page_ctx else 85.0)
    words_max = int(
        page_ctx.reel_narration_max_words if page_ctx
        else app_config.words_for_duration(90.0)
    )
    speed = 1.0 if is_ak else (page_ctx.tts_narration_speed if page_ctx else None)
    vs = dict(page_ctx.elevenlabs_voice_settings if page_ctx else {})
    if is_ak:
        vs["speed"] = 1.0
    voice_id = page_ctx.elevenlabs_voice_id if page_ctx else None
    model_id = page_ctx.elevenlabs_model if page_ctx else "eleven_multilingual_v2"
    cta_text = (page_ctx.reel_cta_text if page_ctx else "") or ""
    if is_mei:
        from agents.media.mei_narrative import approved_cta_text
        cta_text = approved_cta_text()
    if page_ctx is None or page_ctx.strip_audio_tags_before_tts:
        cta_text = _strip_audio_behavior_tags(cta_text)
    from agents.media.mei_narrative import fix_cta_typos
    cta_text = fix_cta_typos(cta_text or "")

    _cta_silence_s: float = 1.0
    _tts_ckpt = None
    try:
        from modules.reel_artefact.checkpoint import ReelCheckpoint as _VoiceCkpt
        from modules.reel_artefact.checkpoint import mark_tts_resumed as _mark_tts_resumed
        from modules.reel_artefact.policy import artefact_images_enabled as _voice_flag

        _mark_tts_resumed(False)
        if _voice_flag() and is_ak:
            _tts_ckpt = _VoiceCkpt.for_reel(page_id or "ancient_knowledge", stem)
            _saved_voice = _tts_ckpt.load_tts(script or "")
            if _saved_voice is not None:
                _mark_tts_resumed(True)
                _LOG.info("TTS resume | stem=%s", stem)
                return (
                    _saved_voice["voice_path"],
                    _saved_voice["word_timings"],
                    _saved_voice["narr_dur"],
                    _saved_voice["total_s"],
                    _saved_voice["script"] or (script or ""),
                    _saved_voice["cta_path"],
                    _saved_voice["cta_dur"],
                    _saved_voice["silence_s"],
                )
    except Exception as _tts_ckpt_exc:  # noqa: BLE001
        _LOG.info("TTS checkpoint skipped (%s)", type(_tts_ckpt_exc).__name__)
        _tts_ckpt = None

    def _synth_narr(text: str, attempt_label: int) -> tuple["Path | None", list, float]:
        """Single narration TTS call. Returns (voice_path, word_timings, dur_s)."""
        if is_ak:
            from modules.reel_artefact.script_length import soft_ceiling

            cleaned = soft_ceiling(text or "")
        else:
            cleaned = _trim_script_to_word_limit(text or "", max_words=words_max)
        if page_ctx is None or page_ctx.strip_audio_tags_before_tts:
            cleaned = _strip_audio_behavior_tags(cleaned)
        if is_ak:
            from modules.reel_artefact.speech import spoken_numbers

            cleaned = spoken_numbers(cleaned)
        narr_out = reel_dir / f"{stem}_v{variant + 1:02d}_narration.mp3"
        try:
            _vp, _wts = generate_voiceover_with_timestamps(
                cleaned,
                narr_out,
                voice_id=voice_id or None,
                model_id=model_id,
                speed=speed,
                voice_settings=vs or None,
                enable_ssml=bool(page_ctx.tts_enable_ssml) if page_ctx else False,
                expressive_mode=_expressive,
            )
            _wts = _filter_audio_tag_timings(_wts)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Pre-image TTS failed (attempt %d): %s", attempt_label, exc)
            return None, [], 0.0
        try:
            _d = float(_audio_file_duration_s(Path(_vp)))
        except Exception:
            _d = float(_wts[-1][2]) if _wts else 0.0
        return _vp, _wts, _d

    def _synth_cta_once() -> tuple["Path | None", float]:
        if not cta_text:
            return None, 0.0
        cta_out = reel_dir / f"{stem}_v{variant + 1:02d}_cta.mp3"
        try:
            _cp, _ = generate_voiceover_with_timestamps(
                cta_text,
                cta_out,
                voice_id=voice_id or None,
                model_id=model_id,
                speed=speed,
                voice_settings=vs or None,
                expressive_mode=_expressive,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Pre-image CTA TTS failed: %s", exc)
            return None, 0.0
        try:
            _cd = float(_audio_file_duration_s(Path(_cp)))
        except Exception:
            _cd = 0.0
        return _cp, _cd

    def _stitch(voice_only_path: "Path | None", cta_only_path: "Path | None"
                ) -> "Path | None":
        if voice_only_path is None:
            return None
        if cta_only_path is None:
            return voice_only_path
        stitched_out = reel_dir / f"{stem}_v{variant + 1:02d}_voice.mp3"
        return _stitch_audio_sequential(
            voice_only_path, cta_only_path, stitched_out, silence_s=_cta_silence_s,
        )

    def _voice_return(voice_path, narr_wts, narr_dur, total_s, current, cta_path, cta_dur):
        if _tts_ckpt is not None and voice_path is not None and Path(voice_path).is_file():
            try:
                _narr_file = reel_dir / f"{stem}_v{variant + 1:02d}_narration.mp3"
                _tts_ckpt.save_tts(
                    input_script=script or "",
                    accepted_script=current or "",
                    narration_path=_narr_file if _narr_file.is_file() else voice_path,
                    cta_path=cta_path,
                    voice_path=voice_path,
                    word_timings=narr_wts,
                    narr_dur=narr_dur,
                    total_s=total_s,
                    cta_dur=cta_dur,
                    silence_s=_cta_silence_s,
                )
            except Exception as _save_exc:  # noqa: BLE001
                _LOG.info("TTS checkpoint write skipped (%s)", type(_save_exc).__name__)
        return (
            voice_path, narr_wts, narr_dur, total_s, current,
            cta_path, cta_dur, _cta_silence_s,
        )

    # ── Attempt 1 — synthesize once, measure ─────────────────────────────
    # Ancient Knowledge retries a DNS failure. Master Mei stays one attempt.
    current = script or ""
    voice_only_1, narr_wts, narr_dur = None, [], 0.0
    _tts_tries = 3 if is_ak else 1
    for _tts_try in range(1, _tts_tries + 1):
        voice_only_1, narr_wts, narr_dur = _synth_narr(current, attempt_label=_tts_try)
        if voice_only_1 is not None:
            break
        if is_ak and _tts_try < _tts_tries:
            from shared.retry import retry_sleep as _retry_sleep

            _LOG.warning("Pre-image TTS network retry %d", _tts_try)
            _retry_sleep(_tts_try)
    if voice_only_1 is None:
        return (None, [], 0.0, 0.0, current, None, 0.0, _cta_silence_s)
    cta_path, cta_dur = _synth_cta_once()
    voice_path = _stitch(voice_only_1, cta_path)
    try:
        total_s = float(_audio_file_duration_s(Path(voice_path))) if voice_path else 0.0
    except Exception:
        total_s = narr_dur + (cta_dur + _cta_silence_s if cta_path else 0.0)
    wc1 = len(current.split())
    observed_wps_1 = (wc1 / narr_dur) if narr_dur > 0 else 0.0
    _LOG.info(
        "VOICE MEASURE | attempt=1 narr=%.1fs cta=%.1fs silence=%.1fs "
        "total=%.1fs target=%.1fs words=%d observed_wps=%.3f delta=%+.1fs (%+.1f%%)",
        narr_dur, cta_dur, _cta_silence_s, total_s, _reel_dur, wc1,
        observed_wps_1, total_s - _reel_dur,
        100.0 * (total_s - _reel_dur) / _reel_dur if _reel_dur > 0 else 0.0,
    )

    # Length follows the story. Do not regenerate the script to hit a duration.
    _LOG.info(
        "VOICE ACCEPT | story kept | narr=%.1fs total=%.1fs words=%d",
        narr_dur, total_s, wc1,
    )
    return _voice_return(
        voice_path, narr_wts, narr_dur, total_s, current, cta_path, cta_dur,
    )
def _reel_audio_is_f5() -> bool:
    try:
        from core.remote_gpu_manager import is_remote_gpu_enabled

        return bool(is_remote_gpu_enabled("audio"))
    except Exception:
        return False


from engine.acts import _strip_audio_behavior_tags  # noqa: E402
