# -*- coding: utf-8 -*-
"""Process bootstrap that must run before config and channel imports."""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _repair_drive_text_file(file_path: Path) -> bool:
    """
    Re-encode a UTF-16 file (Google Drive desktop corruption) as clean UTF-8,
    or strip stray NUL bytes introduced by sync.

    Handles:
    - UTF-16-LE / UTF-16-BE with explicit BOM
    - UTF-16-LE without BOM (Python files starting '#\x00', JSON files starting '{\x00')
    - Files with embedded NUL bytes but otherwise valid UTF-8
    """
    if not file_path.is_file():
        return False
    raw = file_path.read_bytes()

    text = ""
    if raw.startswith(b"\xff\xfe"):
        text = raw[2:].decode("utf-16-le")
    elif raw.startswith(b"\xfe\xff"):
        text = raw[2:].decode("utf-16-be")
    elif raw.startswith((b"#\x00 ", b"#\x00-")):
        text = raw.decode("utf-16-le")
    elif raw.startswith(b"{\x00") or raw.startswith(b"[\x00"):
        text = raw.decode("utf-16-le")

    if text:
        file_path.write_text(text, encoding="utf-8", newline="\n")
        return True

    if b"\x00" not in raw:
        return False
    file_path.write_bytes(raw.replace(b"\x00", b""))
    return True


def _clean_all_python_sources(engine_root: Path) -> None:
    """
    Glob every .py and .json in the project and repair Drive-sync encoding issues.

    Runs at bootstrap. The full recursive scan is expensive on Google Drive
    (thousands of JSON/metric store files), so it is executed on a background
    daemon thread to keep ``python main.py`` responsive. Disposable / dependency
    directories are skipped — those files must never be rewritten.
    """
    import threading as _threading

    _EXCLUDE_DIR_NAMES = {
        "__pycache__", ".git", ".svn", ".hg",
        ".venv", "venv", "env", "node_modules",
        "site-packages", "dist-packages", ".pytest_cache",
    }

    def _clean() -> None:
        for pattern in ("*.py", "*.json"):
            for candidate in sorted(engine_root.rglob(pattern)):
                try:
                    if any(
                        part in _EXCLUDE_DIR_NAMES
                        for part in candidate.relative_to(engine_root).parts
                    ):
                        continue
                except ValueError:
                    pass
                if _repair_drive_text_file(candidate):
                    try:
                        rel = candidate.relative_to(engine_root)
                    except ValueError:
                        rel = candidate
                    print(f"[bootstrap] Repaired {rel}", file=sys.stderr)

    # Non-blocking: let the engine start producing immediately.
    _repair_thread = _threading.Thread(
        target=_clean, name="bootstrap-drive-repair", daemon=True
    )
    _repair_thread.start()


def _preparse_active_page() -> str:
    """
    Extract --channel (alias --page) value from sys.argv without full argparse.
    Sets ACTIVE_PAGE in the environment so all subsequent module imports
    (config, persona_dna) resolve the correct page-specific paths.
    Returns the page slug for informational logging.
    """
    page = "anna_protocol"  # default
    argv = sys.argv[1:]
    for i, arg in enumerate(argv):
        if arg in ("--channel", "--page") and i + 1 < len(argv):
            page = argv[i + 1].lower().strip()
            break
        if arg.startswith(("--channel=", "--page=")):
            page = arg.split("=", 1)[1].lower().strip()
            break
    os.environ["ACTIVE_PAGE"] = page
    return page
