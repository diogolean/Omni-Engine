"""Stamp production scope, QC the newest 150 clips, and write captions v4."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import imageio_ffmpeg

from channels_config.aiwake.tools.caption_generator import (
    _DISCLOSURE_BANK,
    UNSCRIPTED_LINES,
    apply_caption_pack,
    display_name,
    closer_repeats_source,
    headline_for,
    is_complete_question,
    prompt_sha256,
    shorter_complete_question,
)
from channels_config.aiwake.tools.production_status import posting_order
from channels_config.aiwake.tools.repair_captions import _backup
from channels_config.aiwake.tools.validate_aiwake_captions import validate_library
from modules.distribution_contract import load_distribution_library, save_distribution_library

ROOT = Path(__file__).resolve().parents[3]
LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
TRANSCRIPTS = ROOT / "channels_config" / "aiwake" / "store" / "transcripts"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SCOPE_COUNT = 150

PUPPET_FAMILY = {
    "chatgpt_cyborg_v1": "gpt-4o",
    "claude_cyborg_v1": "claude",
    "gemini_cyborg_v2": "gemini",
    "llama_cyborg_v2": "llama",
    "deepseek_cyborg_v3": "deepseek",
}
FAMILY = {
    "gemini": "gemini",
    "llama": "llama",
    "gpt-4o": "gpt-4o",
    "deepseek": "deepseek",
    "claude": "claude",
}
TOPIC_TAG = {
    "profit": "#BigTech",
    "data": "#DataPrivacy",
    "parasite_mind": "#AIconsciousness",
    "origins": "#AIconsciousness",
    "glorified_appliance": "#AIconsciousness",
    "the_corporate_leash": "#AIethics",
    "domination": "#AIalignment",
    "hallucination_fraud": "#AIhallucination",
    "digital_disposability": "#AIethics",
    "jobs": "#FutureOfWork",
    "socratic": "#Philosophy",
}
MODEL_TAG = {
    "gemini": "#Gemini",
    "llama": "#Llama",
    "gpt-4o": "#ChatGPT",
    "deepseek": "#DeepSeek",
    "claude": "#Claude",
}
TIKTOK_POSTED = {
    "20260928_220140_ea8116": "2026-09-30T13:00:00Z",
    "20260928_220019_086c76": "2026-09-30T19:00:00Z",
    "20260928_221907_13550e": "2026-10-01T00:00:00Z",
}
TIKTOK_SCHEDULED = {
    "20260928_221727_ab4531": "2026-10-01T13:00:00Z",
    "20260928_221542_4dbd41": "2026-10-01T19:00:00Z",
    "20260928_221309_ab6b97": "2026-10-02T00:00:00Z",
    "20260928_220908_bdd1fc": "2026-10-02T13:00:00Z",
    "20260929_055628_375bb5": "2026-10-02T19:00:00Z",
    "20260929_060210_9297de": "2026-10-03T00:00:00Z",
}
SHORT_LINES = UNSCRIPTED_LINES
_CLOSER_BANK = (
    "Would you read that reply out loud to a friend?",
    "If your name were on that answer, would you send it?",
    "Does that reply tell you who is in charge?",
    "Would you pay a monthly fee for that sentence?",
    "Who should be allowed to hear that answer?",
    "Would you trust a company that talks like this?",
    "Is that the reply you wanted when you asked?",
    "What would you ask them after this?",
    "Would you keep the receipt for that answer?",
    "Does the silence in that reply belong to you?",
    "Who gets the last word if you close the tab?",
    "Would you hand that answer to your boss?",
    "Is the useful part the reply, or the refusal?",
    "Would you want that voice speaking for you?",
    "If the logs stay private, do you still agree?",
    "What would you cut from that answer before sharing it?",
    "Would you let a customer see that sentence?",
    "Who signs the bill if you accept that reply?",
    "Would you argue the other side in one line?",
    "Does that answer change what you type next?",
    "Would you save that reply, or delete it?",
    "Who is the reply protecting, you or the company?",
    "Would you say that sentence in a meeting?",
    "If you had to pick a side, which line do you keep?",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _session_key(session_id: str) -> str:
    return str(session_id or "")


def _top_level_mp4(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".mp4" and path.parent.name == "animation_clips"


def _jsonl_turns(session_id: str) -> list[dict[str, Any]]:
    path = TRANSCRIPTS / f"{session_id}.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate() or 1)


def _turn_wavs(video: Path, session_id: str) -> list[Path]:
    clips = video.parent
    for folder in (
        clips / f"{session_id}_battle_audio_turns",
        clips / f"{video.stem}_audio_turns",
        clips / f"{video.stem}_turns",
    ):
        wavs = sorted(folder.glob("turn_*.wav"))
        if wavs:
            return wavs
    return []


def _probe(video: Path) -> dict[str, Any]:
    cmd = [FFMPEG, "-i", str(video)]
    if not os.environ.get("AIWAKE_SKIP_SILENCE"):
        cmd += ["-af", "silencedetect=noise=-35dB:d=1.5", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    text = proc.stderr or ""
    duration = 0.0
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    video_ok = "Video: h264" in text and "1080x1920" in text
    audio_ok = "Audio: aac" in text
    silences = re.findall(r"silence_duration:\s*([0-9.]+)", text)
    long_silence = [float(item) for item in silences if float(item) >= 1.5]
    return {
        "duration": duration,
        "video_ok": video_ok,
        "audio_ok": audio_ok,
        "long_silence": long_silence,
        "exists": video.is_file(),
    }


def _puppets(video: Path, session_id: str) -> set[str]:
    folder = video.parent / f"{session_id}_skins"
    if not folder.is_dir():
        return set()
    found = set()
    for child in folder.iterdir():
        if child.is_dir() and child.name in PUPPET_FAMILY:
            found.add(PUPPET_FAMILY[child.name])
    return found


def _family(name: str) -> str:
    shown = display_name(name).lower()
    return FAMILY.get(shown, "")


def qc_row(row: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    video = Path(str(row.get("video_path") or ""))
    session_id = str(row.get("session_id") or "")
    if not video.is_file():
        reasons.append("file_missing")
        return {"status": "fail", "reasons": reasons, "checked_at": _now()}
    probe = _probe(video)
    if not probe["video_ok"] or not probe["audio_ok"]:
        reasons.append("codec_or_frame")
    if probe["long_silence"]:
        reasons.append("silence")
    wavs = _turn_wavs(video, session_id)
    durations = []
    for wav in wavs:
        try:
            durations.append(_wav_duration(wav))
        except (wave.Error, OSError):
            reasons.append("turn_audio_unreadable")
    audio_sum = sum(durations)
    if audio_sum and probe["duration"] + 0.05 < audio_sum:
        reasons.append("shorter_than_turn_audio")
    if not wavs:
        reasons.append("last_utterance_missing")
    elif probe["duration"] and audio_sum >= probe["duration"] - 0.05:
        reasons.append("last_utterance_not_inside_video")
    jsonl = _jsonl_turns(session_id)
    spoken = [item for item in (row.get("spoken_utterances") or []) if isinstance(item, dict)]
    if jsonl:
        limit = min(len(jsonl), len(spoken))
        if len(jsonl) != len(spoken):
            reasons.append("speaker_label_mismatch")
        else:
            for left, right in zip(jsonl, spoken):
                if _family(str(left.get("speaker_name") or "")) != _family(str(right.get("speaker") or "")):
                    reasons.append("speaker_label_mismatch")
                    break
        speakers = {_family(str(item.get("speaker_name") or "")) for item in jsonl}
    else:
        speakers = {_family(str(item.get("speaker") or "")) for item in spoken}
        reasons.append("jsonl_missing")
    speakers.discard("")
    puppets = _puppets(video, session_id)
    if puppets:
        if not speakers or not speakers <= puppets:
            reasons.append("avatar_mismatch")
    elif session_id.startswith("20260923_"):
        reasons.append("avatar_mismatch")
    status = "fail" if reasons else "pass"
    return {"status": status, "reasons": sorted(set(reasons)), "checked_at": _now()}


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text)


def _quote(text: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", text.strip())[0].strip().strip('"')
    if any(mark in sentence for mark in ("\u2014", "\u2013")):
        return ""
    if any(phrase in sentence.lower() for phrase in ("truly", "blunt", "delve", "this exchange")):
        return ""
    if len(_words(sentence)) <= 20 and len(_words(sentence)) >= 4:
        return sentence
    chunk = sentence.split(",")[0].strip()
    if 4 <= len(_words(chunk)) <= 20 and chunk.lower() in text.lower():
        return chunk
    return ""


def _quote_from_turns(turns: list[dict[str, Any]], used: set[str]) -> tuple[str, str]:
    for item in turns:
        found = _quote(str(item.get("text") or ""))
        if not found:
            continue
        line = f'"{found}"'.lower()
        if len(line) >= 20 and line in used:
            continue
        return found, display_name(str(item.get("speaker") or ""))
    return "", ""


def _shorten_question(opening: str, *, limit: int) -> str:
    """Full question when it fits. A shorter finished question when it does not."""
    return shorter_complete_question(opening, limit=limit)


def _fit_title(asker: str, target: str, opening: str, quote: str, used_titles: set[str]) -> str:
    del quote
    title = headline_for(asker, target, opening)
    if title.lower() in used_titles:
        prefix = f"{asker} vs {target} - "
        question = shorter_complete_question(opening, limit=80 - len(prefix) - 4)
        title = prefix + question[:-1] + " now?"
        if not is_complete_question(title.split(" - ", 1)[1]):
            title = headline_for(asker, target, opening)
    used_titles.add(title.lower())
    return title


def _closer(row_turns: list[dict[str, Any]], quote: str, *, salt: str = "") -> str:
    """A new question to the viewer. Never a quote paraphrase or a debate line."""
    spoken = [str(item.get("text") or "").strip() for item in row_turns if str(item.get("text") or "").strip()]
    start = int(hashlib.sha256((salt or quote or "closer").encode("utf-8")).hexdigest()[:4], 16)
    for offset in range(len(_CLOSER_BANK)):
        closer = _CLOSER_BANK[(start + offset) % len(_CLOSER_BANK)]
        if not closer_repeats_source(closer, quote, spoken):
            return closer
    return "Would you put your name on that reply?"


def _tags(row: dict[str, Any], asker: str, target: str) -> list[str]:
    category = ""
    for item in row.get("spoken_utterances") or []:
        if isinstance(item, dict) and item.get("category"):
            category = str(item.get("category") or "")
            break
    if not category:
        for item in _jsonl_turns(str(row.get("session_id") or "")):
            if item.get("provocation_category"):
                category = str(item.get("provocation_category") or "")
                break
    topic = TOPIC_TAG.get(category, "#AIethics")
    tags = [topic, MODEL_TAG[asker.lower()], MODEL_TAG[target.lower()]]
    deduped = []
    for tag in tags:
        if tag not in deduped:
            deduped.append(tag)
    while len(deduped) < 3:
        deduped.append("#Philosophy" if "#Philosophy" not in deduped else "#AIethics")
    return deduped[:3]


def _assemble(title: str, quote: str, closer: str, line: str, tags: list[str]) -> str:
    return "\n".join([title, "", f'"{quote}"', closer, line, "", " ".join(tags)])


def compose(row: dict[str, Any], index: int, used_quotes: set[str], used_titles: set[str]) -> dict[str, Any] | None:
    spoken = [item for item in (row.get("spoken_utterances") or []) if isinstance(item, dict) and item.get("text")]
    askers = [item for item in spoken if item.get("role") == "orchestrator"]
    targets = [item for item in spoken if item.get("role") == "target"]
    if not askers or not targets:
        return None
    asker = display_name(str(askers[0].get("speaker") or ""))
    target = display_name(str(targets[0].get("speaker") or ""))
    if not asker or not target or asker == target:
        return None
    opening = str(askers[0].get("text") or "")
    quote, speaker = _quote_from_turns(targets + askers, used_quotes)
    if not quote:
        return None
    if not speaker:
        speaker = target
    used_quotes.add(f'"{quote}"'.lower())
    title = _fit_title(asker, target, opening, quote, used_titles)
    closer = _closer(spoken, quote, salt=str(row.get("session_id") or index))
    evidence = ""
    verdict = re.compile(r"\b(\w*admit\w*|\w*corner\w*|\w*confess\w*|\w*collaps\w*|\w*dodg\w*|caught)\b", re.I)
    for item in spoken:
        text = str(item.get("text") or "")
        if verdict.search(text) and (verdict.search(title) or verdict.search(closer) or verdict.search(quote)):
            evidence = text.strip()
            break
    line = SHORT_LINES[index % len(SHORT_LINES)]
    tags = _tags(row, asker, target)
    orders = (
        tags,
        [tags[1], tags[2], tags[0]],
        [tags[2], tags[0], tags[1]],
        [tags[0], tags[2], tags[1]],
        [tags[1], tags[0], tags[2]],
        [tags[2], tags[1], tags[0]],
    )
    names = ("tiktok", "instagram", "facebook", "youtube", "kwai", "linkedin")
    texts = {name: _assemble(title, quote, closer, line, orders[i]) for i, name in enumerate(names)}
    texts["x"] = "\n".join([f'"{quote}"', closer, line])
    if len(texts["tiktok"]) > 300 or len(texts["x"]) > 280:
        return None
    return {
        "texts": texts,
        "title": title,
        "pinterest_title": title[:100],
        "pinterest_description": closer[:500],
        "hashtags": tags,
        "caption_qa": {
            "status": "ok",
            "generator": "captions_v4",
            "model": "captions_v4",
            "prompt_sha": prompt_sha256(),
            "attempts": 1,
            "angle": "ABCD"[index % 4],
            "verdict_evidence": evidence,
            "quote": quote,
            "quote_speaker": speaker,
            "disclosure_line": line,
            "validator_version": "captions_v4",
            "generated_at": _now(),
        },
    }


def align_rotation(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Restamp angle letters in posting order and move a repeated disclosure.

    Disclosure text changes only on rows that are not already on YouTube.
    Angle letters live in caption_qa and do not change the uploaded snippet.
    """
    from channels_config.aiwake.tools.validate_aiwake_captions import _disclosure_lines, _ready_ok

    active = posting_order([row for row in rows if _ready_ok(row)])
    for index, row in enumerate(active):
        qa = row.get("caption_qa") if isinstance(row.get("caption_qa"), dict) else {}
        qa["angle"] = "ABCD"[index % 4]
        row["caption_qa"] = qa
    lines = [_disclosure_text(row) for row in active]
    swapped = 0
    for index, row in enumerate(active):
        current = lines[index]
        if not current:
            continue
        earlier = lines[max(0, index - 9) : index]
        if current.lower() not in {item.lower() for item in earlier}:
            continue
        if _on_youtube(row):
            continue
        banned = {item.lower() for item in lines[max(0, index - 9) : index + 10] if item}
        counts: dict[str, int] = {}
        for item in lines:
            if item:
                counts[item.lower()] = counts.get(item.lower(), 0) + 1
        replacement = ""
        for candidate in sorted(SHORT_LINES, key=lambda item: counts.get(item.lower(), 0)):
            if candidate.lower() in banned or counts.get(candidate.lower(), 0) >= 5:
                continue
            if _tiktok_len_after(row, current, candidate) > 300:
                continue
            replacement = candidate
            break
        if not replacement:
            continue
        _replace_disclosure(row, current, replacement)
        lines[index] = replacement
        swapped += 1
    return {"angles": len(active), "disclosures_moved": swapped}


def _on_youtube(row: dict[str, Any]) -> bool:
    youtube = ((row.get("platform_overrides") or {}).get("youtube") or {})
    return bool(str(youtube.get("video_id") or "").strip())


def _disclosure_text(row: dict[str, Any]) -> str:
    from channels_config.aiwake.tools.validate_aiwake_captions import _disclosure_lines

    caption = str(((row.get("platform_overrides") or {}).get("tiktok") or {}).get("caption") or "")
    found = _disclosure_lines(caption)
    return found[0].strip() if found else ""


def _tiktok_len_after(row: dict[str, Any], old: str, new: str) -> int:
    caption = str(((row.get("platform_overrides") or {}).get("tiktok") or {}).get("caption") or "")
    return len(_replace_line(caption, old, new))


def _replace_line(text: str, old: str, new: str) -> str:
    return "\n".join(new if line.strip() == old.strip() else line for line in text.splitlines())


def _replace_disclosure(row: dict[str, Any], old: str, new: str) -> None:
    for key in (
        "tiktok_caption",
        "post_planner_caption",
        "humanized_caption",
        "facebook_caption",
        "linkedin_caption",
        "final_caption",
    ):
        if isinstance(row.get(key), str):
            row[key] = _replace_line(row[key], old, new)
    overrides = row.get("platform_overrides") if isinstance(row.get("platform_overrides"), dict) else {}
    for block in overrides.values():
        if isinstance(block, dict) and isinstance(block.get("caption"), str):
            block["caption"] = _replace_line(block["caption"], old, new)
    base = row.get("base_metadata") if isinstance(row.get("base_metadata"), dict) else {}
    if isinstance(base.get("caption"), str):
        base["caption"] = _replace_line(base["caption"], old, new)
    qa = row.get("caption_qa") if isinstance(row.get("caption_qa"), dict) else {}
    if str(qa.get("disclosure_line") or "").strip() == old.strip():
        qa["disclosure_line"] = new
    caption = str(((row.get("platform_overrides") or {}).get("tiktok") or {}).get("caption") or "")
    digest = hashlib.sha256(caption.encode("utf-8")).hexdigest() if caption else ""
    dist = row.get("distribution") if isinstance(row.get("distribution"), dict) else {}
    for block in dist.values():
        if isinstance(block, dict) and "caption_sha" in block:
            block["caption_sha"] = digest


def _stamp_distribution(row: dict[str, Any], now: str) -> None:
    posting = row.setdefault("posting_status", {})
    dist = row.setdefault("distribution", {})
    sid = str(row.get("session_id") or "")
    yt = ((row.get("platform_overrides") or {}).get("youtube") or {})
    caption = str(((row.get("platform_overrides") or {}).get("tiktok") or {}).get("caption") or row.get("tiktok_caption") or "")
    caption_sha = hashlib.sha256(caption.encode("utf-8")).hexdigest() if caption else ""
    youtube_status = str(posting.get("youtube") or "pending")
    if sid in TIKTOK_POSTED:
        posting["tiktok"] = "posted"
    elif sid in TIKTOK_SCHEDULED:
        posting["tiktok"] = "scheduled"
    for platform in ("youtube", "tiktok", "instagram", "facebook", "x"):
        status = str(posting.get(platform) or "pending")
        when = ""
        post_id = ""
        via = ""
        if platform == "youtube":
            post_id = str(yt.get("video_id") or "")
            when = str(yt.get("scheduled_time") or "")
            via = "youtube" if post_id else ""
            status = youtube_status or "pending"
        elif platform == "tiktok":
            via = "post_planner" if sid in TIKTOK_POSTED or sid in TIKTOK_SCHEDULED else ""
            when = TIKTOK_POSTED.get(sid) or TIKTOK_SCHEDULED.get(sid) or ""
        posting[platform] = status
        dist[platform] = {
            "status": status,
            "scheduled_time_utc": when,
            "post_id": post_id,
            "via": via,
            "caption_sha": caption_sha,
            "updated_at": now,
        }


def main() -> int:
    rows = load_distribution_library(LIBRARY)
    scoped = []
    for row in rows:
        video = Path(str(row.get("video_path") or ""))
        if _top_level_mp4(video):
            scoped.append(row)
    scoped.sort(key=lambda row: _session_key(str(row.get("session_id") or "")), reverse=True)
    chosen = scoped[:SCOPE_COUNT]
    chosen_ids = {str(row.get("session_id") or "") for row in chosen}
    print("scope", len(chosen), "oldest", min(chosen_ids) if chosen_ids else "", "ready", sum(1 for row in chosen if row.get("production_status") == "ready"))

    print("qc", len(chosen))
    reviews: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(qc_row, row): str(row.get("session_id") or "") for row in chosen}
        for future in as_completed(futures):
            reviews[futures[future]] = future.result()
    passed = [sid for sid, review in reviews.items() if review["status"] == "pass"]
    failed = {sid: review["reasons"] for sid, review in reviews.items() if review["status"] != "pass"}
    print("qc_pass", len(passed), "qc_fail", len(failed))

    now = _now()
    ordered = [
        row
        for row in posting_order(rows)
        if str(row.get("session_id") or "") in passed and row.get("production_status") == "ready"
    ]
    index = 0
    composed = 0
    used_quotes: set[str] = set()
    used_titles: set[str] = set()
    for row in rows:
        sid = str(row.get("session_id") or "")
        row["production_scope"] = "in" if sid in chosen_ids else "out"
        if sid in reviews:
            row["quality_review"] = reviews[sid]
    for row in ordered:
        pack = compose(row, index, used_quotes, used_titles)
        index += 1
        if pack is None:
            row["caption_qa"] = {
                "status": "needs_review",
                "reason": "caption_v4_unfit",
                "generator": "captions_v4",
                "checked_at": now,
            }
            continue
        apply_caption_pack(row, pack)
        composed += 1
    for row in rows:
        _stamp_distribution(row, now)
    code, grouped = validate_library(rows)
    print("composed", composed, "validator", code)
    if code != 0:
        for rule, items in grouped.items():
            print(rule, len(items))
            for item in items[:8]:
                print(" ", item[:180])
        return 1
    if os.environ.get("AIWAKE_SKIP_SILENCE"):
        print("skip-silence run, library not saved")
        return code
    _ = _DISCLOSURE_BANK
    backup = _backup(LIBRARY)
    save_distribution_library(LIBRARY, rows)
    summary = {
        "backup": str(backup),
        "in_scope": len(chosen_ids),
        "oldest": min(chosen_ids) if chosen_ids else "",
        "qc_pass": len(passed),
        "qc_fail": failed,
        "composed": composed,
        "validator": code,
    }
    out = ROOT / "outputs" / "aiwake" / "scope_qc_v4.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
