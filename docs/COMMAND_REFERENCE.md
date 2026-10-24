# Command reference

Every command below is the current entry point. Flags match the argparse in that file. `python main.py --help` and `python tools/channel_cli.py --help` print the rest.

Deprecated shims are at the bottom. Use the paths in the sections above.

## Factory CLI

`main.py` calls `production/pipeline/cli.py`. The parser is `production/pipeline/cli_parser.py`.

```bash
python main.py --channel wonder_feed --post-type ECONOMIC_REEL --quantity 1
python main.py "Castor Oil" --channel anna_protocol --post-type STANDARD_QUOTE
```

| Flag | Meaning |
|---|---|
| `topic` | Optional positional subject. Omit it and the factory picks one |
| `--channel`, `--page` | Channel id. Default `anna_protocol`. Choices: `anna_protocol`, `master_mei`, `wonder_feed`, `down_dirty`, `ancient_knowledge`, `momma_circle`, `principles_of_wealth_finance_economics`, `endless_summer_paradise` |
| `--post-type` | Default `STANDARD_QUOTE`. Also `SMART_BAIT`, `LONG_CAPTION_IMAGE`, `CTA_CAPTION_IMAGE`, `ECONOMIC_REEL`, `ECONOMIC_REEL_LOFI`, `WAN_REEL`, `CAROUSEL`, `REFERENCE_BASED_REELS` |
| `--format` | `IMAGE_AVATAR`, `IMAGE_QUOTE`, `IMAGE_BACKGROUND`, `HYBRID_VIDEO`, `TEXT_QUOTE`, `DYNAMIC_REEL`, `SEQUENCE_REEL`, `REFERENCE_BASED_REELS` |
| `--avatar ON\|OFF` | Likeness on or off. Default comes from the channel folder |
| `--quantity`, `--count`, `-n` | How many variants. Default 1 |
| `--carousel_quantity` | Images inside one carousel. Default 3. `--quantity` is how many carousels |
| `--duration` | `ECONOMIC_REEL_LOFI` length in seconds |
| `--cta ON\|OFF` | Default `ON` |
| `--text-overlay`, `--gframe ON\|OFF` | Burn headline text onto the still |
| `--hook-parallax` | Depth-parallax hook. Same as `REEL_HOOK_PARALLAX=1`. Ken Burns stays the fallback |
| `--skip-image` | Caption and planner only |
| `--skip-caption` | Image only |
| `--agentic-pipeline` / `--no-agentic-pipeline` | Force or skip the four-tier still path |
| `--multi-variant` | Split one topic into sub-angles. Default is distinct subjects |
| `--debug` | Keep the warning traces |
| `--publish-youtube`, `--upload-youtube` | After a compile, upload as private with `publishAt`. Does not upload as unlisted |
| `--economic` | Gemini-only research and captions |
| `--premium-relay` | Gemini research plus Claude captions. Claude still needs the opt-in in the README |
| `--ignore-reel-checkpoints` | Do not resume a checkpoint. Same as `REEL_CHECKPOINT=ignore` |
| `--clear-reel-checkpoints` | Delete the checkpoint manifest first. Same as `REEL_CHECKPOINT=clear` |
| `--test` | Print scaffolds and return. When `GEMINI_API_KEY` is set, startup still opens a Gemini client before that return |
| `--test-images N` | Debug stills. Lives in `production/pipeline/stages/debug_images.py` |

`ECONOMIC_REEL_LOFI` flags, all on the same parser:

| Flag | Meaning |
|---|---|
| `--module relationship\|parenting` | RAG theme namespace |
| `--lofi-theme`, `--lofi-subtheme` | Force a theme id |
| `--lofi-mode emotional\|theme\|quote\|paraphrase` | Default `emotional` |
| `--lofi-seed-quote` | Concept for `--lofi-mode quote` |
| `--lofi-image-provider together\|gemini` | Default `together` |
| `--lofi-aphorism-id` | Bank id for `--lofi-mode paraphrase` |
| `--lofi-script JSON` | Repeatable. Render a locked script and skip the writer |
| `--lofi-allow-mixed-era` | Allow stills from different style tags |
| `--stills-only` | Writer, validator, stills, then stop |
| `--script-only` | Writer and validator, then stop |
| `--dry-run`, `--json-only` | Write the staging JSON and stop. No images, no TTS, no render |
| `--lofi-no-review` | Auto-pass gate 1. Gate 2 still needs `--lofi-approve-gate 2` |
| `--lofi-resume-from JSON` | Resume a held pipeline JSON |
| `--lofi-approve-gate 1\|2` | Approve that gate and continue |
| `--test-preview` | One still for a look review. Does not publish |
| `--prompt` | Scene text for `--test-preview` |

Early post types leave `cli` before the variant worker:

- `ECONOMIC_REEL_LOFI` → `production/formats/lofi/pipeline.py`
- `REFERENCE_BASED_REELS` → `production/formats/reference/reference_reel_engine.py`
- `WAN_REEL` registers a runner and still renders inside the variant worker. Ancient Knowledge only

## Workers

`python -m distribution.workers` loads `distribution/workers/runtime.py`.

```bash
python -m distribution.workers --role scheduler --once --dry-run
OMNI_SKIP_DOTENV=1 OUTPUT_PATH=/tmp/demo python -m distribution.workers --role scheduler --once --dry-run
python -m distribution.workers --role scheduler --account ancient_knowledge --pins 1 --dry-run
python -m distribution.workers --role engine
```

| Flag | Meaning |
|---|---|
| `--role scheduler\|engine` | Default is `OMNI_ROLE`, or `scheduler` when that is unset |
| `--once` | One scheduler wake, then exit |
| `--dry-run` | Log the pins. Do not call Pinterest. Do not record them |
| `--account` | One Pinterest account, then exit |
| `--pins` | How many pins `--account` renders. Dry-run defaults to 5 |

`--role engine` does not generate. It logs the `docker compose exec engine python main.py ...` line and sleeps until SIGTERM. The engine service uses that role so the container stays up.

## Channel CLI

```bash
python tools/channel_cli.py --channel <id> <command>
```

`tools/channel_cli.py` loads `channels_config/<id>/cli.py` when that file exists. Otherwise it forwards to `tools/pinterest_cli.py` and adds `--channel <id>`.

Endless Summer Paradise (`channels_config/endless_summer_paradise/cli.py`):

```bash
python tools/channel_cli.py --channel endless_summer_paradise scan
python tools/channel_cli.py --channel endless_summer_paradise queue
python tools/channel_cli.py --channel endless_summer_paradise schedule --dry-run
python tools/channel_cli.py --channel endless_summer_paradise reschedule --dry-run
```

`scan` accepts `--source-dir`, `--library`, `--min-duration`, `--no-stage`, `--dry-report`. `schedule` and `reschedule` accept `--interval` (default `84h`) and `--dry-run`. `schedule` also accepts `--limit`.

Principles of Wealth (`channels_config/principles_of_wealth_finance_economics/cli.py`):

```bash
python tools/channel_cli.py --channel principles_of_wealth_finance_economics scan
python tools/channel_cli.py --channel principles_of_wealth_finance_economics process
python tools/channel_cli.py --channel principles_of_wealth_finance_economics publish --mode longs --dry-run
python tools/channel_cli.py --channel principles_of_wealth_finance_economics publish --mode shorts --dry-run
python tools/channel_cli.py --channel principles_of_wealth_finance_economics playlists
python tools/channel_cli.py --channel principles_of_wealth_finance_economics status
python tools/channel_cli.py --channel principles_of_wealth_finance_economics metadata
python tools/channel_cli.py --channel principles_of_wealth_finance_economics cleanup --dry-run
```

`publish` requires `--mode longs|shorts`. Scheduled uploads stay private with `publishAt`. `--no-schedule` publishes now. `metadata` writes to YouTube only with `--apply`.

## Pinterest CLI

Direct entry, same subcommands the channel CLI forwards:

```bash
python tools/pinterest_cli.py --channel anna_protocol status
python tools/pinterest_cli.py --channel anna_protocol sync --dry-run
python tools/pinterest_cli.py --channel anna_protocol schedule --quantity 1 --dry-run
python tools/pinterest_cli.py --channel anna_protocol export --dry-run
python tools/pinterest_cli.py --channel anna_protocol transform
python tools/pinterest_cli.py --channel anna_protocol check-readiness
python tools/pinterest_cli.py --channel anna_protocol validate-token
```

Every subcommand accepts `--channel`, `--env`, and `--inventory-dir`.

| Command | What it does |
|---|---|
| `sync` | Rebuild `master_inventory.json`. `--limit`, `--force`, `--dry-run`, `--no-ai`, `--ai-delay` |
| `schedule` | Publish pins with a gap. `--quantity` / `-n`, `--min-hours`, `--max-hours`, `--dry-run`, `--no-wait` |
| `export` | Post Planner CSV of unscheduled posts. `--limit`, `--posts-per-day`, `--slots`, `--start`, `--mark-exported`, `--dry-run`, `--open` |
| `transform` | Preview a 2:3 card. `--post-id`, `--method cover\|blurred_padding\|center_crop`, `--open` |
| `status` | Queue and history |
| `check-readiness` | Pre-flight before the first publish |
| `validate-token` | Check the token and refresh it when it is expired |

`sync` calls Claude only when the Claude opt-in is set. `--no-ai` stays on the regex templates.

## Operator tools

These are the supported scripts in `tools/`. `tools/oneoff/` and `tools/lofi/` are experiments.

| Command | What it does |
|---|---|
| `python tools/backup_supermemory.py` | Archive the Supermemory volume and upload it to B2. `--day`, `--backup-dir`, `--volume`, `--image`, `--upload-only`. Not scheduled. On the VM run `deploy/vm/backup_supermemory.sh` by hand (`/opt/omni-engine/scripts/backup_supermemory.sh` after deploy copies it) |
| `python tools/backfill_metadata.py` | Audit or write channel catalogs. `--channel` or `--all`, `--dry-run`, `--skip-restore` |
| `python tools/validate_aiwake_captions.py` | Wrapper for `products/aiwake/tools/validate_aiwake_captions.py`. Optional library path, `--ids`, `--max-examples` |
| `python tools/migrate_content_items.py` | Upgrade a library to ContentItem v1 in place. `--channel` or `--path` |
| `python tools/audit_google_costs.py` | Sum Gemini spend from local logs. `--logs-dir`, `--since`, `--until`, `--peak-start`, `--peak-end` |
| `python tools/cleanup_ephemeral.py` | Drop ephemeral files under outputs. `--channel` or `--all`, `--dry-run` |
| `python tools/migrate_local_outputs_to_factory.py` | Copy a local outputs tree. `--channel` (default `ancient_knowledge`), `--source`, `--keep-source` |
| `python tools/repair_doubled_output_paths.py` | No flags. Rewrites doubled `OUTPUT_PATH` prefixes under the Ancient Knowledge outputs tree |
| `python tools/restore_channel_state.py` | Restore channel state from the Drive backup. `--channel` or `--all` |

Aiwake show scripts live in `products/aiwake/tools/`. `channels_config/aiwake/tools/` re-exports them until M14.

## Docker Compose

Project directory is the repo root. The compose file is `deploy/compose/docker-compose.yml`.

```bash
docker compose --project-directory . -f deploy/compose/docker-compose.yml up -d --build engine
docker compose --project-directory . -f deploy/compose/docker-compose.yml exec engine \
  python main.py --channel wonder_feed --post-type ECONOMIC_REEL --quantity 1
docker compose --project-directory . -f deploy/compose/docker-compose.yml \
  --profile scheduler run --rm --no-deps scheduler \
  python -m distribution.workers --role scheduler --once --dry-run
```

On the VM the copied file is `/opt/omni-engine/docker-compose.yml`:

```bash
cd /opt/omni-engine && docker compose pull engine && docker compose up -d engine
```

Deploy starts `engine` only. Add the scheduler locally with `--profile scheduler`. Supermemory is `--profile memory`.

A push to `main` runs Validate first. Deploy starts from `workflow_run` after that run succeeds. Roll back with a `workflow_dispatch` whose `image_tag` is a 40-character SHA or `latest`. See [architecture/diagrams.md](architecture/diagrams.md) and [deploy.md](deploy.md).

## Tests

Same commands as `.github/workflows/validate.yml`. The suite cap is 120 seconds (`pyproject.toml`). Render tests opt into 300 seconds.

```bash
pytest -q tests channels_config \
  --ignore=channels_config/aiwake \
  --deselect tests/test_lofi_emotional_pipeline.py::test_gemini38_provider_uses_only_configured_primary_model

pytest -q products/aiwake \
  --deselect products/aiwake/tests/test_caption_validator.py::test_truncated_plugs_it_fails \
  --deselect products/aiwake/tests/test_distribution_pipeline.py::test_tiktok_caption_is_hook_quote_trigger \
  --deselect products/aiwake/tests/test_distribution_pipeline.py::test_planner_imports_root_videos_with_optimized_captions
```

## Deprecated until M14

These still resolve. New commands use the paths above.

| Old path | Current path |
|---|---|
| `engine/`, `agents/orchestrator/` | `production/pipeline/` |
| `agents/writer/`, `agents/rag/` | `production/writers/` |
| `agents/media/` (B2 client) | `shared/storage/` |
| `agents/posting/pinterest_main.py` | `python tools/channel_cli.py --channel <id>` or `python tools/pinterest_cli.py` |
| `channels_config/endless_summer_paradise/esp_main.py` | `python tools/channel_cli.py --channel endless_summer_paradise` |
| `channels_config/principles_of_wealth_finance_economics/wealth_main.py` | `python tools/channel_cli.py --channel principles_of_wealth_finance_economics` |
| `channels_config/aiwake/tools/` | `products/aiwake/tools/` |
| `scripts/backup_supermemory.py` | `python tools/backup_supermemory.py` (the VM shell script is `deploy/vm/backup_supermemory.sh`) |
| `core/animator/` | `animation/parametric/` and `products/aiwake/animator/` |
| `modules/reel_qa/`, `quality/VisualQA_Agent/` | `production/qa/` |
| `utils.pipeline_paths`, `utils.process_tools` | `shared.paths`, `shared.ffmpeg` |
