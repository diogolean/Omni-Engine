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

## Machine-specific paths

Nothing in the code needs a particular drive letter. These optional keys move the old Windows defaults. Leave them unset and the historical paths stay.

| Key | What it moves | Default when unset |
|---|---|---|
| `OUTPUT_PATH` / `ASSETS_PATH` | factory outputs and assets | `<OMNI_DRIVE_ROOT>/@MEDIAUPSCALE_FACTORY_DYNAMIC_CONTENT/Unified Multi-Page Factory/...` |
| `OMNI_DRIVE_ROOT` | Drive tree root used by the fallbacks above, the wealth source folder and the Anna Protocol asset lookup | `G:\My Drive\Z sosFiles\Z_act\@ NETWORK` |
| `REFERENCE_AVATAR_PATH` | last-resort reference avatar (after `REFERENCE_IMAGE_PATH` and the page avatar) | the Anna Protocol avatar under `OMNI_DRIVE_ROOT` |
| `FFMPEG_BINARY` | ffmpeg used by `shared.ffmpeg.resolve_ffmpeg` (and the fingerprint and audio-patch callers), tried first | `PATH`, then imageio_ffmpeg, then `C:\ffmpeg\bin\ffmpeg.exe` |
| `FONT_DIRS` | extra font folders for `shared.paths.find_font()` | repo `Fonts/`, then `%WINDIR%\Fonts`, then the usual Linux/macOS folders |
| `OMNI_SKIP_DOTENV` | skips the `.env` load inside `shared.paths` | load the repo `.env` without overriding existing keys |

Windows system fonts are resolved through `shared.paths.windows_fonts_dir()` (`%WINDIR%\Fonts`). `utils.pipeline_paths` is that same module. Aiwake keeps its own font list under `channels_config/aiwake/`.
