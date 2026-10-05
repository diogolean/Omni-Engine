"""Rewrite newest-150 captions: full questions, real closers, fixed disclosure.

Does not upload or change YouTube privacy. ``--youtube`` retitles cut-off
uploads with a snippet update only, then re-lists them.
"""
from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from channels_config.aiwake.tools.caption_generator import (  # noqa: E402
    UNSCRIPTED_LINES,
    categories_of,
    closer_repeats_source,
    display_name,
    headline_for,
    is_complete_question,
    is_cutoff_title,
)
from channels_config.aiwake.tools.production_status import posting_order  # noqa: E402
from channels_config.aiwake.tools.repair_captions import _backup  # noqa: E402
from channels_config.aiwake.tools.scope_qc_captions_v4 import (  # noqa: E402
    _CLOSER_BANK,
    _jsonl_turns,
    compose,
)
from channels_config.aiwake.tools.validate_aiwake_captions import (  # noqa: E402
    _closing,
    _lines,
    _ready_ok,
    validate_library,
)
from modules.distribution_contract import load_distribution_library, save_distribution_library  # noqa: E402

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
RERENDERED = {
    "20260928_221907_13550e",  # fancy parrot
    "20260928_220547_711701",  # toaster
    "20260928_215814_ce428c",  # scripted apologies
}
_FEED = ("tiktok", "instagram", "facebook", "youtube", "kwai")


def _newest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scoped = []
    for row in rows:
        video = Path(str(row.get("video_path") or ""))
        if video.suffix.lower() == ".mp4" and video.parent.name == "animation_clips":
            scoped.append(row)
    scoped.sort(key=lambda row: str(row.get("session_id") or ""), reverse=True)
    return scoped[:150]


def _opening(row: dict[str, Any]) -> str:
    for item in row.get("spoken_utterances") or []:
        if isinstance(item, dict) and item.get("role") == "orchestrator" and item.get("text"):
            return str(item["text"])
    return ""


def _names(row: dict[str, Any]) -> tuple[str, str]:
    spoken = [item for item in (row.get("spoken_utterances") or []) if isinstance(item, dict)]
    asker = display_name(str(next((item.get("speaker") for item in spoken if item.get("role") == "orchestrator"), "")))
    answerer = display_name(str(next((item.get("speaker") for item in spoken if item.get("role") == "target"), "")))
    return asker, answerer


def _ensure_category(row: dict[str, Any]) -> None:
    if categories_of(row):
        return
    for turn in _jsonl_turns(str(row.get("session_id") or "")):
        category = str(turn.get("provocation_category") or "").strip()
        if not category:
            continue
        for item in row.get("spoken_utterances") or []:
            if isinstance(item, dict) and item.get("role") == "orchestrator":
                item["category"] = category
                return


def _replace_title(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: _replace_title(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_title(item, old, new) for item in value]
    if not isinstance(value, str) or not old:
        return value
    if value.strip() == old.strip():
        return new
    lines = value.splitlines()
    if lines and lines[0].strip() == old.strip():
        lines[0] = new
        return "\n".join(lines)
    return value


def _set_disclosure(caption: str, line: str) -> str:
    parts = caption.splitlines()
    for index, raw in enumerate(parts):
        stripped = raw.strip()
        if stripped in UNSCRIPTED_LINES or "unscripted" in stripped.lower():
            parts[index] = line
            return "\n".join(parts)
    for index, raw in enumerate(parts):
        if raw.strip().startswith("#"):
            parts.insert(index, "")
            parts.insert(index, line)
            return "\n".join(parts)
    parts.extend(["", line])
    return "\n".join(parts)


def _set_closer(caption: str, old: str, new: str) -> str:
    if not old or old == new:
        return caption
    parts = caption.splitlines()
    first = next((index for index, raw in enumerate(parts) if raw.strip()), None)
    for index, raw in enumerate(parts):
        if index == first:
            continue
        if raw.strip() == old.strip() or (old.strip() and raw.strip().endswith(old.strip())):
            parts[index] = new
            return "\n".join(parts)
    return caption


def _shrink(caption: str, limit: int) -> str:
    if len(caption) <= limit:
        return caption
    parts = caption.splitlines()
    content = [(index, raw) for index, raw in enumerate(parts) if raw.strip()]
    protected = set()
    if content:
        protected.add(content[0][0])
    for index, raw in content:
        stripped = raw.strip()
        if stripped.startswith("#") or stripped.startswith('"') or stripped in UNSCRIPTED_LINES or "unscripted" in stripped.lower():
            protected.add(index)
    questions = [index for index, raw in content if raw.strip().endswith("?") and index not in protected]
    if questions:
        protected.add(questions[-1])
    droppable = [index for index, _raw in content if index not in protected]
    droppable.sort(key=lambda index: len(parts[index]), reverse=True)
    for index in droppable:
        if len("\n".join(parts)) <= limit:
            break
        parts[index] = ""
    return "\n".join(parts)


def _pick_closer(row: dict[str, Any], quote: str, used: list[str]) -> str:
    spoken = [str(item.get("text") or "") for item in (row.get("spoken_utterances") or []) if isinstance(item, dict)]
    for closer in _CLOSER_BANK:
        if closer_repeats_source(closer, quote, spoken):
            continue
        if any(difflib.SequenceMatcher(None, closer.lower(), other.lower()).ratio() >= 0.65 for other in used):
            continue
        return closer
    fallback = f"Would you stand by reply {len(used) + 1}?"
    return fallback


def _current_title(row: dict[str, Any]) -> str:
    youtube = ((row.get("platform_overrides") or {}).get("youtube") or {})
    return str(youtube.get("title") or (row.get("base_metadata") or {}).get("title") or "")


def _unique_title(title: str, opening: str, used: set[str]) -> str:
    if title.lower() not in used:
        used.add(title.lower())
        return title
    prefix, question = title.split(" - ", 1)
    extras = [
        word.strip(".,?\"'")
        for word in opening.split()
        if len(word.strip(".,?\"'")) > 4 and word.lower().strip(".,?\"'") not in title.lower()
    ]
    for word in extras:
        candidate = f"{prefix} - {question[:-1]} after {word}?"
        topic = candidate.split(" - ", 1)[1]
        if len(candidate) <= 80 and candidate.lower() not in used and is_complete_question(topic):
            used.add(candidate.lower())
            return candidate
    candidate = title[:-1] + " now?"
    used.add(candidate.lower())
    return candidate


def _apply_row(row: dict[str, Any], index: int, used_closers: list[str], used_titles: set[str]) -> str:
    _ensure_category(row)
    asker, answerer = _names(row)
    opening = _opening(row)
    if not asker or not answerer or not opening:
        return "skipped"
    new_title = _unique_title(headline_for(asker, answerer, opening), opening, used_titles)
    old_title = _current_title(row)
    if old_title and old_title != new_title:
        replaced = _replace_title(row, old_title, new_title)
        row.clear()
        row.update(replaced)
    disclosure = UNSCRIPTED_LINES[index % len(UNSCRIPTED_LINES)]
    overrides = row.setdefault("platform_overrides", {})
    for name in list(overrides):
        block = overrides.get(name)
        if isinstance(block, dict) and isinstance(block.get("caption"), str):
            block["caption"] = _set_disclosure(block["caption"], disclosure)
    for field in ("final_caption", "humanized_caption", "post_planner_caption", "tiktok_caption", "facebook_caption", "linkedin_caption"):
        if isinstance(row.get(field), str) and "unscripted" in row[field].lower():
            row[field] = _set_disclosure(row[field], disclosure)
    tiktok = ((overrides.get("tiktok") or {}).get("caption") or "")
    closer = _closing(tiktok)
    quote = str((row.get("caption_qa") or {}).get("quote") or "")
    spoken = [str(item.get("text") or "") for item in (row.get("spoken_utterances") or []) if isinstance(item, dict)]
    if closer and closer_repeats_source(closer, quote, spoken):
        replacement = _pick_closer(row, quote, used_closers)
        for name in _FEED:
            block = overrides.get(name)
            if isinstance(block, dict) and isinstance(block.get("caption"), str):
                block["caption"] = _set_closer(block["caption"], closer, replacement)
        for field in ("final_caption", "humanized_caption", "post_planner_caption", "tiktok_caption", "facebook_caption", "linkedin_caption"):
            if isinstance(row.get(field), str):
                row[field] = _set_closer(row[field], closer, replacement)
        closer = replacement
    if closer:
        used_closers.append(closer)
    tiktok_block = overrides.setdefault("tiktok", {})
    if isinstance(tiktok_block.get("caption"), str) and len(tiktok_block["caption"]) > 300:
        tiktok_block["caption"] = _shrink(tiktok_block["caption"], 300)
        row["tiktok_caption"] = tiktok_block["caption"]
        row["post_planner_caption"] = tiktok_block["caption"]
        row["humanized_caption"] = tiktok_block["caption"]
    youtube = overrides.setdefault("youtube", {})
    youtube["title"] = new_title
    base = row.setdefault("base_metadata", {})
    base["title"] = new_title
    qa = row.setdefault("caption_qa", {})
    if isinstance(qa, dict):
        qa["disclosure_line"] = disclosure
    return "updated"


def _recompose(row: dict[str, Any], index: int, used_quotes: set[str], used_titles: set[str]) -> str:
    from channels_config.aiwake.tools.caption_generator import apply_caption_pack

    _ensure_category(row)
    pack = compose(row, index, used_quotes, used_titles)
    if pack is None:
        return "compose_failed"
    apply_caption_pack(row, pack)
    qa = row.setdefault("caption_qa", {})
    qa["approval"] = "pending_approval"
    return "recomposed"


def repair(rows: list[dict[str, Any]]) -> dict[str, int]:
    newest = {str(row.get("session_id") or "") for row in _newest(rows)}
    used_closers: list[str] = []
    for row in rows:
        if str(row.get("session_id") or "") in newest and _ready_ok(row):
            tiktok = str((((row.get("platform_overrides") or {}).get("tiktok") or {}).get("caption") or ""))
            closer = _closing(tiktok)
            if closer:
                used_closers.append(closer)
    counts = {"updated": 0, "recomposed": 0, "skipped": 0, "compose_failed": 0}
    used_quotes: set[str] = set()
    used_titles: set[str] = set()
    index = 0
    for row in _newest(rows):
        sid = str(row.get("session_id") or "")
        if sid in RERENDERED:
            status = _recompose(row, index, used_quotes, used_titles)
            counts[status] = counts.get(status, 0) + 1
            index += 1
            continue
        if sid not in newest or not _ready_ok(row):
            continue
        status = _apply_row(row, index, used_closers, used_titles)
        counts[status] = counts.get(status, 0) + 1
        index += 1
    active = posting_order([row for row in rows if _ready_ok(row)])
    seen_closers: list[str] = []
    for row in active:
        overrides = row.get("platform_overrides") or {}
        tiktok = str(((overrides.get("tiktok") or {}).get("caption") or ""))
        closer = _closing(tiktok)
        quote = str((row.get("caption_qa") or {}).get("quote") or "")
        spoken = [str(item.get("text") or "") for item in (row.get("spoken_utterances") or []) if isinstance(item, dict)]
        conflict = bool(closer) and (
            closer in seen_closers
            or any(difflib.SequenceMatcher(None, closer.lower(), other.lower()).ratio() >= 0.65 for other in seen_closers)
            or closer_repeats_source(closer, quote, spoken)
        )
        if conflict:
            replacement = _pick_closer(row, quote, seen_closers)
            for name in _FEED:
                block = overrides.get(name)
                if isinstance(block, dict) and isinstance(block.get("caption"), str):
                    block["caption"] = _set_closer(block["caption"], closer, replacement)
            closer = replacement
        if closer:
            seen_closers.append(closer)
        block = overrides.get("tiktok")
        if isinstance(block, dict) and isinstance(block.get("caption"), str) and len(block["caption"]) > 300:
            quote = str((row.get("caption_qa") or {}).get("quote") or "")
            short = _pick_closer(row, quote, seen_closers)
            current = _closing(block["caption"])
            if current:
                block["caption"] = _set_closer(block["caption"], current, short)
                if closer in seen_closers:
                    seen_closers.remove(closer)
                closer = short
                seen_closers.append(short)
            block["caption"] = _set_disclosure(block["caption"], UNSCRIPTED_LINES[0])
            block["caption"] = _shrink(block["caption"], 300)
            row["tiktok_caption"] = block["caption"]
            row["post_planner_caption"] = block["caption"]
            row["humanized_caption"] = block["caption"]
    for position, row in enumerate(active):
        qa = row.get("caption_qa") if isinstance(row.get("caption_qa"), dict) else {}
        qa["angle"] = "ABCD"[position % 4]
        row["caption_qa"] = qa
    return counts


def cutoff_uploads(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    newest = {str(row.get("session_id") or "") for row in _newest(rows)}
    found = []
    for row in rows:
        if str(row.get("session_id") or "") not in newest:
            continue
        youtube = ((row.get("platform_overrides") or {}).get("youtube") or {})
        video_id = str(youtube.get("video_id") or "").strip()
        title = str(youtube.get("title") or "")
        opening = _opening(row)
        if video_id and opening and is_cutoff_title(title, opening):
            asker, answerer = _names(row)
            found.append({
                "video_id": video_id,
                "session_id": str(row.get("session_id") or ""),
                "old": title,
                "new": headline_for(asker, answerer, opening) if asker and answerer else title,
            })
    return found


def retitle_youtube(planned: list[dict[str, str]]) -> dict[str, Any]:
    from agents.posting.youtube_publisher import build_youtube_client_for_page

    youtube = build_youtube_client_for_page("aiwake", enforce_channel=True)
    ids = [item["video_id"] for item in planned]
    before = _list(youtube, ids)
    applied = []
    for item in planned:
        current = before.get(item["video_id"])
        if current is None:
            applied.append({**item, "error": "missing"})
            continue
        new_title = item["new"]
        if not is_complete_question(new_title.split(" - ", 1)[1]):
            applied.append({**item, "error": "new_title_incomplete"})
            continue
        snippet = dict(current.get("snippet") or {})
        body = {
            "title": new_title,
            "description": snippet.get("description") or "",
            "categoryId": snippet.get("categoryId") or "28",
        }
        if snippet.get("tags"):
            body["tags"] = snippet["tags"]
        if snippet.get("defaultLanguage"):
            body["defaultLanguage"] = snippet["defaultLanguage"]
        if snippet.get("defaultAudioLanguage"):
            body["defaultAudioLanguage"] = snippet["defaultAudioLanguage"]
        privacy_before = ((current.get("status") or {}).get("privacyStatus") or "")
        publish_before = (current.get("status") or {}).get("publishAt")
        youtube.videos().update(part="snippet", body={"id": item["video_id"], "snippet": body}).execute()
        applied.append({
            "video_id": item["video_id"],
            "old": item["old"],
            "new": new_title,
            "privacy_before": privacy_before,
            "publishAt_before": publish_before,
        })
    after = _list(youtube, ids)
    incomplete = []
    privacy_changed = []
    for item in applied:
        if item.get("error"):
            incomplete.append(item["video_id"])
            continue
        live = after.get(item["video_id"]) or {}
        status = live.get("status") or {}
        snippet = live.get("snippet") or {}
        topic = str(snippet.get("title") or "").split(" - ", 1)[-1]
        if not is_complete_question(topic) or snippet.get("title") != item["new"]:
            incomplete.append(item["video_id"])
        if status.get("privacyStatus") != item.get("privacy_before"):
            privacy_changed.append(item["video_id"])
        if (status.get("publishAt") or None) != (item.get("publishAt_before") or None):
            privacy_changed.append(item["video_id"] + ":schedule")
    return {"applied": len(applied), "incomplete": incomplete, "privacy_changed": privacy_changed, "after": {
        vid: {
            "title": (item.get("snippet") or {}).get("title"),
            "privacy": (item.get("status") or {}).get("privacyStatus"),
            "publishAt": (item.get("status") or {}).get("publishAt"),
        }
        for vid, item in after.items()
    }}


def _list(youtube: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), 50):
        response = youtube.videos().list(part="snippet,status", id=",".join(ids[start : start + 50])).execute()
        for item in response.get("items") or []:
            found[item["id"]] = item
    return found


def main() -> int:
    rows = load_distribution_library(LIBRARY)
    plan_path = ROOT / "outputs" / "aiwake" / "retitle_complete_plan.json"
    if "--youtube" in sys.argv:
        if not plan_path.is_file():
            raise SystemExit(f"missing {plan_path}")
        planned = json.loads(plan_path.read_text(encoding="utf-8"))
        by_id = {}
        for row in rows:
            youtube = ((row.get("platform_overrides") or {}).get("youtube") or {})
            video_id = str(youtube.get("video_id") or "").strip()
            if video_id:
                by_id[video_id] = str(youtube.get("title") or "")
        for item in planned:
            item["new"] = by_id.get(item["video_id"], item["new"])
        result = retitle_youtube(planned)
        out = ROOT / "outputs" / "aiwake" / "retitle_complete_20261004.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print("youtube", {key: result[key] for key in ("applied", "incomplete", "privacy_changed")})
        return 1 if result["incomplete"] or result["privacy_changed"] else 0
    planned = cutoff_uploads(rows)
    print("cutoff_uploads", len(planned))
    counts = repair(rows)
    code, grouped = validate_library(rows)
    print("repair", counts, "validator", code)
    if code:
        for rule, items in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            print(rule, len(items))
            for item in items[:8]:
                print(" ", item[:180])
    if "--apply" in sys.argv and code == 0:
        by_id = {}
        for row in rows:
            youtube = ((row.get("platform_overrides") or {}).get("youtube") or {})
            video_id = str(youtube.get("video_id") or "").strip()
            if video_id:
                by_id[video_id] = str(youtube.get("title") or "")
        for item in planned:
            item["new"] = by_id.get(item["video_id"], item["new"])
        plan_path = ROOT / "outputs" / "aiwake" / "retitle_complete_plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(planned, indent=2, ensure_ascii=False), encoding="utf-8")
        backup = _backup(LIBRARY)
        save_distribution_library(LIBRARY, rows)
        print("backup", backup.name, "rows", len(rows), "plan", len(planned))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
