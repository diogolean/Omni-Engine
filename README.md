# Omni-Engine

Omni-Engine is the content factory for several public pages. `python main.py` drafts a post, draws or renders it, and writes a Post Planner row. The process that stays up on the Oracle VM is `python -m omni.worker`.

The repository is public. API keys, tokens, and credentials live only in `.env` (gitignored). `.env.example` lists the names and stays empty.

## Architecture

```
python main.py --channel <page> --post-type ECONOMIC_REEL
        │
        ▼
main.py          UTF-8, ACTIVE_PAGE, .env
        │
        ▼
engine/          produce → stills, voice, compile
        │
        ├── agents/media/      image, audio, video, B2
        ├── modules/reel_qa/   seal the MP4 before a row is publishable
        └── agents/posting/    Post Planner xlsx, YouTube
                 │
                 ▼
        outputs/<page>/  (gitignored)   and   Backblaze B2

Oracle VM (docker compose)
    engine service      python -m omni.worker --role engine
    scheduler profile   python -m omni.worker --role scheduler
                 │
                 ▼
    WordPress bot reads the xlsx → Facebook
    Pinterest and YouTube publish from the VM
```

`main.py` is the operator CLI. It sets UTF-8, `ACTIVE_PAGE`, and `.env`, then calls `engine/`. `from main import produce` still works.

## Quickstart

```bash
git clone https://github.com/diogolean/Omni-Engine.git
cd Omni-Engine
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-ci.txt
cp .env.example .env
pytest -q tests channels_config
python main.py --help
```

A real render needs the keys in `.env` and ffmpeg on `PATH`. CI installs the smaller `requirements-ci.txt` set and runs that same pytest tree. Dry-run a Pinterest wake without publishing:

```bash
docker compose --profile scheduler run --rm --no-deps scheduler \
  python -m omni.worker --role scheduler --once --dry-run
```

## Keyless demo

No API keys, no `.env`, nothing published. This needs only the CI requirements and ffmpeg:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-ci.txt

# 1. Reel pipeline end to end on fakes: stages, real ffmpeg render, voice mux, QA gate.
OMNI_SKIP_DOTENV=1 pytest -q tests/test_engine_pipeline_split.py tests/test_audio_first_pipeline.py \
  tests/test_reel_ladder.py tests/test_reel_safety_cap_and_ffmpeg.py \
  tests/test_reel_voice_mux_and_sentence_beats.py tests/test_reel_object_beats_and_encode.py \
  tests/test_reel_visual_qa.py tests/test_ffmpeg_timeout.py

# 2. One scheduler wake in dry-run against an empty outputs folder.
OMNI_SKIP_DOTENV=1 OUTPUT_PATH=/tmp/omni-demo \
  python -m omni.worker --role scheduler --once --dry-run
```

The scheduler logs which pins it would post and why each account is skipped, then exits 0. `python main.py --test` is not keyless: it still opens a Gemini client, so it needs `google-genai` and a key.

## Docs

The index is [docs/README.md](docs/README.md).

| | |
|---|---|
| Layout | [docs/architecture/REPO_LAYOUT.md](docs/architecture/REPO_LAYOUT.md) |
| Where it runs | [docs/architecture/overview.md](docs/architecture/overview.md) |
| Commands | [docs/COMMAND_REFERENCE.md](docs/COMMAND_REFERENCE.md) |
| Setup | [docs/setup.md](docs/setup.md) |
| Deploy | [docs/deploy.md](docs/deploy.md) |

The repository is public. No license has been chosen yet.
