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
    assert beats[0][2] <= beats[1][1] + 0.05
    assert beats[1][2] > beats[1][1]


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
    from modules.reel_qa.beats import beats_from_timestamps

    script = "Limestone hills face the farmland. The river plain holds date palms."
    words = []
    t = 0.0
    for token in script.split():
        words.append((token, t, t + 0.1))
        t += 0.1
    beats = beats_from_timestamps(script, words, narration_s=t)
    assert len(beats) == 2
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
    _tone(voice, 440.0, max(0.8, t))
    out = tmp_path / "compiled.mp4"
    compile_sequence_reel(
        frames,
        "",
        voice_audio=voice,
        output_path=out,
        act_durations=[max(0.4, beat[2] - beat[1]) for beat in beats],
        strict_act_durations=True,
        word_timings=words,
        page_id="offline",
        enable_hook_text=False,
    )
    assert out.is_file()
    assert output_has_audio(out) is True
    assert _band_energy(out, 440.0) > 20.0
    assert len(frames) == len(beats)


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
