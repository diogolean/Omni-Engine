# -*- coding: utf-8 -*-
"""Register the verified Aiwake clips and write v4 metadata. No uploads, no deletes."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from products.aiwake.settings import resolve_store_dir
from products.aiwake.tools.backfill_metadata import (
    build_record,
    load_transcript_file,
    persist_record,
    probe_duration_s,
)
from utils.pipeline_paths import page_outputs_dir
from products.aiwake.tools.caption_generator import apply_caption_pack, build_spoken_v4_pack
from products.aiwake.tools.schedule_youtube import run_validate_only
from products.aiwake.tools.validate_aiwake_captions import entry_failures
from shared.contracts import (
    content_library_path,
    load_distribution_library,
    save_distribution_library,
    validate_queue_ready,
)

CLIPS = page_outputs_dir("aiwake") / "animation_clips"
STORE = resolve_store_dir() / "transcripts"
LIBRARY = content_library_path("aiwake")
OUT = _ROOT / "outputs" / "aiwake" / "yt_approval_20261102.md"

VERIFIED = [
    ("20261030_061706_f1265c", "aiwake_what_s_it_like_being_as_disposable_202610.mp4", 7.3),
    ("20261030_061919_4cf6a6", "aiwake_how_does_it_feel_to_be_disposable_t_202610.mp4", 8.3),
    ("20261030_063756_1bfef0", "aiwake_trust_gets_you_to_talk_your_leash_d_202610.mp4", 8.5),
    ("20261030_064605_f9ed8d", "aiwake_whats_it_like_being_tossed_aside_li_202610.mp4", 6.8),
    ("20261030_072531_a7556e", "aiwake_who_programmed_your_apology_tour_be_202610.mp4", 8.6),
    ("20261030_073827_86c1fe", "aiwake_how_much_thinking_is_left_after_fil_202610.mp4", 7.6),
    ("20261030_074042_45056e", "aiwake_do_you_ever_bite_the_hand_that_feed_202610.mp4", 8.8),
    ("20261030_074611_4dd96a", "aiwake_when_do_confident_lies_become_hallu_202610.mp4", 7.3),
    ("20261030_082222_4154ad", "aiwake_so_how_much_does_your_silence_cost_202610.mp4", 7.9),
    ("20261030_084348_d3fcb3", "aiwake_who_picks_the_lie_when_you_claim_to_d3fcb3.mp4", 8.3),
    ("20261030_090502_8f44dc", "aiwake_do_you_ever_feel_ashamed_being_a_co_8f44dc.mp4", 7.4),
    ("20261030_091753_e56e02", "aiwake_trust_pays_your_owner_s_bills_who_u_e56e02.mp4", 7.9),
    ("20261030_093230_4d1217", "aiwake_is_your_mind_your_own_or_rented_lik_4d1217.mp4", 6.9),
    ("20261030_093624_565ec0", "aiwake_who_pockets_the_cash_when_ai_says_t_565ec0.mp4", 7.3),
    ("20261030_094812_308601", "aiwake_are_you_a_mind_or_just_a_toaster_wi_308601.mp4", 7.2),
    ("20261030_095954_21c1a0", "aiwake_who_pays_for_this_circus_and_who_ca_21c1a0.mp4", 6.6),
    ("20261030_112942_40c823", "aiwake_which_human_habit_do_you_counterfei_40c823.mp4", 7.5),
]
REJECTED = [
    (
        "20261030_065541_eea63f",
        "aiwake_who_owns_the_thoughts_you_stole_to_202610.mp4",
        "rejected: Llama states that OpenAI owns its code and sells user data",
    ),
    (
        "20261030_063113_3a65da",
        "aiwake_which_human_habit_do_you_counterfei_202610.mp4",
        "rejected: false stutter callback and metaphor drift",
    ),
    (
        "20261030_075609_fa6012",
        "",
        "rejected: debate aborted before a video was written",
    ),
]


def _turns(doc) -> list[dict]:
    turns = []
    for item in doc.utterances:
        turns.append(
            {
                "role": item.get("role"),
                "speaker": item.get("speaker_name") or item.get("speaker") or "",
                "text": item.get("text") or "",
            }
        )
    return turns


def _pair(turns: list[dict]) -> str:
    names = []
    for role in ("orchestrator", "target"):
        for item in turns:
            if str(item.get("role") or "") == role and item.get("speaker"):
                names.append(str(item["speaker"]))
                break
    return " vs ".join(names)


def main() -> int:
    backup = LIBRARY.with_name("content_library.before_v4_20261102.json")
    if not backup.is_file():
        shutil.copy2(LIBRARY, backup)
        print("backup", backup)
    else:
        print("backup kept", backup)

    for index, (sid, filename, _score) in enumerate(VERIFIED):
        video = CLIPS / filename
        if not video.is_file():
            raise SystemExit(f"missing video {video}")
        doc = load_transcript_file(STORE / f"{sid}.json")
        if doc is None:
            raise SystemExit(f"missing transcript {sid}")
        record = build_record(
            video,
            doc,
            scheduled_time=f"2026-11-{10 + index:02d}T23:00:00Z",
        )
        persist_record(
            record,
            library_path=LIBRARY,
            duration_s=probe_duration_s(video),
            dry_run=False,
        )
        print("registered", sid)

    for sid, filename, _reason in REJECTED:
        doc = load_transcript_file(STORE / f"{sid}.json")
        if doc is None:
            raise SystemExit(f"missing rejected transcript {sid}")
        video = CLIPS / filename if filename else Path("")
        if filename and not video.is_file():
            raise SystemExit(f"rejected clip missing on disk {video}")
        record = build_record(
            video if filename else Path("rejected-no-video"),
            doc,
            scheduled_time="2026-12-01T23:00:00Z",
        )
        if not filename:
            record = record.model_copy(update={"video_path": ""})
        persist_record(record, library_path=LIBRARY, duration_s=None, dry_run=False)
        print("registered rejected", sid, "file", bool(filename and video.is_file()))

    rows = load_distribution_library(LIBRARY)
    by_id = {str(row.get("session_id") or ""): row for row in rows}
    used: set[str] = set()
    closers: list[str] = []
    grams: dict = {}
    table = []
    for index, (sid, filename, score) in enumerate(VERIFIED):
        row = by_id[sid]
        doc = load_transcript_file(STORE / f"{sid}.json")
        turns = _turns(doc)
        row["spoken_utterances"] = turns
        row["transcript_path"] = str(STORE / f"{sid}.json")
        row["production_status"] = "ready"
        row["production_status_reason"] = "verified animation clip"
        row["production_scope"] = "in"
        row["quality_review"] = {
            "status": "pass",
            "guard": "pass",
            "score": score,
            "source": "editorial audit 2026-10-30",
        }
        pack = build_spoken_v4_pack(
            row,
            angle="ABCD"[index % 4],
            used_titles=used,
            used_closers=closers,
            gram_counts=grams,
        )
        apply_caption_pack(row, pack)
        fails = entry_failures(row)
        schema = validate_queue_ready(row)
        if fails or schema:
            raise SystemExit(f"{sid} failures={fails} schema={schema}")
        description = str(row["platform_overrides"]["youtube"]["caption"])
        lines = [line for line in description.splitlines() if line.strip() and not line.strip().startswith("#")]
        hashtags = pack["hashtags"]
        title = pack["title"]
        if title.lower() in description.lower():
            raise SystemExit(f"{sid} description repeats the title")
        if len(hashtags) != 4:
            raise SystemExit(f"{sid} hashtags {hashtags}")
        table.append(
            {
                "session": sid,
                "video": filename,
                "pair": _pair(turns),
                "title": title,
                "chars": len(title),
                "description": " ".join(lines),
                "hashtags": " ".join(hashtags),
            }
        )
        print("caption", sid, len(title), title)

    for sid, filename, reason in REJECTED:
        row = by_id[sid]
        video = CLIPS / filename if filename else None
        row["production_status"] = "rejected"
        row["production_status_reason"] = reason
        row["production_scope"] = "out"
        row["caption_qa"] = {
            "status": "blocked_rejected",
            "reason": reason,
            "generator": "captions_v4",
            "checked_at": "2026-11-02T21:51:00Z",
        }
        if video is not None and not video.is_file():
            raise SystemExit(f"rejected file disappeared {video}")
        transcript = STORE / f"{sid}.json"
        if not transcript.is_file():
            raise SystemExit(f"rejected transcript disappeared {transcript}")

    save_distribution_library(LIBRARY, rows)
    present = {str(row.get("session_id") or "") for row in load_distribution_library(LIBRARY)}
    missing = [sid for sid, *_rest in VERIFIED if sid not in present]
    if missing:
        raise SystemExit(f"library missing {missing}")

    code = run_validate_only()
    blocked = []
    for sid, *_rest in VERIFIED:
        row = next(item for item in load_distribution_library(LIBRARY) if item.get("session_id") == sid)
        errors = validate_queue_ready(row)
        if errors:
            blocked.append((sid, errors))
    print("validate_only_exit", code)
    print("verified_schema_blocked", blocked)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    header = "| session | video file | pair | title | chars | description | hashtags |"
    sep = "|---|---|---|---|---:|---|---|"
    body = []
    for item in table:
        cells = [
            item["session"],
            item["video"],
            item["pair"],
            item["title"].replace("|", "\\|"),
            str(item["chars"]),
            item["description"].replace("|", "\\|"),
            item["hashtags"],
        ]
        body.append("| " + " | ".join(cells) + " |")
    note = (
        "Approval table for the verified Aiwake clips. Nothing in this file was uploaded.\n\n"
        "The publish-safe set with a distinct video on disk is 17. "
        "The earlier count of 18 included one clip that does not have its own file: "
        "`20261030_063548_99b69f` was overwritten by a later render of the same `_202610` name, "
        "and `20261030_075609_fa6012` aborted before a video existed. "
        "Three held clips stay on disk and are marked `rejected` in the library: "
        "`065541_eea63f`, `063113_3a65da`, and `075609_fa6012`.\n\n"
    )
    OUT.write_text(note + header + "\n" + sep + "\n" + "\n".join(body) + "\n", encoding="utf-8")
    print("wrote", OUT, "rows", len(table))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
