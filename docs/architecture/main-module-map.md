# `main.py` module map

`main.py` is the CLI and the variant worker. It still owns topic setup, image generation, reel compile, library writes, and the YouTube hand-off. The list below is the current shape. The split that follows is a proposal. It is not implemented.

## What the file does today

| Area | Functions | Calls into |
|---|---|---|
| Bootstrap | `_preparse_active_page`, `cli` | `config.py`, `channel_loader.py` |
| Variant orchestration | `produce`, `run_pipeline`, `_produce_variant_worker` | page context, cost tracker |
| Script and voice | `_trim_script_to_word_limit`, `_synthesize_sequence_voice_track`, `_filter_audio_tag_timings` | `agents/writer/caption_engine.py`, `agents/media/audio_engine.py` |
| Still planning | `_segment` via `_split_script_into_act_chunks`, `_build_reel_act_descriptors`, `_extract_topic_visual_entities` | `agents/media/prompt_alignment.py`, `modules/reel_qa/beats.py` |
| Image jobs | `_run_acts_parallel`, `_apply_still_cache_to_jobs`, `_ensure_sequence_image` | image adapter, `modules/reel_visual_qa.py` |
| Compile | phase D inside `_produce_variant_worker` | `core/reel_sequence_engine.py`, `agents/media/video_engine.py` |
| QA seal | after compile | `modules/reel_qa/pipeline.py` |
| Publish | `append_planner_row` call sites, YouTube block in `cli` | `agents/posting/post_planner.py`, `omni/adapters/youtube.py` |
| Agentic stills | `_produce_agentic`, `_persist_agentic_postplanner` | `agents/orchestrator/` |

Early post types leave `cli` before `produce`:

- `ECONOMIC_REEL_LOFI` → `core/economic_reel_lofi/pipeline.py` (seals the MP4 on the way out)
- `REFERENCE_BASED_REELS` → `core/reference_reel_engine.py` (seals, then writes Post Planner only on `pass`)
- `WAN_REEL` registers a runner but still renders inside the variant worker

## Proposal: layered modules

Keep `main.py` as a thin CLI that parses arguments and calls one orchestrator. Move the rest by responsibility. No behavior change is implied by the names.

| Layer | Would own | Pulled out of `main.py` |
|---|---|---|
| `orchestration` | `cli`, `produce`, variant workers, locks, run stamps | `produce`, `run_pipeline`, `_produce_variant_worker`, `cli` |
| `producers` | topic, script, captions, act descriptors | script trim, voiceover synthesis, hook text |
| `media` | image adapter jobs, TTS files, MP4 compile, B2 upload | parallel act jobs, scene export, compile dispatch |
| `qa` | already started | `modules/reel_qa/` stays the QA package; `main.py` only calls `seal_compiled_reel` |
| `publishers` | Post Planner, YouTube, library JSON | planner writes and the YouTube schedule loop |

Rules for the move, when someone does it:

- One package per layer, with the public function named after the step (`plan_beats`, `compile_reel`, `seal_compiled_reel`, `planner_row_allowed`).
- `main.py` does not grow new branches for a post type. A new reel registers a runner and calls the same QA seal.
- Publish code asks `modules.reel_qa.publish` and does not reimplement the verdict check.

Until that split, the trace for a reel is: `cli` → `_produce_variant_worker` → `refine_reel_beats` → `generate_and_gate` → compile → `seal_compiled_reel` → `planner_row_allowed` / `row_publishable`.
