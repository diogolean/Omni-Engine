# Contributing

The repo is public. Secrets stay out of it.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-ci.txt
cp .env.example .env
```

Put API keys and tokens in `.env` only. `.env.example` lists names and stays empty.

## Checks

```bash
ruff check .
pytest -q tests channels_config
```

CI runs those on every pull request (`.github/workflows/validate.yml`, job name `lint and tests`). A few tests that already failed on the base branch are deselected there. Do not deselect a test you broke.

Optional heavy dependencies (moviepy, OpenCV, google-genai) are skipped with `pytest.importorskip` when they are not installed. Install `requirements.txt` to run them.

## Where code goes

Read `docs/architecture/REPO_LAYOUT.md` first.

- Root: entry points, config, containers, CI, docs, and the packages on that allowlist.
- New pipeline steps: `production/pipeline/`, or the package that already owns the concern (`production/qa/`, `production/providers/`, `production/media/`, `production/writers/`, `production/formats/`, `production/library/`, `distribution/publishers/`, `shared/storage/`). `engine/` is a shim. Pin production is `production/formats/pins/`. Cost is `production/cost/`. Root `config.py` is the settings facade.
- Tests: `tests/` or `channels_config/<channel>/tests/`. Name the file for the behavior, not a round number.
- Scratch, logs, renders, and review dumps: `outputs/<channel>/` via `temp_path` and `review_path`.

Do not rename `agents/media/`, `config/pinterest_accounts.yaml`, `config/pinterest_image_map.yaml`, `channels_config/*/store`, `scene_library`, or `artefact_cache`. Compose service names `engine` and `scheduler`, and the `memory` profile, stay as they are. The VM command is `python -m distribution.workers`. A push to `main` deploys the Oracle VM.

`ACTIVE_PAGE` is set in `main.py` before `engine.runtime` imports the media stack. Later imports read that name at import time. Do not reorder that bootstrap.
