# `main.py` module map

`main.py` is the process entry. It sets UTF-8, repairs Drive-synced text, pre-parses `--page`, loads `.env`, then calls `cli()`. The steps that used to live in that file are in `engine/`. `from main import produce` and the other names scripts already import still resolve.

Import order is load-bearing. `ACTIVE_PAGE` is set before `engine.runtime` imports `config` and the media stack. Do not move those imports above the pre-parse.

## Where each step lives

| Area | Module | Calls into |
|---|---|---|
| Bootstrap | `engine/bootstrap.py` (`_preparse_active_page`) | env only, before `config` |
| Imports and page rebind | `engine/runtime.py` | `config.py`, `channel_loader.py`, media stack |
| Variant orchestration | `engine/orchestrator.py` (`produce`, `run_pipeline`, `_produce_variant_worker`) | page context, cost tracker |
| Script and voice | `engine/voice.py` | `agents/writer/caption_engine.py`, `agents/media/audio_engine.py` |
| Still planning | `engine/acts.py` | `agents/media/prompt_alignment.py`, `modules/reel_qa/beats.py` |
| Image jobs | `engine/stills.py` | image adapter, `modules/reel_visual_qa.py` |
| Compile | phase D inside `_produce_variant_worker` | `core/reel_sequence_engine.py`, `agents/media/video_engine.py` |
| QA seal | after compile | `modules/reel_qa/pipeline.py` |
| Reporting | `engine/reporting.py` | cost tracker |
| Publish | planner calls in the worker; YouTube block in `engine/cli.py` | `agents/posting/post_planner.py`, `omni/adapters/youtube.py` |
| Agentic stills | `_produce_agentic`, `_persist_agentic_postplanner` | `agents/orchestrator/` |
| CLI | `engine/cli.py` | the modules above |

`engine/__init__.py` imports nothing. Importing the package does not load the media stack.

Early post types leave `cli` before `produce`:

- `ECONOMIC_REEL_LOFI` → `core/economic_reel_lofi/pipeline.py` (seals the MP4 on the way out)
- `REFERENCE_BASED_REELS` → `core/reference_reel_engine.py` (seals, then writes Post Planner only on `pass`)
- `WAN_REEL` registers a runner but still renders inside the variant worker

## What stayed put

QA stays in `modules/reel_qa/`. Publishers stay in `agents/posting/` and `omni/adapters/`. `_produce_variant_worker` is still one function: the phases share one local scope, and splitting it further changes control flow.

These modules are still large and were not split in the same change. They are cohesive libraries of functions that call each other:

- `core/economic_reel_lofi/visual_identity.py`
- `core/economic_reel_lofi/pipeline.py`
- `core/animator/factory/one_shot_factory.py`
- `agents/writer/script_agent.py`
- `core/animator/vision/head_analyzer.py`
- `core/reel_sequence_engine.py`

A reel still traces: `cli` → `_produce_variant_worker` → `refine_reel_beats` → `generate_and_gate` → compile → `seal_compiled_reel` → `planner_row_allowed` / `row_publishable`.
