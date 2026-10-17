# -*- coding: utf-8 -*-
"""Supermemory service pin, compose profile, and B2 backup guardrails."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from scripts.backup_supermemory import (
    archive_name,
    b2_key,
    backup_once,
    main,
    prune_local,
    upload_archive,
)

from agents.media.b2_budget import B2DailyCapError

_ROOT = Path(__file__).resolve().parents[1]
_SHA = "b789be16cd7192b3c08e882396c0a94ba330ea467d5a7fb1b39ac3684abf5374"


def _executable_lines(path: Path) -> str:
    kept: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def test_image_pin_is_0_0_9_with_checksum() -> None:
    text = (_ROOT / "docker" / "supermemory" / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG SUPERMEMORY_VERSION=0.0.9" in text
    assert _SHA in text
    assert "sha256sum -c" in text
    assert "linux/arm64" in text
    assert "0.0.8 and older" in text
    entry = (_ROOT / "docker" / "supermemory" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "GEMINI_API_KEY=$GOOGLE_API_KEY" in entry
    assert "echo" not in entry


def test_compose_service_is_opt_in_and_unpublished() -> None:
    doc = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = doc["services"]["supermemory"]
    assert service["profiles"] == ["memory"]
    assert "ports" not in service
    assert service["volumes"] == ["supermemory-data:/data"]
    assert "supermemory-data" in doc["volumes"]
    assert service["environment"]["SUPERMEMORY_HOST"] == "0.0.0.0"
    assert service["environment"]["SUPERMEMORY_DATA_DIR"] == "/data"
    assert service["mem_limit"] == "2g"
    limits = service["deploy"]["resources"]["limits"]
    assert str(limits["cpus"]) == "1.0"
    assert str(limits["memory"]) in {"2G", "2g"}
    rendered = yaml.safe_dump(service)
    assert "GEMINI_API_KEY=" not in rendered
    assert "sk-" not in rendered
    engine = doc["services"]["engine"]["environment"]
    assert engine["SUPERMEMORY_BASE_URL"] == "http://supermemory:6767"


def test_deploy_does_not_start_memory_or_force_the_scheduler() -> None:
    script = _executable_lines(_ROOT / ".github" / "scripts" / "remote-deploy.sh")
    assert "--profile memory" not in script
    assert "up -d supermemory" not in script
    assert "up -d engine" in script
    workflow = (_ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "docker/supermemory/Dockerfile" in workflow
    assert "platforms: linux/arm64" in workflow
    assert "needs: build\n" in workflow
    assert "needs: [build, supermemory-image]" not in workflow


def test_host_backup_restarts_only_when_it_was_running() -> None:
    shell = (_ROOT / "scripts" / "backup_supermemory.sh").read_text(encoding="utf-8")
    assert 'if [ -n "$running" ]' in shell
    assert "docker compose --profile memory stop supermemory" in shell
    assert "docker compose --profile memory up -d supermemory" in shell
    assert "--upload-only" in shell
    assert "b2_delete" not in shell
    assert "delete_object" not in shell


def test_local_retention_keeps_seven_archives(tmp_path: Path) -> None:
    for index in range(9):
        (tmp_path / f"2026-10-{index:02d}.tgz").write_bytes(b"x")
    removed = prune_local(tmp_path, keep=7)
    left = sorted(path.name for path in tmp_path.glob("*.tgz"))
    assert len(left) == 7
    assert len(removed) == 2
    assert left[0] == "2026-10-02.tgz"


def test_upload_skips_an_object_that_already_exists(tmp_path: Path) -> None:
    day = "2026-10-17"
    archive = tmp_path / archive_name(day)
    archive.write_bytes(b"archive")
    uploaded: list[str] = []
    status = upload_archive(
        archive,
        day=day,
        existing_keys=[b2_key(day)],
        upload=lambda key, path: uploaded.append(key),
    )
    assert status == "skipped"
    assert uploaded == []
    assert archive.is_file()


def test_budget_error_keeps_the_local_archive(tmp_path: Path) -> None:
    day = "2026-10-17"
    archive = tmp_path / archive_name(day)
    archive.write_bytes(b"archive")

    def _refuse(key: str, path: Path) -> None:
        raise B2DailyCapError("class c")

    with pytest.raises(B2DailyCapError):
        upload_archive(archive, day=day, existing_keys=[], upload=_refuse)
    assert archive.is_file()


def test_archive_restarts_a_running_service_and_leaves_a_stopped_one(
    tmp_path: Path,
) -> None:
    day = "2026-10-17"

    def _running(argv: list[str]) -> str:
        if "ps" in argv:
            return "container\n"
        if argv[:2] == ["docker", "run"]:
            (tmp_path / archive_name(day)).write_bytes(b"tar")
        return ""

    calls: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        calls.append(list(argv))
        return _running(argv)

    uploaded: list[str] = []
    status = backup_once(
        day=day,
        backup_dir=tmp_path,
        runner=runner,
        existing_keys=[],
        upload=lambda key, path: uploaded.append(key),
    )
    assert status == "uploaded"
    assert uploaded == [b2_key(day)]
    assert any("stop" in argv and "supermemory" in argv for argv in calls)
    assert any(argv[-3:] == ["up", "-d", "supermemory"] for argv in calls)

    idle: list[list[str]] = []

    def idle_runner(argv: list[str]) -> str:
        idle.append(list(argv))
        if argv[:2] == ["docker", "run"]:
            (tmp_path / "other").mkdir(exist_ok=True)
        return ""

    backup_once(
        day="2026-10-18",
        backup_dir=tmp_path,
        runner=idle_runner,
        existing_keys=[b2_key("2026-10-18")],
        upload=lambda key, path: (_ for _ in ()).throw(AssertionError(key)),
    )
    assert not any("stop" in argv for argv in idle)
    assert not any("up" in argv for argv in idle)


def test_failed_tar_still_restarts_when_the_service_was_stopped_by_us(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> str:
        calls.append(list(argv))
        if "ps" in argv:
            return "container\n"
        if argv[:2] == ["docker", "run"]:
            raise subprocess.CalledProcessError(1, argv)
        return ""

    with pytest.raises(subprocess.CalledProcessError):
        backup_once(
            day="2026-10-17",
            backup_dir=tmp_path,
            runner=runner,
            existing_keys=[],
            upload=lambda key, path: None,
        )
    assert any(argv[-3:] == ["up", "-d", "supermemory"] for argv in calls)


def test_upload_only_returns_2_on_the_daily_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(*args, **kwargs):
        raise B2DailyCapError("class c")

    monkeypatch.setattr("scripts.backup_supermemory.upload_archive", _boom)
    code = main(
        ["--upload-only", "--day", "2026-10-17", "--backup-dir", str(tmp_path)]
    )
    assert code == 2


def test_backup_module_has_no_b2_delete() -> None:
    source = (_ROOT / "scripts" / "backup_supermemory.py").read_text(encoding="utf-8")
    assert "delete_object" not in source
    assert "b2_delete" not in source
    assert "DeleteObject" not in source
