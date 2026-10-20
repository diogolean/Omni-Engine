# Omni-Engine

Omni-Engine is the content factory for several public pages. `python main.py` drafts a post, draws or renders it, and writes a Post Planner row. The process that stays up on the Oracle VM is `python -m distribution.workers`. `python -m omni.worker` is the same process, kept as a fallback.

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
    engine service      python -m distribution.workers --role engine
    scheduler profile   python -m distribution.workers --role scheduler
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
  python -m distribution.workers --role scheduler --once --dry-run
```

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
