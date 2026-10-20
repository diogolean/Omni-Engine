# Repository layout

The root is an index, not a scratch drawer. A new file belongs in one of the homes below. If it does not, it does not go at the root.

This rule is also in `AGENTS.md` and `.cursor/rules/project-structure.mdc`. `tests/test_repo_root_layout.py` checks the tracked root against the allowlist.

## Root

Tracked at the repository root:

| Kind | Names |
|---|---|
| Identity | `README.md`, `AGENTS.md`, `CONTRIBUTING.md` |
| Entry points | `main.py`, `config.py`, `channel_loader.py`. `run_main_noupload.py` is an allowed entry point when it is added. Do not rename it. |
| Project config | `pyproject.toml`, `requirements.txt`, `requirements-ci.txt`, `requirements-docker.txt`, `.env.example`, `.pre-commit-config.yaml`, `.gitleaksignore` |
| Containers and CI | `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.github/` |
| Packages | `accounts/`, `agents/`, `channels_config/`, `config/`, `core/`, `distribution/`, `engine/`, `models_avatar/`, `modules/`, `portfolio_showcase/`, `quality/`, `shared/`, `utils/` |
| Support trees | `assets/`, `docker/`, `docs/`, `Fonts/`, `infra/`, `scripts/`, `tests/`, `tools/` |
| Agent rules | `.cursor/rules/` |

`Fonts/` stays at the root because page configs address fonts as `Fonts/<family>/...` from the repo root. Critic profiles live only in `quality/VisualQA_Agent/critic_profiles/`. `visual_critic.py` loads `critic_profiles/<name>.json` from that package.

`config.py` and `channel_loader.py` stay beside `main.py`. `config/` is a data directory (YAML only, not a Python package), and other packages have their own `config.py` (`pinterest_engine`, `economic_reel_lofi`, `VisualQA_Agent`). `import config` is the process settings module. `engine/runtime.py` imports it, and `from channel_loader import load_page_context`, only after `ACTIVE_PAGE` is set. `config.py` uses `Path(__file__).parent` as the repo root to find `.env`. Moving either file would rename that import across the factory and move the `.env` lookup.

The three requirement files stay split. `requirements.txt` is the full local install (`mediapipe`, `pyautogui`). `requirements-docker.txt` is the arm64 image and drops those two (no aarch64 wheel, and no desktop on the VM). `requirements-ci.txt` is the pull-request subset so CI does not install the media stack. One combined file would either put desktop packages in the Oracle image or drop pins the VM needs.

Secrets live only in `.env` (gitignored, including `.env.bak*` and `.env.*.bak`) and in `credentials/` (gitignored). `.env.example` lists names and stays empty of secrets. The doc index is [../README.md](../README.md).

## Packages that another change may add

These already have a home. Do not invent a second one, and do not add empty placeholder trees for them.

| Tree | Home | Notes |
|---|---|---|
| Publishers | `distribution/publishers/` | API (`pinterest`, `youtube`, `pinterest_oauth`), browser (`facebook_scheduler`), and Post Planner export. Error screenshots go to `outputs/facebook_scheduler/screenshots/`, not into the package. Some old imports under `agents/posting/` are still shims. |
| B2, Drive, content source | `agents/media/` | Do not move. The B2 client guard forbids direct B2 clients outside this package. |
| Puppet / Aiwake animator | `core/animator/` | Code. Downloaded binaries stay in `core/animator/bin/` and are gitignored. |
| Visual QA critic | `quality/VisualQA_Agent/` | Code and package-local critic JSON in `critic_profiles/`. |
| Cursor rules | `.cursor/rules/` | The whole directory is versioned, including `aiwake-copy.mdc` and `video-pipeline.mdc`. `.cursor/*` other than `rules/` is gitignored. |
| Channel store | `channels_config/<channel>/store/` | Runtime state stays gitignored and dockerignored, and only lives in the owner's Drive backup. `.dockerignore` excludes `channels_config/*/store/` and re-includes `channels_config/aiwake/store/portfolio/` so those tracked showcase snapshots stay in the image. `store/.gitkeep` is a git placeholder. The Oracle compose file mounts Ancient Knowledge's store over that path. Do not rename it. |
| Lofi store | `core/economic_reel_lofi/store/` | Files already tracked in this directory stay in git. `aphorism_recent.json` stays gitignored. |
| Committed artefact photos | `channels_config/<channel>/artefact_cache/<entity>/` | Photographs shipped with the repo. The runtime cache under `store/artefact_cache/` stays gitignored. |
| Channel operator env | `channels_config/<id>/.env.<id>` on the host, seen in the container as `channels_env/<id>/.env.<id>` | The compose file mounts `./channels_config` at `/opt/omni-engine/channels_env`. Do not change that mount. |
| Pinterest account map | `config/pinterest_accounts.yaml`, `config/pinterest_image_map.yaml` | Do not rename. Article cache for Ancient Knowledge is `outputs/ancient_knowledge/article_cache/` and is gitignored with the rest of `outputs/`. |
| Worker entry | `python -m distribution.workers --role engine` and `--role scheduler` | The implementation is `distribution/workers/runtime.py`. Compose, the Dockerfile `CMD`, and the VM use that module. Flags stay `--role`, `--once`, `--dry-run`, `--account`, and `--pins`. Compose service names `engine` and `scheduler` (profile `scheduler`) and `supermemory` (profile `memory`) stay as they are. |
| Scheduling | `distribution/scheduling/` | Pinterest drip, scheduler, pin ledger, inventory, library source, and the Facebook queue. Pin copy, photos, articles, and image generation stay in `agents/posting/pinterest_engine/` until the production move. |
| Account registry | `accounts/` | `brandings.py`, `tokens.py`, `pinterest.py`. Not `distribution/accounts/` and not a rename of `channels_config/`. |

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
- A channel keeps its own `page_config.py`, prompts, and tests beside each other.
- Pytest collects `test_*.py` from those trees. Probe scripts live in `scripts/`, not in `tests/`.
- Test helpers imported only by tests (`caption_fixtures.py`, `cv_ground_truth.py`) stay next to the tests that use them.

## Pipeline code extracted from `main.py`

`main.py` is the CLI process: UTF-8 setup, Drive-text repair, `--page` pre-parse, `.env`, then a call to `cli()`. The steps live in `engine/`:

| Module | Responsibility |
|---|---|
| `engine/bootstrap.py` | UTF-8, source repair, page pre-parse |
| `engine/geo_anchors.py` | `_GEO_ANCHORS`. Import the table from this module. |
| `engine/runtime.py` | Imports that must run after `ACTIVE_PAGE` is set, then the page rebind |
| `engine/acts.py` | Post-type predicates, act descriptors, topic entities |
| `engine/voice.py` | Script trim, TTS stitch, voice track |
| `engine/stills.py` | Still cache, parallel act jobs |
| `engine/reporting.py` | Cost and run summaries |
| `engine/orchestrator.py` | Re-exports `produce`, the variant worker, agentic stills, and `--test-images` |
| `engine/pipeline/` | Stage modules behind that re-export. `variant.py` sequences them. See [main-module-map.md](main-module-map.md) |
| `engine/cli.py` | `cli()`. Flags are in `engine/cli_parser.py`. The run sequence is `engine/cli_run.py` |
| `engine/log.py` | Logger proxy bound to `main`'s logger |

`from main import produce` and the other names scripts already import keep working. `python main.py` is unchanged.

QA stays in `modules/reel_qa/`. Publishers live in `distribution/publishers/`. Scheduling lives in `distribution/scheduling/`. The account registry lives in `accounts/`. `omni/` is gone. Some old `agents/posting/` module paths are still shims onto those packages. `engine/` does not grow a second copy. Pin production (copy, photos, articles, the image transformer) stays in `agents/posting/pinterest_engine/` until it moves to `production/formats/pins/`.
