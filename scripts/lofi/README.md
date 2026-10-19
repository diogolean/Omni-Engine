# Lofi operator scripts

Run from the repo root. These are tools, not library modules. Filenames keep their leading underscores: no doc points at a renamed path.

## Run and assemble

| Script | What it does |
|---|---|
| `dry_run_reel.py` | Runs the writer and the spoken-budget gate for one reel. No images, no MP4. |
| `export_prod_scripts.py` | Writes approved tightened scripts as locked stills inputs, with hook stamps. |
| `run_freeform_drafts.py` | Writes and judges scripts only. `--theme` picks the theme. |
| `validate_prose_writer.py` | Runs a few fresh themes through the paragraph writer and prints the QA. |
| `assemble_render_effects.py` | Assembles one episode. Flags turn individual render effects off. |
| `_reassemble_fix.py` | Rebuilds an MP4 from an existing meta JSON. No new stills or voice. |
| `_reassemble_kb.py` | Same rebuild, with Ken Burns on. |
| `_extract_frame.py` | Saves one frame at a timestamp: `python scripts/lofi/_extract_frame.py <mp4> <seconds> <out>`. |

## Checks and audits

| Script | What it does |
|---|---|
| `check_idea_development.py` | Prints connective, recipe, and hook-answer gates for locked scripts. |
| `check_locked_pack.py` | Prints the narrative gates for the loss, loneliness, and forgiveness locks. |
| `check_locked_communication.py` | Prints gates for `locked_script_communication_v1.json`. |
| `check_locked_communication_v2.py` | Prints gates and composition for `locked_script_communication_v2.json`. |
| `check_structure_library_pack.py` | Prints rhetoric, composition, and holds for the structure-library locks. |
| `check_object_churn_fp.py` | Checks object-churn on polish drafts that used to false-positive. |
| `principle_audit_shipped.py` | Principle-voice audit of the shipped hope, distance, grief, and perseverance scripts. No rewrites. |
| `hope_review_audit.py` | Audits the hope reel that was under review. Does not assemble. |
| `_drift_audit.py` | Prints caption-clock drift for the forgiveness reel meta from 2026-08-27. |
| `_verify_alignment.py` | Compares the old concat clock with the scene-boundary clock on that same meta. |
| `_verify_audio_align.py` | Measures rendered audio against each scene boundary. |
| `_verify_kb_final.py` | Checks per-scene audio alignment and that the Ken Burns zoom is present. |
| `_verify_vo_sidecar.py` | Checks the voice-only sidecar against scene boundaries, without the music bed. |
| `_verify_whitespace.py` | Checks that caption render does not insert a stray space. |

## Probes

`probes/` renders or synthesizes a sample. It does not publish.

| Script | What it does |
|---|---|
| `probes/flat_style_probe.py` | Regenerates one or two settings with the flatter riso prompt. |
| `probes/fluxdev_probe.py` | Compares Schnell and FLUX.1-dev on the same beats, with the QA stack. |
| `probes/painterly_probe.py` | Isolated Schnell painterly stills. Does not write the live identity bank. |
| `probes/probe_scene.py` | Replays the per-scene QA retry on one locked-script beat. |
| `probes/probe_tts_speed_wire.py` | Compares multilingual v2 speed 1.0 and 0.8, and dumps the request. |
| `probes/speed_ratio_probe.py` | Compares duration at speed 1.0 and 0.70. No video. |
| `probes/vo_pace_probe.py` | One spoken line, to hear the pace. No video. |
| `probes/vo_pause_probe.py` | About ten seconds of breath commas plus the inter-line silence. No video. |

Sibling scripts at `scripts/`:

| Script | What it does |
|---|---|
| `scripts/inspect_chroma_db.py` | Dumps the VisualQA Chroma store, or the JSON fallback, for Ancient Knowledge. |
| `scripts/_repair_postplanner_b2.py` | Rewrites the 2026-06-08 Post Planner workbook so B2 URLs sit in the media column. |
