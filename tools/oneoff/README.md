# One-off scripts

Operator entry points under `tools/oneoff/`. They are not imported by the factory. `scripts/` keeps a shim for each file until M14. The Supermemory backup is `tools/backup_supermemory.py`, not a script in this folder. It is not scheduled. Until scheduling is decided, run `/opt/omni-engine/scripts/backup_supermemory.sh` by hand on the VM.

| Path | What it does |
|---|---|
| [../lofi/README.md](../lofi/README.md) | Lofi dry-run, assemble, checks, audits, and probes |
| `inspect_chroma_db.py` | Dumps the VisualQA Chroma store, or the JSON fallback, for Ancient Knowledge |
| `_repair_postplanner_b2.py` | Repairs the 2026-06-08 Post Planner workbook's B2 media column |
| `reassemble_lofi_trust_reel.py` | Reassembles existing trust stills with grading off, TTS, and the music bed |
| `wan_20step_motion_test.py` | Wan 20-step motion test with the 4-step LoRA disabled |
| `wan_hardcode_4step_test.py` | Checks whether the Comfy switch drops the LightX2V 4-step path |

`agents/posting/scheduler_engine.py` and `agents/posting/facebook_publisher.py` were a sheet-queue Facebook Graph publisher. Nothing imported them except each other, and no deploy entry called them. They were deleted in the M4 distribution move. The live Facebook path is `distribution/publishers/browser/facebook_scheduler/` (`agents/posting/facebook_scheduler/` still imports, as a shim).

Other scripts in this folder (`checks/`, `burn_subs_onto_reel.py`, `upload_library_to_b2.py`, and the rest) are the same kind of tool. Run one by its path from the repo root.
