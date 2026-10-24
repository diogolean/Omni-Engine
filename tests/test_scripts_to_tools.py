# -*- coding: utf-8 -*-
"""scripts/ is a shim onto tools/ until M14. portfolio_showcase stays at the root."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_backup_shim_is_the_tools_module() -> None:
    import scripts.backup_supermemory as shim
    import tools.backup_supermemory as impl

    assert shim is impl
    assert shim.archive_name("2026-10-24") == "2026-10-24.tgz"
    assert shim.b2_key("2026-10-24") == "backups/supermemory/2026-10-24.tgz"


def test_script_shims_are_real_files_and_the_old_paths_still_run() -> None:
    backup = _ROOT / "scripts" / "backup_supermemory.py"
    lofi = _ROOT / "scripts" / "lofi" / "dry_run_reel.py"
    ps1 = _ROOT / "scripts" / "run_pinterest_local.ps1"
    for path in (backup, lofi, ps1):
        assert path.is_file()
        assert not path.is_symlink()
    assert (_ROOT / "tools" / "backup_supermemory.py").is_file()
    assert (_ROOT / "tools" / "lofi" / "dry_run_reel.py").is_file()
    assert (_ROOT / "tools" / "oneoff" / "inspect_chroma_db.py").is_file()
    assert not (_ROOT / "tools" / "portfolio").exists()
    assert (_ROOT / "portfolio_showcase" / "README.md").is_file()
    out = subprocess.check_output(
        ["git", "ls-files", "-s"],
        cwd=_ROOT,
        text=True,
    )
    assert [line for line in out.splitlines() if line.startswith("120000 ")] == []
    help_text = subprocess.run(
        [sys.executable, str(backup), "--help"],
        cwd=_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert help_text.returncode == 0, help_text.stderr
    assert "upload-only" in help_text.stdout


def test_oneoff_parent_index_still_finds_the_repo_root() -> None:
    text = (_ROOT / "tools" / "oneoff" / "inspect_chroma_db.py").read_text(encoding="utf-8")
    assert ".parents[2]" in text
    assert ".parents[1]" not in text
    checks = (_ROOT / "tools" / "oneoff" / "checks" / "audio_engine_check.py").read_text(
        encoding="utf-8"
    )
    assert ".parents[3]" in checks
