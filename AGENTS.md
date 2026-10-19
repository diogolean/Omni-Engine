# Agent rules

Read `docs/architecture/REPO_LAYOUT.md` before adding files.

- The repo root holds entry points, config, containers, CI, docs, and the packages listed there. Do not add scratch files, logs, screenshots, or one-off probes beside `main.py`.
- Secrets go in `.env` only. `.env.example` lists the names and stays empty. Never hardcode tokens, including test defaults.
- Video, audio, `*.npz`, `*.psd`, error screenshots, and `outputs/` stay out of git.
- Channel store state under `channels_config/<channel>/store/` stays gitignored and dockerignored, and only lives in the owner's Drive backup. `channels_config/aiwake/store/portfolio/` stays tracked and in the image. Files already tracked in `core/economic_reel_lofi/store/` stay in git, and `aphorism_recent.json` stays gitignored. `.cursor/rules/` is versioned. `scene_library/`, `artefact_cache/`, and `reel_checkpoints/` under a channel store stay gitignored.
- Tests live in `tests/` or `channels_config/<channel>/tests/`. Name them for the behavior they lock, not for a smoke round number.
- Do not rename or move: `agents/media/`, `config/pinterest_accounts.yaml`, `config/pinterest_image_map.yaml`, `channels_config/*/store`, `scene_library`, `artefact_cache`, the compose services `engine` and `scheduler`, the `memory` profile, or the worker entry `python -m core.worker`. Keep the `$OUTPUT_PATH/_omni` ledger and the `OMNI_*` env names exactly. `run_main_noupload.py` stays a root entry point if it is added.
- `main.py` stays the CLI. New pipeline steps go in `engine/` or the package that already owns that concern (`modules/reel_qa/`, `agents/posting/`, `agents/media/`).
- Temp files: `temp_path` / `review_path` in `utils/pipeline_paths.py`. Logs: `outputs/logs/` via `pipeline_logs_dir()`.
