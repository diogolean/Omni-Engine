# -*- coding: utf-8 -*-
"""Text-only harness for the freeform writer + five-criterion judge.

No images, no atmosphere, no assemble — this writes and judges scripts so the
text layer can be reviewed on its own.

    python scripts/lofi/run_freeform_drafts.py
    python scripts/lofi/run_freeform_drafts.py --theme distance --theme regret
    python scripts/lofi/run_freeform_drafts.py --quote "We suffer more in imagination than in reality."
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.pipeline_paths import page_outputs_dir

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True, encoding="utf-8-sig")

from agents.writer.script_brain import BrainResult, compose  # noqa: E402
from agents.writer.writer_brief import WriterBrief  # noqa: E402

OUT_DIR = page_outputs_dir("wonder_feed") / "clips" / "freeform_drafts"

# Default review set: three theme briefs and two quote-seed briefs, so both
# entry points into the writer are exercised in the same run.
DEFAULT_THEMES: tuple[tuple[str, str], ...] = (
    ("distance", "silence_that_speaks"),
    ("forgiveness", "the_apology_that_never_came"),
    ("starting_over", "after_it_ended"),
)
DEFAULT_QUOTES: tuple[tuple[str, str], ...] = (
    ("We suffer more often in imagination than in reality.", "Seneca"),
    ("The wound is the place where the light enters you.", "Rumi"),
)


def _md_for(result: BrainResult) -> str:
    brief = result.brief
    head = (
        f"### {brief.label}  ({'APPROVED' if result.ok else 'NOT APPROVED'})\n"
        f"mode: `{brief.mode}`"
    )
    if brief.mode == "quote":
        head += f"  ·  seed: “{brief.seed_quote}” — {brief.seed_attribution}"
    out = [head, ""]

    draft = result.draft or next((a.draft for a in reversed(result.attempts) if a.draft), None)
    verdict = result.verdict or next(
        (a.verdict for a in reversed(result.attempts) if a.verdict), None
    )
    if draft is not None:
        out += [
            f"*situation:* {draft.human_situation}",
            f"*form the writer chose:* {draft.structure}",
            f"*lines:* {len(draft.lines)}  ·  *words:* {draft.word_count}  ·  "
            f"*stills:* {len(draft.lines)}  ·  *caption beats:* {len(draft.beats())}  ·  "
            f"*est. runtime:* {draft.estimated_seconds}s  ·  "
            f"*attempts:* {len(result.attempts)}",
            "",
            "```",
            draft.numbered(),
            "```",
            "",
            f"*closing tool:* {draft.closing_tool}",
            "",
        ]
        budget = (result.diagnostics or {}).get("spoken_budget") or {}
        beats = budget.get("beats") or []
        if beats:
            out.append(
                f"*spoken budget:* {budget.get('ceiling')}w / "
                f"{budget.get('beat_s')}s  ·  requested {budget.get('duration_s')}s  ·  "
                f"{'PASS' if budget.get('ok') else 'FAIL'}"
            )
            out.append("| beat | words | budget | ceiling | |")
            out.append("|---|---|---|---|---|")
            for b in beats:
                mark = "ok" if b.get("ok") else "**OVER**"
                out.append(
                    f"| {b.get('scene')} | {b.get('words')} | {b.get('budget')} | "
                    f"{b.get('ceiling')} | {mark} |"
                )
            out.append("")
    if verdict is not None:
        out.append("| criterion | score | bar | | justification |")
        out.append("|---|---|---|---|---|")
        for s in verdict.scores:
            out.append(
                f"| {s.name} | {s.score} | {s.bar} | {'pass' if s.passed else '**FAIL**'} "
                f"| {s.justification} |"
            )
        out.append("")
        if verdict.revision_note:
            out.append(f"*judge revision note:* {verdict.revision_note}")
        if verdict.cliche_hits:
            out.append(f"*cliche scan (non-blocking):* {', '.join(verdict.cliche_hits)}")
        out.append("")
    if not result.ok:
        out += [f"*blocked because:*\n\n```\n{result.reason()}\n```", ""]
    diag = (result.diagnostics or {}).get("report") or {}
    if diag:
        out.append(
            f"*legacy regex diagnostic (non-blocking):* fails={diag.get('fails') or 'none'}"
        )
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--theme", action="append", default=[], help="theme (repeatable)")
    ap.add_argument("--subtheme", action="append", default=[], help="subtheme, paired by order")
    ap.add_argument("--quote", action="append", default=[], help="seed quote (repeatable)")
    ap.add_argument("--module", default="relationship")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--duration", type=float, default=None, help="requested total seconds (default 27)")
    ap.add_argument("--writer", default=None, help="claude | gemini | deepseek")
    ap.add_argument("--judge", default=None, help="claude | gemini | deepseek")
    args = ap.parse_args()

    from core.economic_reel_lofi import config as lofi_cfg

    duration_s = float(args.duration if args.duration is not None else lofi_cfg.DEFAULT_DURATION_S)
    beat_s = float(lofi_cfg.beat_duration_s())
    brief_meta = {"duration_s": duration_s, "beat_duration_s": beat_s}

    briefs: list[WriterBrief] = []
    if args.theme or args.quote:
        for i, theme in enumerate(args.theme):
            sub = args.subtheme[i] if i < len(args.subtheme) else ""
            briefs.append(
                WriterBrief.from_theme(
                    theme=theme, subtheme=sub, module=args.module, meta=brief_meta
                )
            )
        for quote in args.quote:
            briefs.append(
                WriterBrief.from_quote(quote=quote, module=args.module, meta=brief_meta)
            )
    else:
        briefs = [
            WriterBrief.from_theme(theme=t, subtheme=s, module=args.module, meta=brief_meta)
            for t, s in DEFAULT_THEMES
        ] + [
            WriterBrief.from_quote(quote=q, attribution=a, module=args.module, meta=brief_meta)
            for q, a in DEFAULT_QUOTES
        ]

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[BrainResult] = []

    for i, brief in enumerate(briefs, start=1):
        print(f"\n===== [{i}/{len(briefs)}] {brief.label} =====")
        try:
            results.append(
                compose(
                    brief,
                    max_attempts=args.attempts,
                    writer_provider=args.writer,
                    judge_provider=args.judge,
                )
            )
        except Exception as exc:  # noqa: BLE001 — one bad brief must not kill the batch
            print(f"[LOFI drafts] {brief.label} crashed: {exc}")
            results.append(BrainResult(brief=brief, ok=False))

    approved = sum(1 for r in results if r.ok)
    json_path = OUT_DIR / f"freeform_drafts_{stamp}.json"
    json_path.write_text(
        json.dumps(
            {
                "generated_at": stamp,
                "module": args.module,
                "approved": approved,
                "total": len(results),
                "results": [r.to_dict() for r in results],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    md_path = OUT_DIR / f"freeform_drafts_{stamp}.md"
    md_path.write_text(
        "\n".join(
            [
                f"# Freeform writer drafts — {stamp}",
                "",
                f"Text only. No images generated. {approved}/{len(results)} cleared all "
                "five criteria.",
                "",
                *[_md_for(r) for r in results],
            ]
        ),
        encoding="utf-8",
    )

    print(f"\n[LOFI drafts] {approved}/{len(results)} approved")
    print(f"[LOFI drafts] {json_path}")
    print(f"[LOFI drafts] {md_path}")

    from agents.writer.script_brain import draft_to_script

    for r in results:
        if not r.ok or r.draft is None:
            continue
        script = draft_to_script(r.draft)
        slug = re.sub(r"[^a-z0-9]+", "_", r.brief.label.lower()).strip("_")
        locked = OUT_DIR / f"budgeted_{slug}_{stamp}.json"
        locked.write_text(
            json.dumps({"script": script, "judge": r.verdict.to_dict() if r.verdict else None}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"[LOFI drafts] budgeted script {locked}")
        budget = (r.diagnostics or {}).get("spoken_budget") or {}
        print(
            f"[LOFI drafts] spoken-budget ok={budget.get('ok')} "
            f"lines={budget.get('n_lines')} ceiling={budget.get('ceiling')} "
            f"needed_s={budget.get('needed_s')} requested_s={budget.get('duration_s')}"
        )
        for b in budget.get("beats") or []:
            print(
                f"  beat {b.get('scene')}: {b.get('words')}w / {b.get('ceiling')}w "
                f"{'ok' if b.get('ok') else 'OVER'}  {b.get('text')!r}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
