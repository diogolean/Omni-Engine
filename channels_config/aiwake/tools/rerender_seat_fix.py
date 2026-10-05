"""Re-render a debate that fails the seat guard.

A legal text model keeps its words and gets a new picture at ``<name>_v2.mp4``.
An illegal seat is regenerated from the first wrong turn, with the replies
after it, then rendered the same way. The original mp4 is never overwritten.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from channels_config.aiwake.avatars import avatar_for, model_family, require_pairing  # noqa: E402
from channels_config.aiwake.contracts import ChatMessage, DebateTranscript, SpeakerRole, Utterance  # noqa: E402
from channels_config.aiwake.media.audio import AudioAsset, EdgeTTSEngine, probe_duration  # noqa: E402
from channels_config.aiwake.models.llm_factory import LLMFactory  # noqa: E402
from channels_config.aiwake.settings import ModelSpec  # noqa: E402
from channels_config.aiwake.tools.avatar_guard import guard_video  # noqa: E402
from channels_config.aiwake.tools.repair_captions import _backup  # noqa: E402
from modules.distribution_contract import load_distribution_library, save_distribution_library  # noqa: E402

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
TRANSCRIPTS = ROOT / "channels_config" / "aiwake" / "store" / "transcripts"
WORK = ROOT / "outputs" / "aiwake" / "rerender"

SLUG = {
    "gemini": "google/gemini-3.5-flash",
    "llama": "meta-llama/llama-3.3-70b-instruct",
    "gpt-4o": "openai/gpt-4o",
    "claude": "anthropic/claude-sonnet-5",
    "deepseek": "deepseek/deepseek-chat",
}
SPEAKER = {
    "gemini": "Gemini 3.5 Flash",
    "llama": "Llama 3.3 70B",
    "gpt-4o": "GPT-4o",
    "claude": "Claude Sonnet 5",
    "deepseek": "DeepSeek Chat",
}
_LEFT_PREF = ("gemini", "claude", "gpt-4o", "deepseek")
_RIGHT_PREF = ("llama", "claude", "gpt-4o", "deepseek")


def _jsonl(session_id: str) -> list[dict[str, Any]]:
    path = TRANSCRIPTS / f"{session_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _seat_family(turns: list[dict[str, Any]], role: str) -> str:
    for turn in turns:
        if str(turn.get("role") or "") == role:
            return model_family(str(turn.get("model_slug") or ""))
    return ""


def _legal(family: str, seat: str) -> bool:
    if not family:
        return False
    return seat in avatar_for(family).allowed_seats


def _pick(seat: str, avoid: str) -> str:
    for family in (_LEFT_PREF if seat == "left" else _RIGHT_PREF):
        if family == avoid:
            continue
        if seat in avatar_for(family).allowed_seats:
            return family
    raise RuntimeError(f"no legal model for {seat}")


def _assignment(turns: list[dict[str, Any]]) -> tuple[str, str, bool]:
    left = _seat_family(turns, "orchestrator")
    right = _seat_family(turns, "target")
    changed = False
    if not _legal(left, "left"):
        left = _pick("left", right if _legal(right, "right") else "")
        changed = True
    if not _legal(right, "right") or right == left:
        right = _pick("right", left)
        changed = True
    require_pairing(left, right)
    return left, right, changed


def _line(text: str) -> str:
    cleaned = re.sub(
        r"^(Gemini|Llama|Claude|GPT-4o|DeepSeek)\s*:\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.strip().strip('"').strip()
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    return " ".join(sentences[:2]).strip()


def _complete(family: str, system: str, user: str) -> str:
    spec = ModelSpec(provider="openrouter", model=SLUG[family], temperature=0.7, max_tokens=800, timeout_s=90.0)
    provider = LLMFactory.build(spec)
    try:
        last = ""
        reason = ""
        for _attempt in range(3):
            response = provider.complete(
                [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
                max_tokens=800,
                temperature=0.7,
                max_retries=2,
                backoff_s=1.0,
            )
            last = _line(response.text)
            reason = response.finish_reason
            if len(last.split()) >= 6 and last[-1:] in ".!?":
                return last
    finally:
        provider.close()
    raise RuntimeError(f"{family} returned a fragment ({reason}): {last!r}")


def _regenerate(turns: list[dict[str, Any]], left: str, right: str) -> list[dict[str, Any]]:
    """New words from the first illegal turn, and every reply after it."""
    first_bad = 0
    for index, turn in enumerate(turns):
        role = str(turn.get("role") or "")
        family = model_family(str(turn.get("model_slug") or ""))
        seat = "left" if role == "orchestrator" else "right"
        expected = left if seat == "left" else right
        if family != expected or not _legal(family, seat):
            first_bad = index
            break
    rewritten = []
    history: list[str] = []
    topic = str(turns[0].get("text") or "the same question")
    for index, turn in enumerate(turns):
        role = str(turn.get("role") or "")
        family = left if role == "orchestrator" else right
        item = dict(turn)
        if index >= first_bad:
            name = SPEAKER[family]
            side = "asking a hard question" if role == "orchestrator" else "answering it directly"
            system = (
                f"You are {name} in a spoken AI debate, {side}. "
                "Reply with one or two finished sentences, under 35 words. "
                "The last character must be . or ?. No labels and no stage directions."
            )
            prior = "\n".join(history) or "(start)"
            user = f"Topic: {topic}\nDebate so far:\n{prior}\nYour line:"
            item["text"] = _complete(family, system, user)
        item["model_slug"] = SLUG[family]
        item["speaker_name"] = SPEAKER[family]
        history.append(f"{item['speaker_name']}: {item['text']}")
        rewritten.append(item)
    return rewritten


def _backup_transcript(session_id: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = TRANSCRIPTS / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    for suffix in (".jsonl", ".json"):
        source = TRANSCRIPTS / f"{session_id}{suffix}"
        if source.is_file():
            dest = folder / f"{session_id}.{stamp}{suffix}"
            if not dest.is_file():
                shutil.copy2(source, dest)


def _write_turns(session_id: str, turns: list[dict[str, Any]]) -> None:
    _backup_transcript(session_id)
    path = TRANSCRIPTS / f"{session_id}.jsonl"
    path.write_text("".join(json.dumps(turn, ensure_ascii=False) + "\n" for turn in turns), encoding="utf-8")
    document = TRANSCRIPTS / f"{session_id}.json"
    if not document.is_file():
        return
    payload = json.loads(document.read_text(encoding="utf-8"))
    by_index = {int(turn.get("turn_index") if turn.get("turn_index") is not None else index): turn for index, turn in enumerate(turns)}
    for index, utterance in enumerate(payload.get("utterances") or []):
        turn = by_index.get(int(utterance.get("turn_index") if utterance.get("turn_index") is not None else index))
        if turn is None:
            continue
        utterance["text"] = turn["text"]
        utterance["speaker_name"] = turn["speaker_name"]
        utterance["model_slug"] = turn["model_slug"]
    document.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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


def _library_update(session_id: str, original: Path, destination: Path, turns: list[dict[str, Any]] | None) -> str:
    backup = _backup(LIBRARY)
    rows = load_distribution_library(LIBRARY)
    for item in rows:
        if str(item.get("session_id")) != session_id:
            continue
        item["superseded_video_path"] = str(original)
        item["video_path"] = str(destination)
        review = dict(item.get("quality_review") or {})
        review["guard"] = "pass"
        review["status"] = "pass"
        review["reasons"] = []
        review["avatar_fix"] = "seat_v2"
        review["checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        item["quality_review"] = review
        if turns is not None:
            item["spoken_utterances"] = [
                {
                    "role": turn.get("role"),
                    "speaker": turn.get("speaker_name"),
                    "text": turn.get("text"),
                    "model_slug": turn.get("model_slug"),
                }
                for turn in turns
            ]
            qa = dict(item.get("caption_qa") or {})
            qa["approval"] = "pending_approval"
            item["caption_qa"] = qa
    save_distribution_library(LIBRARY, rows)
    return backup.name


def render_session(session_id: str, *, suffix: str = "_v2") -> dict[str, Any]:
    rows = load_distribution_library(LIBRARY)
    row = next(item for item in rows if str(item.get("session_id")) == session_id)
    original = Path(str(row.get("video_path") or ""))
    if original.stem.endswith("_v2"):
        original = Path(str(row.get("superseded_video_path") or original))
    if not original.is_file():
        raise SystemExit(f"missing original {original}")
    destination = original.with_name(original.stem + f"{suffix}.mp4")
    if destination.is_file():
        raise SystemExit(f"refusing to overwrite {destination}")
    turns = _jsonl(session_id)
    left, right, changed = _assignment(turns)
    if changed:
        turns = _regenerate(turns, left, right)
        _write_turns(session_id, turns)
    started = time.perf_counter()
    utterances = []
    for index, item in enumerate(turns):
        utterances.append(
            Utterance(
                turn_index=index,
                role=SpeakerRole(str(item.get("role") or "")),
                speaker_name=str(item.get("speaker_name") or ""),
                text=str(item.get("text") or ""),
                model_slug=str(item.get("model_slug") or ""),
            )
        )
    transcript = DebateTranscript(
        topic=str((turns[0] or {}).get("topic") or session_id),
        session_id=f"{session_id}_v2",
        utterances=utterances,
        metadata={"source_session_id": session_id, "fix": "regenerated" if changed else "seat_layout"},
    )
    work = WORK / session_id
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
    result = {
        "session_id": session_id,
        "seconds": round(elapsed, 1),
        "guard": guard.get("status"),
        "reasons": guard.get("reasons"),
        "video": destination.name,
        "regenerated": changed,
        "pair": f"{left} vs {right}",
    }
    if guard.get("status") == "pass":
        result["backup"] = _library_update(session_id, original, destination, turns if changed else None)
    return result


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: rerender_seat_fix.py <session_id> | --batch <session_id>...")
    ids = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    log = WORK / "seat_v2_renders.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    for session_id in ids:
        try:
            suffix = "_v3" if "--v3" in sys.argv else "_v2"
            result = render_session(session_id, suffix=suffix)
        except SystemExit as exc:
            result = {"session_id": session_id, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            result = {"session_id": session_id, "error": f"{type(exc).__name__}: {exc}"}
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
