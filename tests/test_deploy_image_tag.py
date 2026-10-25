# -*- coding: utf-8 -*-
"""Rollback tags are a full SHA or latest, and the VM records the tag after pull."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "deploy" / "vm" / "remote-deploy.sh"
_WORKFLOW = _ROOT / ".github" / "workflows" / "deploy.yml"
_SHA = "a" * 40


def test_dispatch_accepts_only_a_full_sha_or_latest_and_inspects_the_manifest() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "^(latest|[0-9a-f]{40})$" in text
    assert "{7,40}" not in text
    assert "docker manifest inspect" in text
    assert "--password-stdin" in text
    assert text.index("^(latest|[0-9a-f]{40})$") < text.index("docker login")
    assert text.index("docker login") < text.index("docker manifest inspect")
    assert "ghcr.io/diogolean/omni-engine:${TAG}" in text
    script = _SCRIPT.read_text(encoding="utf-8")
    assert "^(latest|[0-9a-f]{40})$" in script
    assert "{7,40}" not in script


def _docker_stub(bin_dir: Path, calls: Path) -> None:
    docker = bin_dir / "docker"
    docker.write_text(
        "\n".join(
            [
                "#!/bin/bash",
                "printf '%s\\n' \"$*\" >> \"$DEPLOY_CALLS\"",
                "if [[ \"$1\" == compose && \"$*\" == *pull* ]]; then",
                "  if grep -q \"^OMNI_IMAGE_TAG=${IMAGE_TAG}$\" .env; then",
                "    printf '%s\\n' 'file-has-new-tag' >> \"$DEPLOY_CALLS\"",
                "  else",
                "    printf '%s\\n' 'file-still-old-tag' >> \"$DEPLOY_CALLS\"",
                "  fi",
                "  printf '%s\\n' \"env=${OMNI_IMAGE_TAG:-}\" >> \"$DEPLOY_CALLS\"",
                "  if [[ \"${FAIL_PULL:-}\" == 1 ]]; then",
                "    exit 1",
                "  fi",
                "fi",
                "exit 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    calls.write_text("", encoding="utf-8")


def _run(
    root: Path,
    bin_dir: Path,
    calls: Path,
    tag: str,
    *,
    fail_pull: bool = False,
    channel_dirs: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["OMNI_DEPLOY_ROOT"] = str(root)
    env["IMAGE_TAG"] = tag
    env["DEPLOY_CALLS"] = str(calls)
    env["FAIL_PULL"] = "1" if fail_pull else "0"
    if channel_dirs is not None:
        env["CHANNEL_DIRS"] = channel_dirs
    return subprocess.run(
        ["bash", str(_SCRIPT)],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_image_tag_is_written_only_after_a_successful_pull(tmp_path: Path) -> None:
    root = tmp_path / "opt"
    root.mkdir()
    env_file = root / ".env"
    env_file.write_text("SCHEDULER_ENABLED=0\nOMNI_IMAGE_TAG=old\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    _docker_stub(bin_dir, calls)

    failed = _run(root, bin_dir, calls, _SHA, fail_pull=True)
    assert failed.returncode != 0
    assert env_file.read_text(encoding="utf-8") == "SCHEDULER_ENABLED=0\nOMNI_IMAGE_TAG=old\n"
    assert not (root / "releases.log").exists()
    during = calls.read_text(encoding="utf-8")
    assert "file-still-old-tag" in during
    assert "file-has-new-tag" not in during
    assert f"env={_SHA}" in during

    calls.write_text("", encoding="utf-8")
    ok = _run(root, bin_dir, calls, "latest")
    assert ok.returncode == 0, ok.stderr
    text = env_file.read_text(encoding="utf-8")
    assert "OMNI_IMAGE_TAG=latest\n" in text
    assert "OMNI_IMAGE_TAG=old" not in text
    assert "latest" in (root / "releases.log").read_text(encoding="utf-8")
    pulled = calls.read_text(encoding="utf-8")
    assert "file-still-old-tag" in pulled
    assert "file-has-new-tag" not in pulled
    assert "env=latest" in pulled


def test_remote_deploy_rejects_a_short_sha(tmp_path: Path) -> None:
    root = tmp_path / "opt"
    root.mkdir()
    env_file = root / ".env"
    env_file.write_text("OMNI_IMAGE_TAG=old\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    _docker_stub(bin_dir, calls)
    result = _run(root, bin_dir, calls, "abc1234")
    assert result.returncode != 0
    assert "Refusing image tag" in result.stderr
    assert env_file.read_text(encoding="utf-8") == "OMNI_IMAGE_TAG=old\n"
    assert calls.read_text(encoding="utf-8") == ""


def test_remote_deploy_mkdir_rejects_paths_outside_the_channel_regex(
    tmp_path: Path,
) -> None:
    script = _SCRIPT.read_text(encoding="utf-8")
    pattern = r"^(channels_config|state)/[a-z0-9_]+(/store)?$"
    assert pattern in script
    assert script.index(pattern) < script.index('mkdir -p "$rel"')
    assert "mkdir -p ${CHANNEL_DIRS}" not in script

    root = tmp_path / "opt"
    root.mkdir()
    env_file = root / ".env"
    env_file.write_text("OMNI_IMAGE_TAG=old\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    _docker_stub(bin_dir, calls)

    bad = _run(
        root,
        bin_dir,
        calls,
        _SHA,
        channel_dirs="channels_config/anna_protocol ../etc",
    )
    assert bad.returncode != 0
    assert "Refusing channel path" in bad.stderr
    assert not (root / "channels_config").exists()
    assert not (root / "etc").exists()
    assert env_file.read_text(encoding="utf-8") == "OMNI_IMAGE_TAG=old\n"
    assert calls.read_text(encoding="utf-8") == ""

    calls.write_text("", encoding="utf-8")
    ok = _run(
        root,
        bin_dir,
        calls,
        _SHA,
        channel_dirs=(
            "channels_config/anna_protocol "
            "channels_config/ancient_knowledge "
            "state/ancient_knowledge/store"
        ),
    )
    assert ok.returncode == 0, ok.stderr
    assert (root / "channels_config" / "anna_protocol").is_dir()
    assert (root / "channels_config" / "ancient_knowledge").is_dir()
    assert (root / "state" / "ancient_knowledge" / "store").is_dir()
    assert "OMNI_IMAGE_TAG=" + _SHA in env_file.read_text(encoding="utf-8")
