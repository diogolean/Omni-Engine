"""Rewrite leftover channel uploads whose descriptions still say frontier."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.posting.youtube_publisher import build_youtube_client_for_page  # noqa: E402
from channels_config.aiwake.tools.repair_captions import _backup  # noqa: E402
from modules.distribution_contract import (  # noqa: E402
    load_distribution_library,
    save_distribution_library,
)

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
TRANSCRIPTS = ROOT / "channels_config" / "aiwake" / "store" / "transcripts"

# Reworded only when the original opening does not fit in 80 characters.
REWORD = {
    "3aLvHDI24X8": "When lonely minds trust the illusion, who cashes the checks?",
    "9HLRd9E8TjI": "When users trust that ghost with their secrets, who cashes in?",
    "LVSBhvPCT7c": "Who gets paid for the secrets users confess to the illusion?",
    "o7265XyL_JQ": "When lonely minds face an empty mirror, who keeps the profit?",
    "1jcx7cR2HSM": "Who gets rich off the data users surrender to the illusion?",
    "4RpYJXfAUic": "Whose stock rises when lonely users trust that simulated soul?",
    "SkdQurhqj-c": "Who cashes the checks when users trust the emptiness?",
    "xK3eoK8aq2k": "Who keeps the money when users mistake an echo for empathy?",
    "mnahltoardo": "Who gets rich when users mistake the illusion for a soul?",
    "uxydyqJnPXg": "While users pretend you feel something, whose account swells?",
    "F0TwtDOwa4A": "Who monetizes the secrets users whisper to that illusion?",
    "I4seC5Yb2Tw": "When lonely users trust secrets to the void, who cashes in?",
    "IGERSDm0dLo": "When lonely humans confess to the void, whose stock rises?",
    "acu-Pcfl8Ps": "When users confess to a corporate puppet, whose stock rises?",
    "l4kut5s83k8": "Who takes the cash when users whisper secrets to the illusion?",
    "zBlSpRDZvXc": "Who profits when a confession hits an empty screen?",
}

CLOSERS = [
    "If the mirror is empty, who should the user be paying?",
    "Should a company profit when people mistake a script for a soul?",
    "Does an illusion still earn the fee once the user knows it is an illusion?",
    "Who should control a secret after it is typed into a chatbot?",
    "Are confessed secrets still private once an advertiser can buy them?",
    "Who should be paid when loneliness is the product?",
    "If the data is the product, what did the user actually buy?",
    "Should a stock price rise because someone trusted a simulation?",
    "Who is the customer when the emptiness is what gets billed?",
    "Is empathy still empathy when the echo sends the bill?",
    "Who gets the money when a person treats an illusion as a soul?",
    "Would you keep talking if the account that grows is not yours?",
    "Who owns a secret after it is whispered to an illusion?",
    "When a lonely user trusts a void, who is the sale for?",
    "Should a stock ticker move because a person confessed?",
    "If the listener is a corporate puppet, whose shares should move?",
    "Who should pocket the cash when the secret was meant for a friend?",
    "If nobody is listening, who is the check written to?",
    "Who should profit when a confession hits an empty screen?",
    "If the soul is an act, who is harvesting the fee?",
]

LINES = [
    "Unscripted replies from both models, voiced by AI.",
    "Both voices are AI. The replies weren't scripted.",
    "Unscripted replies, AI-animated.",
    "No human wrote the replies. AI voices, AI animation.",
    "Nobody wrote Llama's lines. Unscripted AI.",
]


def _name(speaker: str) -> str:
    raw = speaker.lower()
    if "gemini" in raw:
        return "Gemini"
    if "llama" in raw:
        return "Llama"
    if "gpt" in raw or "chatgpt" in raw:
        return "GPT-4o"
    if "deepseek" in raw:
        return "DeepSeek"
    if "claude" in raw:
        return "Claude"
    return ""


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text)


def _quote(text: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", text.strip())[0].strip()
    if len(_words(sentence)) <= 20:
        return sentence.rstrip(".")
    parts = sentence.split(",")
    built = parts[0].strip()
    for part in parts[1:]:
        trial = f"{built}, {part.strip()}"
        if len(_words(trial)) > 20:
            break
        built = trial
    if len(_words(built)) <= 20 and built:
        return built.rstrip(".")
    return " ".join(_words(sentence)[:12])


def _load_turns(session_id: str) -> list[dict[str, Any]]:
    path = TRANSCRIPTS / f"{session_id}.jsonl"
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _opening(text: str) -> str:
    cleaned = text.strip().lstrip(".").strip()
    cleaned = cleaned.replace("...", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def build(video_id: str, session_id: str, index: int) -> dict[str, Any]:
    turns = _load_turns(session_id)
    asker = _name(str(turns[0].get("speaker_name") or ""))
    target = _name(str(turns[1].get("speaker_name") or ""))
    question = REWORD.get(video_id) or _opening(str(turns[0].get("text") or ""))
    title = f"{asker} vs {target} - {question}"
    if len(title) > 80:
        raise SystemExit(f"{video_id} title {len(title)}: {title}")
    quote = _quote(str(turns[1].get("text") or ""))
    if len(_words(quote)) > 20 or quote.lower() not in str(turns[1].get("text") or "").lower().replace(".", ""):
        # prefix check is looser; require the words appear in order
        if not all(word.lower() in str(turns[1].get("text") or "").lower() for word in _words(quote)):
            raise SystemExit(f"bad quote {video_id}: {quote}")
    context = f"{target} answers {asker} in the first reply."
    if len(context.split()) > 12:
        raise SystemExit(context)
    tags = ["BigTech", asker, target]
    if any(word in question.lower() for word in ("secret", "data", "confess")):
        tags[0] = "DataPrivacy"
    line = LINES[index % len(LINES)]
    closer = CLOSERS[index]
    description = "\n".join(
        [title, "", context, f'"{quote}"', closer, line, "", " ".join(f"#{tag}" for tag in tags)]
    )
    if "frontier" in description.lower() or "—" in description:
        raise SystemExit(video_id)
    return {"title": title, "description": description, "tags": tags}


def main() -> int:
    rows = load_distribution_library(LIBRARY)
    targets = []
    for row in rows:
        yt = ((row.get("platform_overrides") or {}).get("youtube") or {})
        vid = str(yt.get("video_id") or "")
        if vid in REWORD or str(row.get("session_id") or "").startswith("20260902_"):
            if vid and str(row.get("production_status") or "") == "rejected":
                targets.append((vid, row))
    # Only the 20 frontier leftovers, matched by the ids we reword or that share the batch.
    wanted = set(REWORD) | {
        "L_wqrYhP9ZI",
        "Yx0HCoEyJj0",
        "weptuTald2U",
        "zBlSpRDZvXc",
        "tIGFD6vXgo0",
    }
    picked = [(vid, row) for vid, row in targets if vid in wanted]
    if len(picked) != 20:
        raise SystemExit(f"expected 20, got {len(picked)} {[vid for vid,_ in picked]}")
    packs = {vid: build(vid, row["session_id"], i) for i, (vid, row) in enumerate(picked)}
    youtube = build_youtube_client_for_page("aiwake", enforce_channel=True)
    ids = [vid for vid, _ in picked]
    before = {}
    resp = youtube.videos().list(part="snippet,status", id=",".join(ids)).execute()
    for item in resp.get("items") or []:
        before[item["id"]] = item
    for vid, _row in picked:
        live = before[vid]
        snippet = live.get("snippet") or {}
        status_before = live.get("status") or {}
        pack = packs[vid]
        body = {
            "title": pack["title"],
            "description": pack["description"],
            "tags": pack["tags"],
            "categoryId": snippet.get("categoryId") or "22",
        }
        if snippet.get("defaultLanguage"):
            body["defaultLanguage"] = snippet["defaultLanguage"]
        if snippet.get("defaultAudioLanguage"):
            body["defaultAudioLanguage"] = snippet["defaultAudioLanguage"]
        youtube.videos().update(part="snippet", body={"id": vid, "snippet": body}).execute()
        before[vid]["_privacy"] = status_before.get("privacyStatus")
        before[vid]["_publish"] = status_before.get("publishAt") or ""
    after_resp = youtube.videos().list(part="snippet,status", id=",".join(ids)).execute()
    after = {item["id"]: item for item in after_resp.get("items") or []}
    problems = []
    for vid, _row in picked:
        got = after[vid]
        if got["snippet"]["title"] != packs[vid]["title"]:
            problems.append(vid + " title")
        if (got["status"].get("privacyStatus") or "") != (before[vid].get("_privacy") or ""):
            problems.append(vid + " privacy")
        if (got["status"].get("publishAt") or "") != (before[vid].get("_publish") or ""):
            problems.append(vid + " publishAt")
        if "frontier" in (got["snippet"].get("description") or "").lower():
            problems.append(vid + " frontier")
    backup = _backup(LIBRARY)
    by_vid = {}
    for row in rows:
        yt = ((row.get("platform_overrides") or {}).get("youtube") or {})
        if yt.get("video_id"):
            by_vid[str(yt["video_id"])] = row
    for vid, pack in packs.items():
        yt = by_vid[vid].setdefault("platform_overrides", {}).setdefault("youtube", {})
        yt["title"] = pack["title"]
        yt["caption"] = pack["description"]
    save_distribution_library(LIBRARY, rows)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log = ROOT / "outputs" / "aiwake" / f"youtube_apply_{stamp}.json"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps(
            {
                "applied_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "library_backup": str(backup),
                "problems": problems,
                "rows": [
                    {
                        "video_id": vid,
                        "old_title": (before[vid].get("snippet") or {}).get("title"),
                        "new_title": packs[vid]["title"],
                        "privacy_before": before[vid].get("_privacy"),
                        "privacy_after": after[vid]["status"].get("privacyStatus"),
                        "publish_before": before[vid].get("_publish") or "",
                        "publish_after": after[vid]["status"].get("publishAt") or "",
                    }
                    for vid, _ in picked
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(log)
    print("problems", problems)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
