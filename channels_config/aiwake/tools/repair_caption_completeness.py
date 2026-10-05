"""Rewrite newest-150 captions: full questions, real closers, fixed disclosure.

Does not upload or change YouTube privacy. ``--youtube`` retitles cut-off
uploads with a snippet update only, then re-lists them.
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from channels_config.aiwake.tools.caption_generator import (  # noqa: E402
    UNSCRIPTED_LINES,
    categories_of,
    closer_about_reply,
    closer_equals_quote,
    closer_has_topic_word,
    closer_repeats_source,
    display_name,
    full_topic_question,
    headline_for,
    is_complete_question,
    is_cutoff_title,
    is_when_if_fragment,
    _noun_phrase,
    side_closer,
    topic_words,
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
_TEXT_FIELDS = (
    "final_caption",
    "humanized_caption",
    "post_planner_caption",
    "tiktok_caption",
    "facebook_caption",
    "linkedin_caption",
)
_FILLER = re.compile(r"Unscripted AI [A-Za-z]+\.")


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


def _needs_new_closer(closer: str, opening: str, title: str, quote: str, spoken: list[str]) -> bool:
    topic = title.split(" - ", 1)[-1] if " - " in title else title
    if not closer:
        return True
    if closer_about_reply(closer) or closer_equals_quote(closer, quote) or is_when_if_fragment(closer):
        return True
    if closer_repeats_source(closer, quote, spoken):
        return True
    return not closer_has_topic_word(closer, opening, topic)


def _rewrite_caption_text(
    text: str,
    *,
    old_title: str,
    new_title: str,
    old_closer: str,
    new_closer: str,
    disclosure: str,
) -> str:
    if not text:
        return text
    updated = text
    if old_title and new_title and old_title != new_title:
        updated = updated.replace(old_title, new_title)
        lines = updated.splitlines()
        for index, raw in enumerate(lines):
            if " vs " in raw and " - " in raw:
                lines[index] = new_title
                break
        updated = "\n".join(lines)
    if disclosure:
        updated = _FILLER.sub(disclosure, updated)
        if disclosure not in updated and "unscripted" not in updated.lower() and "AI voices" not in updated:
            updated = _set_disclosure(updated, disclosure)
    if old_closer and new_closer and old_closer != new_closer:
        updated = _set_closer(updated, old_closer, new_closer)
    current = _closing(updated)
    if new_closer and current and current != new_closer and (
        closer_about_reply(current) or closer_equals_quote(current, old_closer)
    ):
        updated = _set_closer(updated, current, new_closer)
    return updated


def repair_round2(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Topic-sided closers, full questions for When/If stubs, synced caption fields."""
    newest = _newest(rows)
    counts = {"closers": 0, "fragments": 0, "synced": 0}
    used: list[str] = []
    grams: dict[tuple[str, ...], int] = {}
    side_closer._masked_counts = {}
    ready_first = [row for row in newest if _ready_ok(row)]
    rest = [row for row in newest if not _ready_ok(row)]
    for row in ready_first:
        title = _current_title(row)
        opening = _opening(row)
        quote = str((row.get("caption_qa") or {}).get("quote") or "")
        spoken = [str(item.get("text") or "") for item in (row.get("spoken_utterances") or []) if isinstance(item, dict)]
        tiktok = str((((row.get("platform_overrides") or {}).get("tiktok") or {}).get("caption") or ""))
        closer = _closing(tiktok)
        topic = title.split(" - ", 1)[-1] if " - " in title else title
        if closer and not _needs_new_closer(closer, opening, title, quote, spoken) and not is_when_if_fragment(topic):
            used.append(closer)
    for index, row in enumerate(ready_first + rest):
        asker, answerer = _names(row)
        opening = _opening(row)
        title = _current_title(row)
        topic = title.split(" - ", 1)[-1] if " - " in title else title
        quote = str((row.get("caption_qa") or {}).get("quote") or "")
        spoken = [str(item.get("text") or "") for item in (row.get("spoken_utterances") or []) if isinstance(item, dict)]
        overrides = row.setdefault("platform_overrides", {})
        tiktok = str(((overrides.get("tiktok") or {}).get("caption") or ""))
        old_closer = _closing(tiktok)
        new_title = title
        if asker and answerer and opening and is_when_if_fragment(topic) and _ready_ok(row):
            prefix = f"{asker} vs {answerer} - "
            question = full_topic_question(opening, limit=80 - len(prefix))
            if question and not is_when_if_fragment(question) and len(prefix + question) <= 80:
                candidate = prefix + question
                if candidate.lower() not in {item.lower() for item in used}:
                    new_title = candidate
                    counts["fragments"] += 1
        new_closer = old_closer
        if _needs_new_closer(old_closer, opening, new_title, quote, spoken):
            new_closer = side_closer(opening, new_title, quote, spoken, used, grams)
            counts["closers"] += 1
        if new_closer:
            used.append(new_closer)
        disclosure = ""
        for line in _lines(tiktok):
            if line in UNSCRIPTED_LINES:
                disclosure = line
                break
        if not disclosure:
            disclosure = UNSCRIPTED_LINES[index % len(UNSCRIPTED_LINES)]
        changed = new_title != title or new_closer != old_closer
        if not changed and not _FILLER.search(json.dumps(row)):
            continue
        for name, block in list(overrides.items()):
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("caption"), str):
                block["caption"] = _rewrite_caption_text(
                    block["caption"],
                    old_title=title,
                    new_title=new_title,
                    old_closer=old_closer,
                    new_closer=new_closer,
                    disclosure=disclosure,
                )
            if isinstance(block.get("title"), str) and title and block["title"] == title:
                block["title"] = new_title
            if isinstance(block.get("description"), str) and block["description"] == old_closer:
                block["description"] = new_closer
        for field in _TEXT_FIELDS:
            if isinstance(row.get(field), str):
                row[field] = _rewrite_caption_text(
                    row[field],
                    old_title=title,
                    new_title=new_title,
                    old_closer=old_closer,
                    new_closer=new_closer,
                    disclosure=disclosure,
                )
        base = row.setdefault("base_metadata", {})
        if isinstance(base, dict):
            if isinstance(base.get("caption"), str):
                base["caption"] = _rewrite_caption_text(
                    base["caption"],
                    old_title=title,
                    new_title=new_title,
                    old_closer=old_closer,
                    new_closer=new_closer,
                    disclosure=disclosure,
                )
            if isinstance(base.get("title"), str):
                base["title"] = new_title
        youtube = overrides.setdefault("youtube", {})
        if isinstance(youtube, dict):
            youtube["title"] = new_title
        tiktok_block = overrides.get("tiktok") if isinstance(overrides.get("tiktok"), dict) else {}
        tiktok_caption = str(tiktok_block.get("caption") or "")
        if len(tiktok_caption) > 300 and new_closer:
            budget = 300 - (len(tiktok_caption) - len(new_closer))
            short = side_closer(opening, new_title, quote, spoken, used, grams, max_len=max(budget, 24))
            if short != new_closer and len(tiktok_caption) - len(new_closer) + len(short) <= 300:
                for name, block in list(overrides.items()):
                    if isinstance(block, dict) and isinstance(block.get("caption"), str):
                        block["caption"] = block["caption"].replace(new_closer, short, 1)
                for field in _TEXT_FIELDS:
                    if isinstance(row.get(field), str):
                        row[field] = row[field].replace(new_closer, short, 1)
                if isinstance(base, dict) and isinstance(base.get("caption"), str):
                    base["caption"] = base["caption"].replace(new_closer, short, 1)
                new_closer = short
                used.append(short)
        if changed:
            counts["synced"] += 1
    return counts


def sync_caption_fields(rows: list[dict[str, Any]]) -> int:
    """Copy the TikTok closer and title into every caption field."""
    changed = 0
    for row in _newest(rows):
        overrides = row.get("platform_overrides") or {}
        tiktok = str(((overrides.get("tiktok") or {}).get("caption") or ""))
        closer = _closing(tiktok)
        title = _current_title(row)
        if not closer or not title:
            continue
        for block in overrides.values():
            if isinstance(block, dict) and isinstance(block.get("caption"), str):
                current = _closing(block["caption"])
                updated = block["caption"]
                if current and current != closer:
                    updated = _set_closer(updated, current, closer)
                    if _closing(updated) != closer:
                        updated = updated.replace(current, closer, 1)
                if title not in updated and " vs " in updated:
                    lines = updated.splitlines()
                    for index, raw in enumerate(lines):
                        if " vs " in raw and " - " in raw:
                            lines[index] = title
                            break
                    updated = "\n".join(lines)
                if updated != block["caption"]:
                    block["caption"] = updated
                    changed += 1
        for field in _TEXT_FIELDS:
            text = row.get(field)
            if not isinstance(text, str) or not text.strip():
                continue
            current = _closing(text)
            updated = text
            if current and current != closer:
                updated = _set_closer(updated, current, closer)
                if _closing(updated) != closer:
                    updated = updated.replace(current, closer, 1)
            if title not in updated:
                lines = updated.splitlines()
                for index, raw in enumerate(lines):
                    if " vs " in raw and " - " in raw:
                        lines[index] = title
                        break
                updated = "\n".join(lines)
            if updated != text:
                row[field] = updated
                changed += 1
        base = row.get("base_metadata")
        if isinstance(base, dict):
            base["title"] = title
            text = base.get("caption")
            if isinstance(text, str) and text.strip():
                current = _closing(text)
                updated = text
                if current and current != closer:
                    updated = _set_closer(updated, current, closer)
                    if _closing(updated) != closer:
                        updated = updated.replace(current, closer, 1)
                if title not in updated:
                    lines = updated.splitlines()
                    for index, raw in enumerate(lines):
                        if " vs " in raw and " - " in raw:
                            lines[index] = title
                            break
                    updated = "\n".join(lines)
                if updated != text:
                    base["caption"] = updated
                    changed += 1
    return changed


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
