# Scripts

Operator entry points. They are not imported by the factory.

| Path | What it does |
|---|---|
| [lofi/README.md](lofi/README.md) | Lofi dry-run, assemble, checks, audits, and probes |
| `inspect_chroma_db.py` | Dumps the VisualQA Chroma store, or the JSON fallback, for Ancient Knowledge |
| `_repair_postplanner_b2.py` | Repairs the 2026-06-08 Post Planner workbook's B2 media column |
| `reassemble_lofi_trust_reel.py` | Reassembles existing trust stills with grading off, TTS, and the music bed |
| `wan_20step_motion_test.py` | Wan 20-step motion test with the 4-step LoRA disabled |
| `wan_hardcode_4step_test.py` | Checks whether the Comfy switch drops the LightX2V 4-step path |

`agents/posting/scheduler_engine.py` is a sheet-queue scheduler kept on disk. The live Facebook path is `agents/posting/facebook_scheduler/`. The sheet-queue file is a candidate for a later review, not a deletion.

Other scripts in this folder (`checks/`, `burn_subs_onto_reel.py`, `upload_library_to_b2.py`, and the rest) are the same kind of tool. Run one by its path from the repo root.
