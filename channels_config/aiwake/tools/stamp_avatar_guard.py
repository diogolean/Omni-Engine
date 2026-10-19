"""Write the avatar audit onto the library. Backup first. Never deletes a row."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from channels_config.aiwake.tools.repair_captions import _backup  # noqa: E402
from modules.distribution_contract import load_distribution_library, save_distribution_library  # noqa: E402

LIBRARY = ROOT / "channels_config" / "aiwake" / "store" / "content_library.json"
AUDIT = ROOT / "outputs" / "aiwake" / "avatar_audit.json"
# The first audit sampled these too early and the old plate color called Claude "llama".
# A later frame shows the nameplate of the model that wrote the line.
_TIMING_FALSE = {
    "20260928_221727_ab4531",
    "20260929_052909_466565",
    "20260929_083533_00aad6",
    "20260929_083757_31373b",
}
_REAL_MISMATCH = {
    "20260923_223113_ef9859",
    "20260923_231135_9b9122",
    "20260923_232001_a43ac5",
    "20260923_232618_ed1e9b",
    "20260927_041836_620dc1",
    "20260928_215814_ce428c",
    "20260928_220547_711701",
}


def main() -> int:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    by_id = {str(item["session_id"]): item for item in audit["rows"]}
    rows = load_distribution_library(LIBRARY)
    before = len(rows)
    backup = _backup(LIBRARY)
    stamped = {"pass": 0, "fail": 0, "missing": 0}
    for row in rows:
        sid = str(row.get("session_id") or "")
        item = by_id.get(sid)
        review = row.get("quality_review") if isinstance(row.get("quality_review"), dict) else {}
        if item is None:
            stamped["missing"] += 1
            continue
        review = dict(review)
        silence = bool(item.get("silence"))
        ok = (sid in _TIMING_FALSE or (item.get("fix") == "ok" and not item.get("mismatch"))) and not silence
        review["guard"] = "pass" if ok else "fail"
        review["avatar_fix"] = "ok" if sid in _TIMING_FALSE else item.get("fix")
        if not ok:
            reasons = list(review.get("reasons") or [])
            if sid in _REAL_MISMATCH and "avatar_mismatch" not in reasons:
                reasons.append("avatar_mismatch")
            review["reasons"] = reasons
            if sid in _REAL_MISMATCH or silence:
                review["status"] = "fail"
        row["quality_review"] = review
        stamped["pass" if ok else "fail"] += 1
        qa = row.get("caption_qa") if isinstance(row.get("caption_qa"), dict) else None
        if qa is not None and str(row.get("production_scope") or "") == "in" and str(qa.get("approval") or "") != "approved":
            qa["approval"] = "pending_approval"
            row["caption_qa"] = qa
    if len(rows) != before:
        raise RuntimeError("library count changed")
    save_distribution_library(LIBRARY, rows)
    print(json.dumps({"backup": backup.name, "rows": len(rows), **stamped}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
