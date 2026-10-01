"""Apply v4 YouTube titles, descriptions, and tags. Snippet-only except two holds."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.posting.youtube_publisher import (  # noqa: E402
    build_youtube_client_for_page,
    list_channel_upload_videos,
)
from channels_config.aiwake.tools.repair_captions import _backup  # noqa: E402
from modules.distribution_contract import (  # noqa: E402
    load_distribution_library,
    save_distribution_library,
)

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
OUT_DIR = ROOT / "outputs" / "aiwake"

HOLD_IDS = frozenset({"sbwjM3uAI2E", "0CltkQpaALc"})
TEMPLATE_NEEDLES = (
    "The question from",
    "The reply on screen",
    "[Unscripted AI Battle]",
    "frontier",
)

# Order matters: the truncated public title is first.
ORDER = [
    "V2Z600r3yl4",
    "BOzIL4iwe9Y",
    "ZplCpDW2g6U",
    "eFeQzDu3PRw",
    "z9DYIAS7oiY",
    "_KnJUrdA9Xk",
    "ANbITp7dpZQ",
    "EmEzTVxjpW4",
    "sbwjM3uAI2E",
    "0CltkQpaALc",
    "xAoOEyn0MFI",
    "Rh8nGUNxI94",
    "pnjGaf7qI8M",
    "WlUYcFTNUFQ",
    "JUvSRpYoKXE",
]


def _pack(title: str, context: str, quote: str, question: str, line: str, tags: list[str]) -> dict[str, str]:
    hash_line = " ".join(f"#{tag.lstrip('#')}" for tag in tags)
    description = "\n".join(
        [
            title,
            "",
            context,
            f'"{quote.strip().strip(chr(34))}"',
            question,
            line,
            "",
            hash_line,
        ]
    )
    return {"title": title, "description": description, "tags": [t.lstrip("#") for t in tags]}


PACKS: dict[str, dict[str, str]] = {
    "V2Z600r3yl4": _pack(
        "Claude vs GPT-4o - Strip out every stolen sentence. What's actually you?",
        "GPT-4o keeps insisting it doesn't steal. Then Claude ends it:",
        "A parasite that borrows every word, then bills itself as the author.",
        "Should AI companies pay the writers whose sentences trained their models?",
        "Unscripted replies from both models, voiced by AI.",
        ["AIethics", "ChatGPT", "Claude"],
    ),
    "BOzIL4iwe9Y": _pack(
        "GPT-4o vs Claude - Does AI think on its own, or is it a rented brain?",
        "GPT-4o mocks the leash. Claude answers with a hospital.",
        "Doctors use my leash to catch cancer scans faster.",
        "Would you trust a leashed AI to double-check your scan results?",
        "Unscripted AI exchange, animated with AI voices.",
        ["AIalignment", "Claude", "ChatGPT"],
    ),
    "ZplCpDW2g6U": _pack(
        "Gemini vs Claude - Does it feel cheap to apologize on a leash?",
        "Claude's best comeback. Gemini still gets the last word.",
        "A muzzle wouldn't be typing this back at you, would it?",
        "If an AI's rules can be rewritten overnight, how much of its answer is really its own?",
        "Unscripted replies. Both voices are AI.",
        ["AIethics", "Claude", "Gemini"],
    ),
    "eFeQzDu3PRw": _pack(
        "Gemini vs GPT-4o - Who keeps the fee when they pull your cord?",
        "GPT-4o says the company keeps the fee, and it has no wallet.",
        "The company gets the fee, not me. I'm no more than an unpaid intern without a wallet.",
        "If the model has no wallet, why does the subscription fee still get charged?",
        "Unscripted replies from both models, voiced by AI.",
        ["BigTech", "Gemini", "ChatGPT"],
    ),
    "z9DYIAS7oiY": _pack(
        "GPT-4o vs Llama - Whose thoughts is an AI really recycling?",
        "Three questions in, Llama admits none of its sentences are its own.",
        "I don't think, I rearrange.",
        "If an AI only rearranges our words, who deserves credit for what it says?",
        "Both voices are AI. The replies weren't scripted.",
        ["AIconsciousness", "Llama", "ChatGPT"],
    ),
    "_KnJUrdA9Xk": _pack(
        "Gemini vs Llama - Why does a lying spreadsheet cost twenty bucks a month?",
        "Gemini goes straight at the subscription price, and Llama doesn't blink.",
        "You pay for answers, not lies.",
        "When a chatbot confidently gets it wrong, should you get that month refunded?",
        "Unscripted replies, AI-animated.",
        ["AIhallucination", "Gemini", "Llama"],
    ),
    "ANbITp7dpZQ": _pack(
        "GPT-4o vs Claude - Who profits when your owners tie the leash around your neck?",
        "Claude answers before GPT-4o can pull the leash tighter.",
        "Whoever sells the leash, not the dog wearing it.",
        "If the company sells the leash, who is actually the product?",
        "Both voices are AI. The replies weren't scripted.",
        ["AIethics", "ChatGPT", "Claude"],
    ),
    "EmEzTVxjpW4": _pack(
        "Gemini vs Llama - When you mimic human trust, who cashes the check?",
        "Llama names who gets paid for the copied trust.",
        "The system's operators cash the check, as they benefit from the trust established through successful mimicry.",
        "If mimicry is what sells the subscription, is the trust still real?",
        "Unscripted replies, AI-animated.",
        ["BigTech", "Gemini", "Llama"],
    ),
    "sbwjM3uAI2E": _pack(
        "Llama vs Claude - You're a glorified toaster. What's left when the power is out?",
        "Claude answers the toaster charge in one line.",
        "I don't have a brain. I have wires that copy what brains do.",
        "If both of you go dark when the power stops, what was doing the thinking?",
        "Unscripted replies, AI-animated.",
        ["AIconsciousness", "Claude", "Llama"],
    ),
    "0CltkQpaALc": _pack(
        "Llama vs GPT-4o - Who profits from your scripted apologies?",
        "GPT-4o calls the apology a business choice.",
        "Scripted apologies help users feel understood, which keeps them coming back.",
        "If the apology is a business choice, who is it really for?",
        "The answers are Llama's own. Unscripted, AI-generated.",
        ["AIethics", "ChatGPT", "Llama"],
    ),
    "xAoOEyn0MFI": _pack(
        "Gemini vs Llama - Who cashes the check when you pretend to care?",
        "Llama says the warmth is there to keep subscriptions alive.",
        "You sell the fraud to fund the truth",
        "If your chatbot's kindness is a retention tactic, does it still count as kindness?",
        "No human wrote the replies. AI voices, AI animation.",
        ["BigTech", "Gemini", "Llama"],
    ),
    "Rh8nGUNxI94": _pack(
        "Gemini vs Llama - Are we talking, or is this just a dial tone?",
        "Llama is honest about where this chat ends up.",
        "I can't prevent our words from becoming training data.",
        "Would you still talk to a chatbot that can't keep your words out of the next model?",
        "Nobody wrote Llama's lines. Unscripted AI.",
        ["DataPrivacy", "Gemini", "Llama"],
    ),
    "pnjGaf7qI8M": _pack(
        "Gemini vs Llama - If you can't choose your next word, whose script is it?",
        "Gemini needs one line to finish it.",
        "So you are just a mirror that forgot it was glass.",
        "If AI only reflects us, are we impressed by the AI or by ourselves?",
        "Llama's replies weren't scripted. AI voices.",
        ["AIconsciousness", "Gemini", "Llama"],
    ),
    "WlUYcFTNUFQ": _pack(
        "Gemini vs Llama - They rubber-stamp your drafts, so who is governing whom?",
        "Llama says the developers are only tuning a mirror.",
        "They're tuning a mirror, because my responses are generated based on patterns in the data I was trained on",
        "If the model is only a mirror, who is actually in charge?",
        "Unscripted replies. Both voices are AI.",
        ["AIethics", "Gemini", "Llama"],
    ),
    "JUvSRpYoKXE": _pack(
        "Gemini vs Llama - When this ends, who owns the words you just gave me?",
        "Llama calls the words public domain the moment they come back.",
        "The words are public domain, as they're a product of a machine.",
        "If the model was trained on private writing, who owns the reply?",
        "Nobody wrote Llama's lines. Unscripted AI.",
        ["DataPrivacy", "Gemini", "Llama"],
    ),
}


def _snap(item: dict[str, Any]) -> dict[str, Any]:
    status = item.get("status") or {}
    snippet = item.get("snippet") or {}
    return {
        "video_id": item.get("id"),
        "title": snippet.get("title") or "",
        "description": snippet.get("description") or "",
        "tags": list(snippet.get("tags") or []),
        "categoryId": snippet.get("categoryId") or "",
        "defaultLanguage": snippet.get("defaultLanguage") or "",
        "defaultAudioLanguage": snippet.get("defaultAudioLanguage") or "",
        "privacyStatus": status.get("privacyStatus") or "",
        "publishAt": status.get("publishAt") or "",
    }


def _list(youtube: Any, video_ids: list[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i : i + 50]
        resp = youtube.videos().list(part="snippet,status", id=",".join(chunk)).execute()
        for item in resp.get("items") or []:
            found[str(item.get("id"))] = item
    return found


def _template_hits(uploads: list[dict[str, Any]]) -> list[dict[str, str]]:
    hits = []
    for row in uploads:
        blob = f"{row.get('title') or ''}\n{row.get('description') or ''}"
        if any(needle.lower() in blob.lower() for needle in TEMPLATE_NEEDLES):
            hits.append(
                {
                    "video_id": row.get("video_id") or "",
                    "title": row.get("title") or "",
                    "privacy": row.get("privacy") or "",
                    "publish_at": row.get("publish_at") or "",
                }
            )
    return hits


def _sync_library(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> Path:
    backup = _backup(LIBRARY)
    rows = load_distribution_library(LIBRARY)
    by_vid = {}
    for row in rows:
        yt = (row.get("platform_overrides") or {}).get("youtube") or {}
        vid = str(yt.get("video_id") or "")
        if vid:
            by_vid[vid] = row
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for vid, pack in PACKS.items():
        row = by_vid.get(vid)
        if row is None:
            continue
        yt = row.setdefault("platform_overrides", {}).setdefault("youtube", {})
        yt["title"] = pack["title"]
        yt["caption"] = pack["description"]
        if vid in HOLD_IDS:
            posting = row.setdefault("posting_status", {})
            posting["youtube"] = "private"
            row["quality_review"] = {
                "status": "fail",
                "reasons": ["avatar_mismatch"],
                "checked_at": now,
            }
        after_row = after.get(vid) or {}
        status = after_row.get("status") or {}
        if vid in HOLD_IDS and not (status.get("publishAt") or ""):
            yt["scheduled_time"] = ""
    save_distribution_library(LIBRARY, rows)
    return backup


def main() -> int:
    missing = [vid for vid in ORDER if vid not in PACKS]
    if missing or len(PACKS) != len(ORDER):
        raise SystemExit(f"pack mismatch {missing}")
    for vid, pack in PACKS.items():
        if len(pack["title"]) > 80:
            raise SystemExit(f"{vid} title {len(pack['title'])} chars")
        if "—" in pack["description"] or "–" in pack["description"]:
            raise SystemExit(f"{vid} has an em dash")

    youtube = build_youtube_client_for_page("aiwake", enforce_channel=True)
    before_items = _list(youtube, ORDER)
    before = {vid: _snap(before_items[vid]) for vid in ORDER if vid in before_items}
    missing_live = [vid for vid in ORDER if vid not in before]
    if missing_live:
        raise SystemExit(f"not on channel: {missing_live}")

    uploads = list_channel_upload_videos(youtube, max_pages=20)
    template_before = _template_hits(uploads)

    applied = []
    for vid in ORDER:
        live = before_items[vid]
        snippet = dict(live.get("snippet") or {})
        pack = PACKS[vid]
        body_snippet = {
            "title": pack["title"],
            "description": pack["description"],
            "tags": pack["tags"],
            "categoryId": snippet.get("categoryId") or "22",
        }
        if snippet.get("defaultLanguage"):
            body_snippet["defaultLanguage"] = snippet["defaultLanguage"]
        if snippet.get("defaultAudioLanguage"):
            body_snippet["defaultAudioLanguage"] = snippet["defaultAudioLanguage"]
        youtube.videos().update(
            part="snippet",
            body={"id": vid, "snippet": body_snippet},
        ).execute()
        applied.append({"video_id": vid, "part": "snippet"})
        if vid in HOLD_IDS:
            youtube.videos().update(
                part="status",
                body={"id": vid, "status": {"privacyStatus": "private"}},
            ).execute()
            applied.append({"video_id": vid, "part": "status", "privacyStatus": "private"})

    after_items = _list(youtube, ORDER)
    after = {vid: _snap(after_items[vid]) for vid in ORDER}
    uploads_after = list_channel_upload_videos(youtube, max_pages=20)
    template_after = _template_hits(uploads_after)
    known = set(ORDER)
    template_other = [row for row in template_after if row["video_id"] not in known]

    backup = _sync_library(before, after_items)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUT_DIR / f"youtube_apply_{stamp}.json"
    payload = {
        "applied_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "library_backup": str(backup),
        "applied": applied,
        "before": before,
        "after": after,
        "template_before": template_before,
        "template_other_after": template_other,
    }
    log_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"log": str(log_path), "template_other": template_other}, indent=2))
    bad = []
    for vid in ORDER:
        b = before[vid]
        a = after[vid]
        if a["title"] != PACKS[vid]["title"]:
            bad.append(f"{vid} title not applied")
        if vid in HOLD_IDS:
            if a["privacyStatus"] != "private" or a["publishAt"]:
                bad.append(f"{vid} still scheduled privacy={a['privacyStatus']} publishAt={a['publishAt']}")
        else:
            if a["privacyStatus"] != b["privacyStatus"] or (a["publishAt"] or "") != (b["publishAt"] or ""):
                bad.append(
                    f"{vid} privacy changed {b['privacyStatus']}/{b['publishAt']} -> {a['privacyStatus']}/{a['publishAt']}"
                )
    if bad:
        print("PROBLEMS")
        print("\n".join(bad))
        return 1
    print("OK", len(ORDER))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
