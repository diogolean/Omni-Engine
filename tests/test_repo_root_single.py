"""repo_root() has one owner: utils.pipeline_paths."""
from __future__ import annotations

from pathlib import Path

from core.publishing import brandings
from modules.shot_planner import entities
from utils import pipeline_paths


def test_repo_root_single_source():
    root = Path(__file__).resolve().parents[1]
    assert pipeline_paths.repo_root() == root
    assert brandings.repo_root() == root
    assert entities.repo_root() == root
    assert brandings.DEFAULT_BRANDINGS_PATH == root / "config" / "brandings.yaml"


def test_no_production_import_of_omni():
    offenders = []
    for path in Path(pipeline_paths.repo_root()).rglob("*.py"):
        rel = path.relative_to(pipeline_paths.repo_root())
        if rel.parts[0] in {"omni", "tests", ".git"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "from omni" in text or "import omni" in text:
            offenders.append(str(rel))
    assert offenders == []
