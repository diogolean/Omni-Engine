# Project structure

The repository root holds entry points, configuration, and top-level packages. Scratch files and tests do not live beside `main.py`.

## What stays at the root

- Entry points and config: `main.py`, `config.py`, `channel_loader.py`, `requirements.txt`, `requirements-docker.txt`, `requirements-ci.txt`, `Dockerfile`, `docker-compose.yml`, `.gitignore`, `README.md`, `.env`.
- Top-level packages: `agents/`, `channels_config/`, `config/` (`brandings.yaml`), `core/`, `models_avatar/`, `omni/`, `utils/`, `quality/`, `scripts/`, `tests/`, `tools/`, `docs/`, `.github/`.

`.env`, `credentials/`, and `.supermemory/` stay on disk and stay out of git.

## Where scratch files go

Temp files, previews, review dumps, and logs go under the per-project outputs tree:

| Kind | Path | Helper |
|---|---|---|
| Scratch | `outputs/<channel>/_tmp/` | `temp_path(channel, *parts)` |
| Preview and review | `outputs/<channel>/_review/` | `review_path(channel, *parts)` |
| Engine logs | `outputs/logs/` | `pipeline_logs_dir()` |

Both helpers live in `utils/pipeline_paths.py` and follow `OUTPUT_PATH`. Do not create `_tmp*`, `_cp_preview`, `_review`, or log folders at the repository root.

## Tests

Tests live in the project's `tests/` folder:

- Factory-wide tests: `tests/`
- Channel tests: `channels_config/<channel>/tests/`

Pytest must be able to collect every test module from those folders.

## Cleanup

When a temp folder is no longer referenced by code, delete it. Do not leave empty `_tmp*`, `_review`, or `_cp_preview` directories at the repo root.
