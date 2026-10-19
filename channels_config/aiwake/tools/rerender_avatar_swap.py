"""Re-render a debate whose text is legal but whose face was the wrong model.

Writes a sibling ``<name>_v2.mp4``. Does not delete or overwrite the original.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from channels_config.aiwake.avatars import avatar_for  # noqa: E402
from channels_config.aiwake.contracts import DebateTranscript, SpeakerRole, Utterance  # noqa: E402
from channels_config.aiwake.media.audio import AudioAsset, EdgeTTSEngine, probe_duration  # noqa: E402
from channels_config.aiwake.tools.avatar_guard import guard_video  # noqa: E402
from channels_config.aiwake.tools.repair_captions import _backup  # noqa: E402
from modules.distribution_contract import load_distribution_library, save_distribution_library  # noqa: E402

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
TRANSCRIPTS = ROOT / "channels_config" / "aiwake" / "store" / "transcripts"
WORK = ROOT / "outputs" / "aiwake" / "rerender"

# Text model is already legal on its seat. Keep the words. New face and voice.
SWAP = (
    "20260923_223113_ef9859",
    "20260923_231135_9b9122",
    "20260923_232001_a43ac5",
    "20260923_232618_ed1e9b",
    "20260927_041836_620dc1",
)


def _load(session_id: str) -> tuple[DebateTranscript, list[dict]]:
    path = TRANSCRIPTS / f"{session_id}.jsonl"
    raw = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    utterances = []
    for index, item in enumerate(raw):
        role = SpeakerRole(str(item.get("role") or ""))
        utterances.append(
            Utterance(
                turn_index=index,
                role=role,
                speaker_name=str(item.get("speaker_name") or ""),
                text=str(item.get("text") or ""),
                model_slug=str(item.get("model_slug") or ""),
            )
        )
    topic = str((raw[0] or {}).get("topic") or session_id)
    transcript = DebateTranscript(topic=topic, session_id=f"{session_id}_v2", utterances=utterances, metadata={"source_session_id": session_id, "fix": "avatar_swap"})
    return transcript, raw


def _tts(transcript: DebateTranscript, folder: Path) -> dict[int, AudioAsset]:
    folder.mkdir(parents=True, exist_ok=True)
    engine = EdgeTTSEngine(output_dir=folder)
    assets: dict[int, AudioAsset] = {}
    for utterance in transcript.utterances:
        voice = avatar_for(utterance.model_slug).voice
        destination = folder / f"turn_{utterance.turn_index:02d}.mp3"
        if not destination.is_file() or destination.stat().st_size < 1000:
            engine._synthesize(utterance.text, voice, destination)
        duration, estimated = probe_duration(destination, fallback_text=utterance.text)
        assets[utterance.turn_index] = AudioAsset(
            path=destination,
            duration_s=duration,
            voice=voice,
            estimated=estimated,
            char_count=len(utterance.text),
            role=utterance.role.value,
        )
    return assets


def render_one(session_id: str) -> dict:
    if session_id not in SWAP:
        raise SystemExit(f"{session_id} is not an avatar_swap")
    rows = load_distribution_library(LIBRARY)
    row = next(item for item in rows if str(item.get("session_id")) == session_id)
    original = Path(str(row.get("video_path") or ""))
    if not original.is_file():
        raise SystemExit(f"missing original {original}")
    destination = original.with_name(original.stem + "_v2.mp4")
    if destination.is_file():
        raise SystemExit(f"refusing to overwrite {destination}")
    work = WORK / session_id
    started = time.perf_counter()
    transcript, _raw = _load(session_id)
    audio = _tts(transcript, work / "audio")
    from channels_config.aiwake.animator_bridge import render_debate_animation

    rendered = render_debate_animation(
        transcript,
        audio_by_turn=audio,
        output_dir=work,
        output_name=destination.name,
        enable_cta=False,
        generate_thumbnail=False,
    )
    if destination.is_file():
        raise SystemExit(f"refusing to overwrite {destination}")
    shutil.move(str(rendered), str(destination))
    elapsed = time.perf_counter() - started
    guard = guard_video(
        destination,
        session_id,
        transcripts=TRANSCRIPTS,
        durations=[asset.duration_s for _, asset in sorted(audio.items())],
    )
    if guard.get("status") != "pass":
        return {"session_id": session_id, "seconds": round(elapsed, 1), "guard": guard.get("status"), "reasons": guard.get("reasons"), "video": str(destination)}
    backup = _backup(LIBRARY)
    rows = load_distribution_library(LIBRARY)
    for item in rows:
        if str(item.get("session_id")) != session_id:
            continue
        item["superseded_video_path"] = str(original)
        item["video_path"] = str(destination)
        review = dict(item.get("quality_review") or {})
        reasons = [reason for reason in (review.get("reasons") or []) if reason != "avatar_mismatch"]
        review["reasons"] = reasons
        review["avatar_fix"] = "avatar_swap"
        review["guard"] = "pass"
        if not reasons:
            review["status"] = "pass"
        item["quality_review"] = review
    save_distribution_library(LIBRARY, rows)
    return {"session_id": session_id, "seconds": round(elapsed, 1), "guard": "pass", "video": destination.name, "backup": backup.name}


def main() -> int:
    session_id = sys.argv[1]
    print(json.dumps(render_one(session_id)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
