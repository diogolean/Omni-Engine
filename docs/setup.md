# Setup

Python 3.12 and ffmpeg on `PATH`. ffmpeg is what MoviePy uses to write video.

```bash
git clone https://github.com/diogolean/Omni-Engine.git
cd Omni-Engine
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-ci.txt   # pytest and ruff
cp .env.example .env
```

Fill `.env` from `.env.example`. That file lists the names and stays empty of secrets. Do not commit `.env` or a backup such as `.env.bak_20261017_prepinterest`.

Three requirement files stay separate:

| File | Who installs it |
|---|---|
| `requirements.txt` | A local factory, including `mediapipe` and `pyautogui` |
| `requirements-docker.txt` | The `linux/arm64` image. Drops `mediapipe` (no aarch64 wheel) and `pyautogui` (needs a desktop) |
| `requirements-ci.txt` | Pull-request checks. Pytest, ruff, and the imports that suite needs, without the media stack |

Commands, channels, and post types: [COMMAND_REFERENCE.md](COMMAND_REFERENCE.md).

Each page is `channels_config/<id>/` with `page_config.py`, `master_dna.json`, and `logo/logo.png`. `python main.py --help` lists the flags. Layout rules: [architecture/REPO_LAYOUT.md](architecture/REPO_LAYOUT.md).
