# `main.py` module map

`main.py` is the process entry. It sets UTF-8, repairs Drive-synced text, pre-parses `--page`, loads `.env`, then calls `cli()`. The steps that used to live in that file are in `engine/`. `from main import produce` and the other names scripts already import still resolve.

Import order is load-bearing. `ACTIVE_PAGE` is set before `engine.runtime` imports `config` and the media stack. Do not move those imports above the pre-parse.

## Where each step lives

| Area | Module | Calls into |
|---|---|---|
| Bootstrap | `engine/bootstrap.py` (`_preparse_active_page`) | env only, before `config` |
| Imports and page rebind | `engine/runtime.py` | `config.py`, `channel_loader.py`, media stack |
| Variant orchestration | `engine/orchestrator.py` re-exports `produce`, `run_pipeline`, `_produce_variant_worker` | `engine/pipeline/` |
| Script and voice | `engine/voice.py` | `agents/writer/caption_engine.py`, `agents/media/audio_engine.py` |
| Still planning | `engine/acts.py` | `agents/media/prompt_alignment.py`, `modules/reel_qa/beats.py` |
| Image jobs | `engine/stills.py` | image adapter, `modules/reel_visual_qa.py` |
| Compile | `engine/pipeline/stage_compile_reel.py` | `core/reel_sequence_engine.py`, `agents/media/video_engine.py` |
| QA seal | after compile, still inside the compile stage | `modules/reel_qa/pipeline.py` |
| Reporting | `engine/reporting.py` | cost tracker |
| Publish | `engine/pipeline/stage_publish.py`; YouTube block in `engine/cli_run.py` | `distribution/publishers/export/post_planner.py`, `distribution/publishers/api/youtube.py` (old paths are shims) |
| Agentic stills | `engine/pipeline/agentic.py` (`_produce_agentic`, `_persist_agentic_postplanner`) | `agents/orchestrator/` |
| CLI | `engine/cli.py` calls `engine/cli_parser.py` then `engine/cli_run.py` | the modules above |

`engine/__init__.py` imports nothing. Importing the package does not load the media stack. `engine/pipeline/__init__.py` is the same: import a stage module, or `engine.orchestrator`, to load the worker.

## Variant worker stages

`_produce_variant_worker` in `engine/pipeline/variant.py` binds the arguments on a `VariantState` and calls the stages in order. A stage reads and writes attributes on that object. The worker used to be one function; the statements are the same, only the scope is the state object.

| Stage | Module | What it does |
|---|---|---|
| Setup | `engine/pipeline/stage_setup.py` | Batch angle, topic, sketch lock, horror override, then the calls into render and sequence |
| Still render | `engine/pipeline/stage_render_still.py` | Text-quote and standard stills |
| Sequence plan | `engine/pipeline/stage_sequence_plan.py` | Narration, beats, asset pool |
| Sequence render | `engine/pipeline/stage_sequence_render.py` | Master Mei and sequence stills, including the QA gates |
| Caption and brand | `engine/pipeline/stage_caption_brand.py` | Caption, fonts, logo, overlay, carousel upload |
| Compile | `engine/pipeline/stage_compile_reel.py` | `unique_stills`, then scene export, compile, and the QA seal |
| Publish | `engine/pipeline/stage_publish.py` | Planner rows, library writes, cost telemetry |

`produce` and `run_pipeline` live in `engine/pipeline/batch.py`. `--test-images` lives in `engine/pipeline/debug_images.py`. `engine/orchestrator.py` only re-exports those names, so `from main import produce` is unchanged.

## CLI

`cli()` in `engine/cli.py` is the sequencer. `engine/cli_parser.py` builds the same flags and exposes `_together_cli_image_model`. `engine/cli_run.py` applies page locks, dispatches the early post types, calls `produce`, and runs the YouTube block. Destinations and environment variables are unchanged.

Early post types leave `cli` before `produce`:

- `ECONOMIC_REEL_LOFI` → `core/economic_reel_lofi/pipeline.py` (seals the MP4 on the way out)
- `REFERENCE_BASED_REELS` → `core/reference_reel_engine.py` (seals, then writes Post Planner only on `pass`)
- `WAN_REEL` registers a runner but still renders inside the variant worker

## What stayed put

QA stays in `modules/reel_qa/`. Publishers live in `distribution/publishers/`. The variant worker sequences the stage modules above; it does not grow a second copy of QA or publish.

These modules are still large and were not split in the same change. They are cohesive libraries of functions that call each other:

- `core/economic_reel_lofi/visual_identity.py`
- `core/economic_reel_lofi/pipeline.py`
- `core/animator/factory/one_shot_factory.py`
- `agents/writer/script_agent.py`
- `core/animator/vision/head_analyzer.py`
- `core/reel_sequence_engine.py`

A reel still traces: `cli` → `_produce_variant_worker` → `refine_reel_beats` → `generate_and_gate` → compile → `seal_compiled_reel` → `planner_row_allowed` / `row_publishable`.
