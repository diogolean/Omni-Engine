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

## Session scripts

These stay in the folder. Nothing in the factory imports them. They are candidates for a later review, not deletions.

| Script | What it does |
|---|---|
| `_xcorr_vo.py` | Cross-correlates each source voice clip against the rendered audio. |
| `assemble_hope_hold_review.py` | Assembles a review MP4 from a QA-hold package. Not postable. |
| `batch_fast_preview_remaining.py` | Fast-preview assemble of remaining locked packs. No still regen. |
| `build_five_script_pack.py` | Five gate-passed scripts and Flux prompts. No image or video. |
| `compose_regret_open_question.py` | One off-domain regret / open-question draft. |
| `draft_tool_scripts.py` | Text-only tool-script drafts. Does not touch the hope hold. |
| `finish_four_tighten.py` | Finishes two leftover tighten jobs. No images. |
| `hope9_vo_concat.py` | Nine-line hope voice concat. No video. |
| `lock_approved_nine.py` | Builds locked 9-line scripts from the approved captions. |
| `lock_assemble_three.py` | Reuses passing stills and patches failing visuals. |
| `lock_hope_closing.py` | Locks the hope 9-line rewrite for a closing-pass test. |
| `lock_offdomain_nine.py` | Locks off-domain 9-line drafts. |
| `make_close_pack_reuse.py` | Close-pack still reuse. |
| `make_close_pack_reuse2.py` | Second close-pack reuse pass. |
| `make_close_pack_reuse3.py` | Third close-pack reuse pass. |
| `make_comm_reuse.py` | Communication-pack still reuse. |
| `make_comm_v2_assemble.py` | Communication v2 assemble. |
| `make_comm_v2_reuse.py` | Communication v2 still reuse. |
| `make_loss_assemble.py` | Loss-pack assemble. |
| `make_regret_belonging_assemble.py` | Regret and belonging assemble. |
| `make_structure_v1_assemble.py` | Stamps signed-off structure v1 captions onto existing still envelopes. |
| `polish_fresh_healing.py` | Gate-clean healing draft, then a polish pass. |
| `polish_held_v1.py` | Polishes the gate-passing v1 held pack. |
| `recover_four_tighten.py` | Recovers judge and TTS failures on the 4-pack tighten. Imports `tighten_four_scripts.py`. |
| `refresh_draft_index.py` | Refreshes `script_drafts_20260825/index.json`. |
| `regen_hope_visual_hold.py` | Regenerates hope hold stills. Does not touch voice. |
| `rematch_hope9_duck.py` | Re-mixes existing hope voice concats. No new TTS. |
| `rerun_structure_pack.py` | Regenerates the five held structure-library scripts. |
| `resplit_validate.py` | Re-splits and validates a pack. |
| `restamp_pack_prompts.py` | Restamps Flux prompts. Spoken lines stay frozen. |
| `retry_wound_light.py` | One loosened-budget retry of wound_light. |
| `rewrite_close_pack_captions.py` | Rewrites close-pack captions. |
| `rewrite_drafts_gate.py` | Patches two drafts. No image generation. |
| `run_drift_batch.py` | Five off-domain pattern stress tests. |
| `sanitize_handsfree_seeds.py` | Cleans hands-free seeds. |
| `seed_structure_library_v2.py` | One-time seed of rhetorical patterns. |
| `stamp_close_pack_history.py` | Stamps close-pack history. |
| `stamp_comm_history.py` | Stamps communication-pack history. |
| `tighten_four_scripts.py` | Retrofit of the spoken-budget gate onto four locked scripts. |
| `tighten_locked_script.py` | Tightens the locked distance script in place. |
| `verify_comm_v2.py` | Checks the communication v2 lock. |
| `verify_riso_close_pack.py` | Technical check for the riso close pack. |
| `verify_vo_gaps.py` | Concatenates per-scene voice and runs silencedetect. |
| `voice_speed_listen.py` | Short speed samples for a listen-pick. No video. |
| `write_structure_test_scripts.py` | Rewrites captions for a structure-library review. |
