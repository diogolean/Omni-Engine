# -*- coding: utf-8 -*-
"""deploy/ is the home of images, compose, VM scripts, and RunPod workflows.

Old paths stay as symlinks until M14. The file copied to the VM keeps
project-root relatives so /opt/omni-engine/docker-compose.yml still mounts
the same host paths.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

_SHIMS = {
    "Dockerfile": "deploy/docker/Dockerfile",
    "docker-compose.yml": "deploy/compose/docker-compose.yml",
    "docker/supermemory": "deploy/docker/supermemory",
    ".github/scripts/remote-deploy.sh": "deploy/vm/remote-deploy.sh",
    "scripts/backup_supermemory.sh": "deploy/vm/backup_supermemory.sh",
    "infra/runpod": "deploy/runpod",
    "infra/serverless": "deploy/runpod/serverless",
}

# Bind mounts the VM copy must keep. Rewriting these to ../.. would
# point /opt/omni-engine/docker-compose.yml at /opt.
_VM_MOUNTS = (
    "./outputs:/opt/omni-engine/outputs",
    "./credentials:/opt/omni-engine/credentials",
    "./.env:/opt/omni-engine/.env",
    "./channels_config:/opt/omni-engine/channels_env",
    "./state/ancient_knowledge/store:/opt/omni-engine/channels_config/ancient_knowledge/store",
)


def test_old_paths_are_symlinks_until_m14() -> None:
    for old, new in _SHIMS.items():
        link = _ROOT / old
        assert link.is_symlink(), old
        assert link.resolve() == (_ROOT / new).resolve()


def test_compose_relatives_match_the_vm_project_directory() -> None:
    text = (_ROOT / "deploy" / "compose" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert "context: ../.." not in text
    assert "context: ." in text
    assert "dockerfile: Dockerfile" in text
    assert "context: docker/supermemory" in text
    assert "env_file:" in text
    for mount in _VM_MOUNTS:
        assert mount in text
    assert '["python", "-m", "distribution.workers", "--role", "engine"]' in text
    assert '["python", "-m", "distribution.workers", "--role", "scheduler"]' in text
    assert "ghcr.io/diogolean/omni-engine:${OMNI_IMAGE_TAG:-latest}" in text
    assert "profiles:" in text
    assert "- scheduler" in text
    assert "- memory" in text


def test_workflow_changes_only_the_paths_the_vm_reads() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )
    assert "context: .\n" in workflow
    assert "file: deploy/docker/Dockerfile\n" in workflow
    assert "context: deploy/docker/supermemory\n" in workflow
    assert "file: deploy/docker/supermemory/Dockerfile\n" in workflow
    assert (
        "deploy/compose/docker-compose.yml "
        '"$VM_USER@$VM_HOST:/opt/omni-engine/docker-compose.yml"'
    ) in workflow
    assert (
        "deploy/vm/backup_supermemory.sh "
        '"$VM_USER@$VM_HOST:/opt/omni-engine/scripts/backup_supermemory.sh"'
    ) in workflow
    assert "bash -s" in workflow
    assert "< deploy/vm/remote-deploy.sh" in workflow
    assert "needs: build\n" in workflow
    assert "needs: [build, supermemory-image]" not in workflow
    validate = (_ROOT / ".github" / "workflows" / "validate.yml").read_text(
        encoding="utf-8"
    )
    assert "Dockerfile" not in validate
    assert "docker-compose" not in validate
    assert "deploy/" not in validate


def test_vm_scripts_still_run_from_opt_omni_engine() -> None:
    remote = (_ROOT / "deploy" / "vm" / "remote-deploy.sh").read_text(encoding="utf-8")
    assert "cd /opt/omni-engine" in remote
    assert "docker compose pull engine" in remote
    assert "docker compose up -d engine" in remote
    assert "docker compose --profile scheduler stop scheduler" in remote
    assert "compose -f" not in remote
    assert "--file" not in remote
    assert "up -d supermemory" not in remote
    backup = (_ROOT / "deploy" / "vm" / "backup_supermemory.sh").read_text(
        encoding="utf-8"
    )
    assert "cd /opt/omni-engine" in backup
    assert "python scripts/backup_supermemory.py --upload-only" in backup


def test_runpod_workflows_resolve_from_the_new_home(monkeypatch) -> None:
    from production.providers.image.remote_gpu_manager import workflows_dir

    canonical = (_ROOT / "deploy" / "runpod" / "workflows").resolve()
    monkeypatch.setenv("REMOTE_GPU_WORKFLOWS_DIR", "deploy/runpod/workflows")
    found = workflows_dir()
    assert found == canonical
    assert (found / "flux_schnell_text_to_img.json").is_file()
    monkeypatch.setenv("REMOTE_GPU_WORKFLOWS_DIR", "infra/runpod/workflows")
    assert workflows_dir() == canonical
