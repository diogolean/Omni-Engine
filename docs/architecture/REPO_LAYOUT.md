# Repository layout

The root is an index, not a scratch drawer. A new file belongs in one of the homes below. If it does not, it does not go at the root.

This rule is also in `AGENTS.md` and `.cursor/rules/project-structure.mdc`. `tests/test_repo_root_layout.py` checks the tracked root against the allowlist.

## Root

Tracked at the repository root:

| Kind | Names |
|---|---|
| Identity | `README.md`, `AGENTS.md`, `CONTRIBUTING.md` |
| Entry points | `main.py`, `config.py`, `channel_loader.py`, `layer_wiring.py`. `run_main_noupload.py` is an allowed entry point when it is added. Do not rename it. |
| Project config | `pyproject.toml`, `requirements.txt`, `requirements-ci.txt`, `requirements-docker.txt`, `.env.example`, `.pre-commit-config.yaml`, `.gitleaksignore` |
| Containers and CI | `.dockerignore`, `.github/` |
| Deploy | `deploy/` |
| Packages | `accounts/`, `agents/`, `animation/`, `channels_config/`, `config/`, `core/`, `distribution/`, `engine/`, `models_avatar/`, `modules/`, `portfolio_showcase/`, `production/`, `products/`, `quality/`, `shared/`, `utils/` |
| Support trees | `assets/`, `docs/`, `Fonts/`, `scripts/` (compat shims until the scheduler cycles), `tests/`, `tools/` |
| Shims until the scheduler cycles | `infra/` (`infra.runpod` and `infra.serverless` re-export `deploy.runpod`) |
| Agent rules | `.cursor/rules/` |

`deploy/` holds the engine Dockerfile (`deploy/docker/Dockerfile`), the supermemory image (`deploy/docker/supermemory/`), compose (`deploy/compose/docker-compose.yml`), the VM scripts (`deploy/vm/remote-deploy.sh`, `deploy/vm/backup_supermemory.sh`), and RunPod workflows (`deploy/runpod/workflows/`, plus `deploy/runpod/serverless/`). The engine image build context stays the repo root, so `.dockerignore` stays beside it. Compose `dockerfile` is `deploy/docker/Dockerfile` and the supermemory build context is `deploy/docker/supermemory`. There is no root `Dockerfile`, `docker-compose.yml`, or `docker/` directory, and the repo tracks no symlinks. GitHub Actions copies the compose file to `/opt/omni-engine/docker-compose.yml` and the backup script to `/opt/omni-engine/scripts/backup_supermemory.sh`. Volume and `env_file` relatives stay project-root paths (`./outputs`, `./credentials`, `./.env`, `./channels_config`, `./state/ancient_knowledge/store`). There is no `deploy/k8s/` tree yet. Until M14, `import infra.runpod` and `import infra.serverless` load `deploy.runpod`. `REMOTE_GPU_WORKFLOWS_DIR=infra/runpod/workflows` resolves to `deploy/runpod/workflows` in code.

`Fonts/` stays at the root because page configs address fonts as `Fonts/<family>/...` from the repo root. Critic profiles live only in `production/qa/judges/profiles/`. `visual_critic.py` loads `profiles/<name>.json` from that folder. `quality/VisualQA_Agent/` is a shim.

`config.py` and `channel_loader.py` stay beside `main.py` as facades. `config/` is a data directory (YAML only, not a Python package), and other packages have their own `config.py` (`production/formats/pins`, `production/formats/lofi`, `production/qa/judges/visual_critic`). `import config` is the process settings module. It loads `.env` with `override=True` (preserving a preset `ACTIVE_PAGE`) and re-exports `shared.env`, `shared.paths.resolve_page_layout`, `production.providers.settings`, and `production.cost.pricing`. `production/pipeline/runtime.py` imports it, and `from channel_loader import load_page_context`, only after `ACTIVE_PAGE` is set. `channel_loader.py` is a shim for `production.pipeline.channel_context`. `layer_wiring.py` is the composition root that loads a module for `shared.layer_bridge`. It is not a layer, and production and distribution do not import each other. `ENGINE_ROOT` is the repository root.

The three requirement files stay split. `requirements.txt` is the full local install (`mediapipe`, `pyautogui`). `requirements-docker.txt` is the arm64 image and drops those two (no aarch64 wheel, and no desktop on the VM). `requirements-ci.txt` is the pull-request subset so CI does not install the media stack. One combined file would either put desktop packages in the Oracle image or drop pins the VM needs.

Secrets live only in `.env` (gitignored, including `.env.bak*` and `.env.*.bak`) and in `credentials/` (gitignored). `.env.example` lists names and stays empty of secrets. The doc index is [../README.md](../README.md).

## Packages that another change may add

These already have a home. Do not invent a second one, and do not add empty placeholder trees for them.

| Tree | Home | Notes |
|---|---|---|
| Publishers | `distribution/publishers/` | API (`pinterest`, `youtube`, `pinterest_oauth`), browser (`facebook_scheduler`), and Post Planner export. Error screenshots go to `outputs/facebook_scheduler/screenshots/`, not into the package. Some old imports under `agents/posting/` are still shims. |
| B2, Drive, imgbb | `shared/storage/` | The only boto3 B2 client is `shared/storage/b2_client.py`. `agents/media/b2_client.py` is a shim. The direct-client guard allows that shim and the storage module, and still skips the rest of `agents/media/`. |
| Parametric animation | `animation/parametric/` | Product-agnostic engine. Public facade is `animation/api.py` (`render_dynamic_animation`). The renderer is `animation/parametric/renderer.py` and stays an ffmpeg pipe. `core/animator/` re-exports that engine, and re-exports the Aiwake plate, voices, puppet factories, and harness, until the scheduler cycles. Those Aiwake modules live in `products/aiwake/animator/`. Downloaded binaries stay in `core/animator/bin/` and are gitignored. |
| Product shows | `products/` | Aiwake is `products/aiwake/` (pipeline, personas, prompts, tools, tests, settings, and the puppet harness). `channels_config/aiwake/` keeps the store and re-exports the show until the scheduler cycles. Import-linter forbids `animation` → `products`. |
| Visual QA critic | `production/qa/judges/visual_critic/` | Code and the DNA JSON. Profiles are `production/qa/judges/profiles/`. `quality/VisualQA_Agent/` is a shim. |
| Cursor rules | `.cursor/rules/` | The whole directory is versioned, including `aiwake-copy.mdc` and `video-pipeline.mdc`. `.cursor/*` other than `rules/` is gitignored. |
| Channel store | `channels_config/<channel>/store/` | Runtime state stays gitignored and dockerignored, and only lives in the owner's Drive backup. `.dockerignore` excludes `channels_config/*/store/` and re-includes `channels_config/aiwake/store/portfolio/` so those tracked showcase snapshots stay in the image. `store/.gitkeep` is a git placeholder. The Oracle compose file mounts Ancient Knowledge's store over that path. Do not rename it. |
| Lofi store | `production/formats/lofi/store/` | Files already tracked in this directory stay in git. `aphorism_recent.json` stays gitignored, including the old `core/economic_reel_lofi/store/` path. |
| Committed artefact photos | `channels_config/<channel>/artefact_cache/<entity>/` | Photographs shipped with the repo. The runtime cache under `store/artefact_cache/` stays gitignored. |
| Channel operator env | `channels_config/<id>/.env.<id>` on the host, seen in the container as `channels_env/<id>/.env.<id>` | The compose file mounts `./channels_config` at `/opt/omni-engine/channels_env`. Do not change that mount. |
| Pinterest account map | `config/pinterest_accounts.yaml`, `config/pinterest_image_map.yaml` | Do not rename. Article cache for Ancient Knowledge is `outputs/ancient_knowledge/article_cache/` and is gitignored with the rest of `outputs/`. |
| Worker entry | `python -m distribution.workers --role engine` and `--role scheduler` | The implementation is `distribution/workers/runtime.py`. Compose, the `deploy/docker/Dockerfile` `CMD`, and the VM use that module. Flags stay `--role`, `--once`, `--dry-run`, `--account`, and `--pins`. Compose service names `engine` and `scheduler` (profile `scheduler`) and `supermemory` (profile `memory`) stay as they are. |
| Scheduling | `distribution/scheduling/` | Pinterest drip, scheduler, pin ledger, inventory, library source, and the Facebook queue. Pin copy, photos, articles, and image generation live in `production/formats/pins/`. |
| Account registry | `accounts/` | `brandings.py`, `tokens.py`, `pinterest.py`. Not `distribution/accounts/` and not a rename of `channels_config/`. |
| Pin production | `production/formats/pins/` | Copy, articles, photo-vs-graphic check, Flux stills, the card renderer, and the pin channel config module. `agents/posting/pinterest_engine/` keeps shims for those names. |
| Providers | `production/providers/` | `llm/` (cheap stack, router, Gemini helpers, `text_model`), `image/` (adapters, prompt compilers, free photos, remote GPU, Wan), `tts/`, `vision/`, `avatar/` (`models_avatar` is a shim). The default text model is the cheap stack. Claude is opt-in. |
| Cost | `production/cost/` | `spend_ledger` (owner of `REEL_REEL_BUDGET_USD`), `cost_tracker`, `google_guardrail`, `gemini_usage`, `run_ledger`. The old `core` and `utils` paths are shims. |
| Media | `production/media/` | Brand compose, still and reel ffmpeg render, scene pacing, audio chunking. |
| Reel QA | `production/qa/` | Reel gate (`modules/reel_qa` is a shim) and `judges/` (visual eval, writer gate, visual critic). |
| Writers | `production/writers/` | Caption and script writers, plus `rag/`. `persona.py` is the one persona loader: it reads `channels_config/<channel>/master_dna.json` and does not store that channel's prompt sentences. `persona_dna.py` binds `ACTIVE_PAGE` at import. `agents/writer` and `agents/rag` are shims. |
| Pipeline | `production/pipeline/` | Stages are `production/pipeline/stages/`. The agentic package is `production/pipeline/agentic/`. `engine/` and `agents/orchestrator/` and `channel_loader.py` are shims. `main.py` stays the CLI. |
| Library | `production/library/` | `content_library`, `durable_library`, `content_source`, `asset_library`. The old `agents.media` and `modules.asset_library` paths are shims. |
| Formats | `production/formats/` | `pins/`, `economic_reel/` (artefact, grounding, hook, shot planner), `lofi/`, `wealth/` (including `fingerprint_engine`), `reference/`, `quotes/`, `mei/`. Old packages are shims. |

Operator scripts live in `tools/`. `tools/backup_supermemory.py` is the Supermemory upload. It is not scheduled. No cron on the VM calls it. Until scheduling is decided, run `/opt/omni-engine/scripts/backup_supermemory.sh` by hand. Deploy copies that file from `deploy/vm/backup_supermemory.sh` and does not run it. One-offs are `tools/oneoff/` (checks under `tools/oneoff/checks/`). Lofi experiments are `tools/lofi/`. `scripts/` is a shim tree until the scheduler cycles, including `scripts/backup_supermemory.py`. The VM shell runs `python tools/backup_supermemory.py`. `scripts/build_post_planner.py` and `scripts/sync_youtube_live_metadata.py` already call `products/aiwake/tools/`. `portfolio_showcase/` stays at the repository root.

`python tools/channel_cli.py --channel <id>` is the operator CLI. A channel folder's `cli.py` owns that channel's commands. Folders without one use the Pinterest CLI (`tools/pinterest_cli.py`). The old `pinterest_main.py`, `esp_main.py`, and `wealth_main.py` entry points were removed after two clean scheduler cycles.

`shared/` is the home for env getters, paths, logging, ffmpeg, image MIME, network retry, the durable channel store, the content contract, and the memory client (ADR 0008 M2 and M3). `utils/pipeline_paths.py` is `shared.paths`. `utils/process_tools.py` is `shared.ffmpeg`. `modules/reel_artefact/net_retry.py` is `shared.retry`. `modules/durable_store.py` is `shared.store`. `modules/distribution_contract.py` is `shared.contracts`. `modules/memory/` is `shared.memory`. New code imports `shared`. The old modules stay as shims so existing imports and monkeypatches keep one object.

`ContentItem` v1 (`shared.contracts`, `schema_version` 1) is the library row. Production writes it. Distribution reads it from `shared.contracts` and changes only `posting_status` through `update_publish_status`. The canonical file stays `channels_config/<channel>/store/content_library.json` (mirrored by `shared.store`). `channels_config/` keeps its name for per-channel content config. The account registry is a future top-level `accounts/` package, not `distribution/accounts/` and not a rename of `channels_config/`. M3 does not add that package. `python tools/migrate_content_items.py` upgrades an existing library in place and leaves `<file>.bak` beside it.

`outputs/` is runtime state: renders, logs, review dumps, the B2 usage ledger, and publisher libraries. It is gitignored. Write scratch with `temp_path` and `review_path` from `shared.paths` (`utils.pipeline_paths` is the same module).

## What does not belong in git

- `.env`, `.env.bak*`, `.env.*.bak`, other env backups, credential JSON, API keys, page tokens
- `credentials/`, `.claude/`
- `outputs/`, `logs/`, `*.mp4`, `*.mp3`, `*.wav`, and other render media
- `*.npz` and `*.psd` (render caches and design binaries). Runtime assets are the PNG/TTF the code opens.
- Error screenshots (`click_failed_*.png`, `*_error_*.png`)
- `*_recovered*.txt` and `__mine_*` transcript miners
- Channel store runtime state under `channels_config/<channel>/store/` (inventories, histories, content libraries, planners, caches, backups, `scene_library/`, `artefact_cache/`, `reel_checkpoints/`). It stays gitignored and dockerignored, and only lives in the owner's Drive backup. The tracked exception is `channels_config/aiwake/store/portfolio/`.

## Tests

- Factory tests: `tests/`
- Channel tests: `channels_config/<channel>/tests/`
- Aiwake tests: `products/aiwake/tests/`. The channel slot `channels_config/aiwake/` keeps `store/` (tracked `store/portfolio/`, gitignored runtime state) and does not keep a second copy of the tests.
- A channel keeps its own `page_config.py`, prompts, and tests beside each other. The engine reads behaviour flags from that file (`shared/channel_flags.py`) and does not branch on the channel name. One-channel format modules stay on the allowlist in `tests/test_channel_flags.py` (`production/formats/mei/`, `production/writers/mei_narrative.py`, `production/providers/image/visual_compiler.py`). Aiwake prompts live in `products/aiwake/prompts/`.
- Pytest collects `test_*.py` from those trees. Probe scripts live in `tools/lofi/` and `tools/oneoff/`, not in `tests/`. `scripts/` keeps a shim for each of those files until the scheduler cycles. `portfolio_showcase/` stays at the root: it is the recruiter showcase (data, assets, and the Next.js blueprint), not an operator CLI, so it does not move under `tools/portfolio/` and it is not retired.
- Test helpers imported only by tests (`caption_fixtures.py`, `cv_ground_truth.py`) stay next to the tests that use them.

## Pipeline code extracted from `main.py`

`main.py` is the CLI process: UTF-8 setup, Drive-text repair, `--page` pre-parse, `.env`, then a call to `cli()`. The steps live in `production/pipeline/`. `engine/` is a shim onto that package.

| Module | Responsibility |
|---|---|
| `production/pipeline/bootstrap.py` | UTF-8, source repair, page pre-parse |
| `production/pipeline/geo_anchors.py` | `_GEO_ANCHORS`. Import the table from this module. |
| `production/pipeline/runtime.py` | Imports that must run after `ACTIVE_PAGE` is set, then the page rebind |
| `production/pipeline/acts.py` | Post-type predicates, act descriptors, topic entities |
| `production/pipeline/voice.py` | Script trim, TTS stitch, voice track |
| `production/pipeline/stills.py` | Still cache, parallel act jobs |
| `production/pipeline/reporting.py` | Cost and run summaries |
| `production/pipeline/orchestrator.py` | Re-exports `produce`, the variant worker, agentic stills, and `--test-images` |
| `production/pipeline/stages/` | Stage modules behind that re-export. `variant.py` sequences them. See [main-module-map.md](main-module-map.md) |
| `production/pipeline/cli.py` | `cli()`. Flags are in `cli_parser.py`. The run sequence is `cli_run.py` |
| `production/pipeline/log.py` | Logger proxy bound to `main`'s logger |

`from main import produce` and the other names scripts already import keep working. `python main.py` is unchanged. `import engine.cli` still loads the same module object as `production.pipeline.cli`.

The production engine reads behaviour flags and does not branch on a folder id. What still names a channel is recorded in [0008 — Known debt](decisions/0008-target-architecture.md#known-debt-hard-coded-channels). A new channel is still a folder.

Reel QA lives in `production/qa/` (`modules/reel_qa/` is a shim). Publishers live in `distribution/publishers/`. Scheduling lives in `distribution/scheduling/`. The account registry lives in `accounts/`. Formats, the pipeline, the content library, providers, cost, media, and writers live under `production/`. The parametric animation engine lives in `animation/parametric/` (`animation/api.py` is the facade). B2, Drive, and imgbb live in `shared/storage/`. `omni/` is gone. Old import paths stay as shims until the scheduler cycles. Do not add a second copy under `engine/`, `core/`, or `modules/`.
