"""Re-render the four illegal production battles and then exit."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from channels_config.aiwake.animator_bridge import render_debate_animation
from channels_config.aiwake.contracts import DebateTranscript, SpeakerRole
from channels_config.aiwake.media.audio import TTSError, build_engine
from channels_config.aiwake.settings import load_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
LOG = logging.getLogger("aiwake.rerevoice")

STORE = Path("channels_config/aiwake/store/transcripts")
DEEPSEEK_SLUG = "deepseek/deepseek-chat"
DEEPSEEK_NAME = "DeepSeek Chat"

JOBS = (
    {
        "session": "20260928_215814_ce428c",
        "swap_orchestrator": True,
        "left": "deepseek_cyborg_v3",
        "right": "chatgpt_cyborg_v1",
    },
    {
        "session": "20260928_220547_711701",
        "swap_orchestrator": True,
        "left": "deepseek_cyborg_v3",
        "right": "claude_cyborg_v1",
    },
    {
        "session": "20260928_220019_086c76",
        "swap_orchestrator": False,
        "left": "gemini_cyborg_v2",
        "right": "llama_cyborg_v2",
    },
    {
        "session": "20260928_220140_ea8116",
        "swap_orchestrator": False,
        "left": "chatgpt_cyborg_v1",
        "right": "llama_cyborg_v2",
    },
)


def _speak_all(transcript: DebateTranscript, settings) -> dict:
    engine = build_engine(settings.audio)
    assets = {}
    session_id = f"{transcript.session_id}_revoice"
    for utterance in transcript.utterances:
        if not (utterance.text or "").strip():
            continue
        asset = None
        for attempt in range(1, 5):
            try:
                asset = engine.speak(utterance, session_id=session_id)
                break
            except TTSError as exc:
                if attempt == 4:
                    raise
                LOG.warning("TTS retry %d/4 turn %d: %s", attempt, utterance.turn_index, exc)
                time.sleep(2.0 * attempt)
        assert asset is not None
        LOG.info(
            "voiced turn %d %s %s (%.2fs)",
            utterance.turn_index,
            utterance.role.value,
            asset.voice,
            asset.duration_s,
        )
        assets[utterance.turn_index] = asset
    return assets


def _prepare(path: Path, swap_orchestrator: bool) -> DebateTranscript:
    transcript = DebateTranscript.model_validate_json(path.read_text(encoding="utf-8"))
    if not swap_orchestrator:
        return transcript
    updated = []
    for utterance in transcript.utterances:
        if utterance.role is SpeakerRole.ORCHESTRATOR:
            utterance = utterance.model_copy(
                update={"speaker_name": DEEPSEEK_NAME, "model_slug": DEEPSEEK_SLUG}
            )
        updated.append(utterance)
    return transcript.model_copy(update={"utterances": updated})


def main() -> None:
    settings = load_settings()
    for job in JOBS:
        path = STORE / f"{job['session']}.json"
        transcript = _prepare(path, bool(job["swap_orchestrator"]))
        video_name = Path(str(transcript.metadata.get("video_path") or "")).name
        if not video_name:
            raise SystemExit(f"missing video_path on {job['session']}")
        output_dir = Path(str(transcript.metadata["video_path"])).parent
        LOG.info(
            "rerender %s -> %s (%s vs %s)",
            job["session"],
            video_name,
            job["left"],
            job["right"],
        )
        assets = _speak_all(transcript, settings)
        rendered = render_debate_animation(
            transcript,
            audio_by_turn=assets,
            output_dir=output_dir,
            left_puppet=str(job["left"]),
            right_puppet=str(job["right"]),
            audio_config=settings.audio,
            enable_cta=False,
            scene="random",
            output_name=video_name,
        )
        LOG.info("wrote %s", rendered)


if __name__ == "__main__":
    main()
