# -*- coding: utf-8 -*-
"""Smoke 17: voice in the mux, sentence beats, and a reel that still compiles."""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


def _tone(path: Path, freq: float, seconds: float, *, sr: int = 16000, amp: float = 0.6) -> None:
    n = int(seconds * sr)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        frames = bytearray()
        for i in range(n):
            sample = int(amp * 32767 * math.sin(2 * math.pi * freq * i / sr))
            frames += struct.pack("<h", sample)
        handle.writeframes(frames)


def _band_energy(path: Path, freq: float, *, sr: int = 16000) -> float:
    import subprocess

    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(path),
            "-ac", "1", "-ar", str(sr), "-f", "s16le", "-",
        ],
        capture_output=True,
        check=False,
    )
    data = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float64)
    if data.size < sr // 10:
        return 0.0
    t = np.arange(data.size) / sr
    ref = np.sin(2 * np.pi * freq * t)
    return float(abs(np.dot(data, ref)) / data.size)


def _pattern(path: Path, shift: int, color: tuple[int, int, int] = (220, 40, 40)) -> None:
    image = Image.new("RGB", (64, 96), (16, 24, 32))
    pixels = image.load()
    for y in range(96):
        for x in range(64):
            if (x + shift) % 10 < 4:
                pixels[x, y] = color
    image.save(path, quality=85)


def _media_duration(path: Path) -> float:
    import subprocess

    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return float((proc.stdout or "0").strip() or 0.0)


def _pix_fmt(path: Path) -> str:
    import subprocess

    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout or "").strip()


def _stills(folder: Path) -> list[Path]:
    frames = []
    for index, color in enumerate(((120, 80, 40), (40, 90, 140))):
        path = folder / f"still_{index}.jpg"
        _pattern(path, index * 5, color)
        frames.append(path)
    return frames


def test_audio_filter_uses_the_real_input_indexes() -> None:
    from core.reel_ffmpeg_render import _audio_filter

    graph = _audio_filter(
        [
            {"index": 1, "role": "voice", "volume": 1.15},
            {"index": 2, "role": "bed", "volume": 0.32},
            {"index": 3, "role": "boom", "volume": 1.0},
        ],
        duck_until=4.0,
        duck_ratio=0.7,
    )
    assert "[1:a]" in graph
    assert "[2:a]" in graph
    assert "[3:a]" in graph
    assert "[0:a]" not in graph


def test_a_reel_without_voice_is_refused(tmp_path: Path) -> None:
    from core.reel_ffmpeg_render import compile_ffmpeg_reel
    from core.reel_sequence_engine import compile_sequence_reel

    still = _stills(tmp_path)[0]
    with pytest.raises(RuntimeError, match="voice track"):
        compile_ffmpeg_reel(
            image_paths=[still],
            act_durations=[0.4],
            output_path=tmp_path / "silent.mp4",
            require_voice=True,
        )
    with pytest.raises(RuntimeError, match="voice track"):
        compile_sequence_reel(
            [still],
            "Hook",
            voice_audio=None,
            output_path=tmp_path / "no-voice.mp4",
            act_durations=[0.4],
            strict_act_durations=True,
            page_id="test",
        )


def test_sentence_beats_follow_the_spoken_line() -> None:
    from modules.reel_qa.beats import beats_from_timestamps

    script = "The hills roll toward Şanlıurfa. Farmers work the limestone fields."
    words = []
    t = 0.0
    for token in script.split():
        words.append((token, t, t + 0.4))
        t += 0.4
    beats = beats_from_timestamps(script, words, narration_s=t)
    assert len(beats) == 2
    assert beats[0][0].startswith("The hills")
    assert "Farmers" in beats[1][0]
    assert beats[0][1] == 0.0
    assert beats[0][2] <= beats[1][1] + 0.05
    assert abs((beats[1][2] - beats[0][1]) - t) < 0.05


def test_soft_ceiling_does_not_cut_the_opening_sentence() -> None:
    from modules.reel_artefact.script_length import soft_ceiling

    opening = "In 9600 BC the pillars already stood."
    kept = soft_ceiling(opening + " " + ("More detail follows here. " * 80))
    assert kept.startswith(opening)
    assert "More detail follows here." in kept


def test_geography_reaches_the_flux_prompt_and_rejects_dunes() -> None:
    import json

    from modules.reel_artefact.flux_policy import safe_flux_prompt
    from modules.reel_qa.relevance import dune_conflict

    entities = json.loads(
        Path("channels_config/ancient_knowledge/entities.json").read_text(encoding="utf-8")
    )
    by_id = {row["id"]: row for row in entities}
    gobekli = by_id["gobekli_tepe"]["geography"]
    baghdad = by_id["baghdad_battery"]["geography"]
    prompt = safe_flux_prompt(
        "The enclosure faces the hills.", geography=gobekli, scene="landscape",
    )
    assert "Şanlıurfa" in prompt
    assert "Germuş" in prompt
    assert "sand dunes" in prompt
    assert "Sahara" in prompt
    bag = safe_flux_prompt("A clay jar was found.", geography=baghdad, scene="landscape")
    assert "Tigris" in bag
    assert "Khujut Rabu" in bag
    assert "date palms" in bag
    assert dune_conflict("Sahara dunes and a mesa", gobekli)
    assert dune_conflict("sand dunes beside the jar", baghdad)
    assert not dune_conflict("date palms on a flat river plain", baghdad)


def test_burned_in_text_is_a_hard_defect(tmp_path: Path) -> None:
    from modules.reel_qa.relevance import _verdict_from_payload

    image = tmp_path / "panel.jpg"
    Image.new("RGB", (8, 8), (20, 20, 20)).save(image)
    verdict = _verdict_from_payload(
        {
            "is_relevant": True,
            "relevance_score": 0.92,
            "contains_text": True,
            "image_description": "a museum panel reading 8000 BCE",
        },
        image_path=image,
        chunk_text="The site dates to about 9600 BC.",
        banned_subjects=None,
        provider="test",
        model_id="test",
    )
    assert verdict["is_relevant"] is False
    assert "contains_text" in str(verdict["rejection_reason"])


def test_light_rays_are_off_for_ancient_knowledge() -> None:
    text = Path("channels_config/ancient_knowledge/page_config.py").read_text(encoding="utf-8")
    assert "ENABLE_LIGHT_RAYS: bool = False" in text


def test_adjacent_near_duplicates_are_rejected(tmp_path: Path) -> None:
    from modules.reel_artefact.diversity import claim_diverse, reset_diversity

    reset_diversity()
    first = tmp_path / "a.jpg"
    copy = tmp_path / "a2.jpg"
    other = tmp_path / "b.jpg"
    _pattern(first, 0, (180, 40, 40))
    _pattern(copy, 0, (180, 40, 40))
    _pattern(other, 5, (20, 40, 160))
    assert claim_diverse(first, source_key="one", beat_index=0)
    assert claim_diverse(copy, source_key="two", beat_index=1) is False
    reset_diversity()
    assert claim_diverse(first, source_key="same", beat_index=0)
    assert claim_diverse(other, source_key="other", beat_index=1)
    assert claim_diverse(copy, source_key="same", beat_index=2)


def test_an_exhausted_beat_still_gets_a_frame(tmp_path: Path) -> None:
    from modules.reel_artefact.reuse import guarantee_still

    path = guarantee_still(tmp_path / "beat.jpg", 3)
    assert path.is_file()
    assert path.stat().st_size > 100


def test_final_frame_is_judged_against_the_planned_shot(tmp_path: Path) -> None:
    from modules.reel_qa.mp4_gate import SubtitleCue, gate_final_mp4

    still = tmp_path / "approved.jpg"
    other = tmp_path / "frame.jpg"
    Image.new("RGB", (32, 48), (10, 10, 10)).save(still)
    Image.new("RGB", (32, 48), (200, 180, 40)).save(other)
    seen: dict = {}

    def _frame(_video: Path, _at: float, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(other.read_bytes())
        return dest

    def _judge(*args, **kwargs):
        seen["text"] = args[1] if len(args) > 1 else ""
        seen["kwargs"] = kwargs
        return {
            "is_relevant": True,
            "relevance_score": 0.9,
            "qa_status": "scored",
            "rejection_reason": None,
        }

    narration = "Some believe stonework exceeds Neolithic tools"
    gate_final_mp4(
        tmp_path / "reel.mp4",
        [SubtitleCue(text="stonework exceeds", start_s=48.0, end_s=52.0)],
        probe_fn=lambda _path: {
            "error": "",
            "duration_s": 60.0,
            "has_audio": True,
            "mean_volume_db": -16.0,
        },
        evaluate_fn=_judge,
        frame_fn=_frame,
        beats=[{
            "index": 8,
            "text": narration,
            "passed": True,
            "image_path": str(still),
            "prompt": "wide limestone hills and farmland near Şanlıurfa",
            "subject": "rolling limestone hills near Şanlıurfa",
        }],
    )
    assert seen["kwargs"]["score_against"] == "shot"
    assert "Şanlıurfa" in seen["kwargs"]["shot_subject"]
    assert seen["kwargs"]["shot_subject"] != narration


def test_baghdad_search_includes_the_site_queries() -> None:
    import json

    entities = json.loads(
        Path("channels_config/ancient_knowledge/entities.json").read_text(encoding="utf-8")
    )
    bag = next(row for row in entities if row["id"] == "baghdad_battery")
    queries = " ".join(bag["commons_search"])
    assert "Baghdad Battery" in queries
    assert "Parthian battery" in queries
    assert "Khujut Rabu jar" in queries
    assert "Baghdad Battery" in " ".join(bag["commons_categories"])


@pytest.mark.skipif(
    not __import__("core.reel_ffmpeg_render", fromlist=["ffmpeg_has_libass"]).ffmpeg_has_libass(),
    reason="ffmpeg libass missing",
)
def test_ffmpeg_reel_keeps_the_voice(tmp_path: Path) -> None:
    from core.reel_ffmpeg_render import compile_ffmpeg_reel, output_has_audio

    voice = tmp_path / "voice.wav"
    bed = tmp_path / "bed.wav"
    _tone(voice, 440.0, 1.2)
    _tone(bed, 110.0, 1.2, amp=0.15)
    out = tmp_path / "reel.mp4"
    compile_ffmpeg_reel(
        image_paths=_stills(tmp_path),
        act_durations=[0.6, 0.6],
        output_path=out,
        voice_audio=voice,
        ambient_audio=bed,
        require_voice=True,
        synthesize_drone=False,
        transition_booms=True,
        workers=2,
    )
    assert out.is_file()
    assert output_has_audio(out) is True
    voice_energy = _band_energy(out, 440.0)
    bed_energy = _band_energy(out, 110.0)
    assert voice_energy > bed_energy
    assert voice_energy > 20.0


@pytest.mark.skipif(
    not __import__("core.reel_ffmpeg_render", fromlist=["ffmpeg_has_libass"]).ffmpeg_has_libass(),
    reason="ffmpeg libass missing",
)
def test_offline_reel_compiles_with_voice_and_no_adjacent_dupes(tmp_path: Path) -> None:
    """Mocked stills, real ffmpeg. The gate the live smoke did not have."""
    from core.reel_ffmpeg_render import output_has_audio
    from core.reel_sequence_engine import compile_sequence_reel
    from modules.reel_artefact.diversity import claim_diverse, reset_diversity
    from modules.reel_qa.beats import beats_from_timestamps, check_timeline

    script = "Limestone hills face the farmland. The river plain holds date palms."
    words = []
    clock = 0.8
    sentence_starts = [0.0]
    first = True
    for sentence in ("Limestone hills face the farmland.", "The river plain holds date palms."):
        if not first:
            sentence_starts.append(clock)
        first = False
        for token in sentence.split():
            words.append((token, clock, clock + 0.25))
            clock += 0.25
        clock += 0.6
    audio_len = words[-1][2]
    beats = beats_from_timestamps(script, words, narration_s=audio_len, audio_end=audio_len)
    assert len(beats) == 2
    assert beats[0][1] == 0.0
    assert abs(beats[1][1] - sentence_starts[1]) < 0.3
    durations = [end - start for _text, start, end in beats]
    check_timeline(durations, [start for _text, start, _end in beats], audio_len)
    reset_diversity()
    frames = []
    colors = ((90, 110, 60), (40, 70, 140))
    for index, (color, beat) in enumerate(zip(colors, beats)):
        path = tmp_path / f"beat_{index}.jpg"
        _pattern(path, index * 5, color)
        assert claim_diverse(path, source_key=f"beat-{index}", beat_index=index)
        frames.append(path)
        assert beat[0]
    voice = tmp_path / "voice.wav"
    _tone(voice, 440.0, audio_len)
    out = tmp_path / "compiled.mp4"
    compile_sequence_reel(
        frames,
        "",
        voice_audio=voice,
        output_path=out,
        act_durations=durations,
        strict_act_durations=True,
        word_timings=words,
        page_id="offline",
        enable_hook_text=False,
    )
    assert out.is_file()
    assert output_has_audio(out) is True
    assert _band_energy(out, 440.0) > 20.0
    assert len(frames) == len(beats)
    assert abs(_media_duration(out) - audio_len) < 0.3
    assert _pix_fmt(out) == "yuv420p"


def test_moviepy_fallback_keeps_the_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("moviepy")
    from core.reel_ffmpeg_render import output_has_audio
    from core.reel_sequence_engine import compile_sequence_reel

    monkeypatch.setenv("REEL_RENDERER", "moviepy")
    voice = tmp_path / "voice.wav"
    _tone(voice, 440.0, 0.8)
    out = tmp_path / "moviepy.mp4"
    compile_sequence_reel(
        _stills(tmp_path),
        "",
        voice_audio=voice,
        output_path=out,
        act_durations=[0.4, 0.4],
        strict_act_durations=True,
        page_id="offline",
        enable_hook_text=False,
    )
    assert out.is_file()
    assert output_has_audio(out) is True
    assert _band_energy(out, 440.0) > 10.0


def test_pauses_stay_on_the_sentence_and_the_cta_reaches_the_voice() -> None:
    from modules.reel_qa.beats import (
        beats_from_timestamps,
        check_timeline,
        seal_sentence_timeline,
    )

    words = []
    clock = 0.7
    second = 0.0
    for index, sentence in enumerate((
        "The hills roll toward the farmland.",
        "Farmers work the limestone fields.",
    )):
        if index == 1:
            second = clock
        for token in sentence.split():
            words.append((token, clock, clock + 0.3))
            clock += 0.3
        clock += 0.6
    narr_end = words[-1][2]
    silence = 1.0
    cta = 1.4
    full = narr_end + silence + cta
    beats = beats_from_timestamps(
        "The hills roll toward the farmland. Farmers work the limestone fields.",
        words,
        narration_s=narr_end,
        audio_end=narr_end + silence,
    )
    assert beats[0][1] == 0.0
    assert abs(beats[1][1] - second) < 0.05
    durations = [end - start for _text, start, end in beats]
    snippets, durations = seal_sentence_timeline(
        [text for text, _s, _e in beats],
        durations,
        [start for _text, start, _end in beats],
        full,
        "Follow for the next find.",
    )
    assert snippets[-1] == "Follow for the next find."
    assert abs(sum(durations) - full) < 0.05
    check_timeline(durations, [start for _t, start, _e in beats], full)
    from modules.reel_qa.beats import TimelineError

    with pytest.raises(TimelineError):
        check_timeline([1.0, 1.0], [0.0, 1.0], 4.0)


def test_a_short_picture_is_rejected(tmp_path: Path) -> None:
    from core.reel_sequence_engine import compile_sequence_reel

    voice = tmp_path / "voice.wav"
    _tone(voice, 440.0, 2.0)
    with pytest.raises(RuntimeError, match="timeline mismatch"):
        compile_sequence_reel(
            [_stills(tmp_path)[0]],
            "",
            voice_audio=voice,
            output_path=tmp_path / "short.mp4",
            act_durations=[0.4],
            strict_act_durations=True,
            page_id="offline",
            enable_hook_text=False,
        )


def test_final_qa_rejects_a_truncated_narration() -> None:
    from modules.reel_qa.mp4_gate import decide_mp4_gate

    probe = {
        "error": "",
        "duration_s": 20.0,
        "has_audio": True,
        "mean_volume_db": -16.0,
        "narration_end_s": 30.0,
    }
    frames = [{"passed": True, "text": "farmland"}]
    cut = decide_mp4_gate(probe, frames)
    assert "narration_truncated" in cut["reasons"]
    held = decide_mp4_gate({**probe, "narration_end_s": 19.8}, frames)
    assert "narration_truncated" not in held["reasons"]


def test_yellow_captions_sit_in_a_dark_box() -> None:
    from core.reel_ffmpeg_render import ass_document, ken_burns_filter

    script = ass_document([("Sub", 0.0, 1.0, "the jar")])
    assert "&H0000E6FF" in script
    assert "&H80000000" in script
    assert ",3,14,1," in script
    assert "DejaVu Sans,64" in script
    assert ken_burns_filter(0, 8).endswith("format=yuv420p")
    hook = ass_document([("Hook", 0.0, 1.0, "Göbekli Tepe")])
    assert "DejaVu Sans,80" in hook


def test_ffmpeg_flag_fails_loud_and_honors_the_env_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from core.reel_ffmpeg_render import attempt_ffmpeg, ffmpeg_exe, ffprobe_exe

    monkeypatch.setenv("REEL_RENDERER", "ffmpeg")
    monkeypatch.delenv("FFMPEG_BINARY", raising=False)
    monkeypatch.delenv("FFPROBE_BINARY", raising=False)
    monkeypatch.setattr("core.reel_ffmpeg_render.shutil.which", lambda _name: None)
    monkeypatch.setattr("core.reel_ffmpeg_render._imageio_ffmpeg", lambda: "")
    with pytest.raises(RuntimeError, match="ffmpeg is not on PATH"):
        ffmpeg_exe()
    binary = tmp_path / "ffmpeg"
    probe = tmp_path / "ffprobe"
    binary.write_bytes(b"")
    probe.write_bytes(b"")
    monkeypatch.setenv("FFMPEG_BINARY", str(binary))
    monkeypatch.setenv("FFPROBE_BINARY", str(probe))
    assert ffmpeg_exe() == str(binary)
    assert ffprobe_exe() == str(probe)
    monkeypatch.setattr("core.reel_ffmpeg_render.ffmpeg_exe", lambda: str(binary))
    with pytest.raises(RuntimeError, match="render broke"):
        attempt_ffmpeg(lambda: (_ for _ in ()).throw(RuntimeError("render broke")))


def test_a_wide_photo_is_a_vertical_crop_not_a_blurred_band() -> None:
    from modules.reel_artefact.crop import prepare_reel_frame

    image = Image.new("RGB", (400, 120), (20, 20, 220))
    image.paste(Image.new("RGB", (80, 120), (220, 30, 30)), (0, 0))
    frame, how = prepare_reel_frame(image, anchor="center")
    assert how == "window"
    assert frame.size == (1080, 1920)
    corner = frame.getpixel((8, 8))
    assert corner[2] > corner[0]


def test_the_cta_frame_is_not_failed_for_anachronism(tmp_path: Path) -> None:
    from modules.reel_qa.mp4_gate import SubtitleCue, gate_final_mp4

    still = tmp_path / "still.jpg"
    frame = tmp_path / "cta.jpg"
    Image.new("RGB", (64, 96), (10, 10, 10)).save(still)
    Image.new("RGB", (64, 96), (40, 70, 90)).save(frame)
    seen = {"n": 0}

    def _frame(_video: Path, _at: float, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(frame.read_bytes())
        return dest

    def _judge(*_args, **_kwargs):
        seen["n"] += 1
        return {
            "is_relevant": False,
            "relevance_score": 0.1,
            "qa_status": "scored",
            "rejection_reason": "anachronism",
        }

    decision = gate_final_mp4(
        tmp_path / "reel.mp4",
        [SubtitleCue(text="Follow for more", start_s=8.0, end_s=10.0)],
        probe_fn=lambda _path: {
            "error": "",
            "duration_s": 90.0,
            "has_audio": True,
            "mean_volume_db": -16.0,
        },
        evaluate_fn=_judge,
        frame_fn=_frame,
        beats=[{
            "index": 4,
            "text": "Follow for more hidden mysteries.",
            "is_cta": True,
            "passed": True,
            "image_path": str(still),
        }],
    )
    assert seen["n"] == 0
    assert decision["passed"] is True


def test_the_hook_names_gobekli_and_rejects_turkish_sands() -> None:
    from modules.reel_artefact.script_grounding import (
        grounding_problem,
        hook_names_topic,
        script_is_usable,
        terrain_conflicts,
    )

    topic = "Göbekli Tepe's carved stone pillars, 9600 BC"
    bad = (
        "The Great Pyramid of Giza is a legendary marvel. "
        "Some researchers believe the stones were buried beneath the Turkish sands. "
        "The pillars still stand."
    )
    good = (
        "Göbekli Tepe's carved pillars rose on a limestone hill near Şanlıurfa. "
        "Farmland and the Germuş mountains surround the enclosures. "
        "The T-shaped stones carry reliefs of animals, and the site dates to 9600 BC. "
        "Builders set each pillar into the hill before the villages spread across the plain."
    )
    assert hook_names_topic(bad, topic) is False
    assert hook_names_topic(good, topic) is True
    assert terrain_conflicts(bad, "rolling limestone hills and farmland, no lake") == "sands"
    assert grounding_problem(good, topic, channel="ancient_knowledge") == ""
    assert script_is_usable("BC. Baghdad.", topic) is False
    assert script_is_usable(good, topic) is True


def test_mystery_music_does_not_call_gemini_pro(monkeypatch: pytest.MonkeyPatch) -> None:
    from agents.media.audio_engine import generate_dynamic_music_prompt

    seen: dict = {}

    def _client(_key: str) -> object:
        return object()

    def _generate(_client_obj: object, chain: list[str], contents: object = None) -> object:
        seen["chain"] = list(chain)

        class _Response:
            text = "Dark drones and a slow frame drum. Slow tempo 55 BPM."

        return _Response()

    monkeypatch.setattr(
        "agents.media.audio_engine.app_config.GEMINI_API_KEY", "test-key", raising=False,
    )
    monkeypatch.setattr(
        "agents.media.providers.gemini_utils.make_gemini_client_with_fallback", _client,
    )
    monkeypatch.setattr(
        "agents.media.providers.gemini_utils.generate_content_with_model_fallback",
        _generate,
    )
    text = generate_dynamic_music_prompt(
        "Göbekli Tepe", style_profile="mystery", channel_name="ancient_knowledge",
    )
    assert seen["chain"]
    assert all("2.5-pro" not in item for item in seen["chain"])
    assert "models/gemini-2.5-flash" in seen["chain"]
    assert len(text) >= 20


def test_nano_stops_on_a_billing_error() -> None:
    from modules.reel_artefact.nanobanana import fatal_nano_error, run_model_chain

    calls: list[str] = []

    def _call(model: str) -> bool:
        calls.append(model)
        raise RuntimeError("402 RESOURCE_EXHAUSTED")

    with pytest.raises(RuntimeError, match="nanobanana_failed"):
        run_model_chain(["cheap", "mid", "dear"], _call)
    assert calls == ["cheap"]
    assert fatal_nano_error(OSError("[Errno 11001] getaddrinfo failed")) is True


def test_the_documentary_prompt_names_the_topic_not_giza() -> None:
    from core.reel_sequence_engine import build_sequence_script_prompt

    prompt = build_sequence_script_prompt(
        "Göbekli Tepe's carved stone pillars, 9600 BC",
        "history",
        channel_name="ancient_knowledge",
    )
    assert "first sentence must include that name" in prompt
    assert "The Great Pyramid of Giza" not in prompt
    other = build_sequence_script_prompt("A topic", "history", channel_name="other")
    assert "The Great Pyramid of Giza" in other
