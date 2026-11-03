# -*- coding: utf-8 -*-
"""Config loader: N brandings × N platforms, publish only where active."""

from __future__ import annotations

import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from accounts.brandings import BrandingConfigError, active_accounts, load_brandings
from accounts.tokens import remember_rotated_token, resolve_pinterest_token
from distribution.publishers.base import Account
from distribution.workers.runtime import interval_seconds, run_cycle


def test_brandings_model_multiple_pinterest_accounts() -> None:
    brandings = load_brandings()
    by_id = {row.branding_id: row for row in brandings}
    assert "ancient_knowledge" in by_id
    assert "anna_protocol" in by_id

    ancient = by_id["ancient_knowledge"].platform("pinterest")
    anna = by_id["anna_protocol"].platform("pinterest")
    assert ancient is not None and anna is not None
    assert ancient.active is True
    assert anna.active is True
    assert ancient.token_env == "PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE"
    assert anna.token_env == "PINTEREST_ACCESS_TOKEN_ANNA_PROTOCOL"
    assert ancient.board_id_env != anna.board_id_env
    assert ancient.account_id == "ancient_knowledge"
    assert anna.account_id == "anna_protocol"

    pins = active_accounts(brandings, platform="pinterest")
    pin_ids = [account.account_id for account in pins]
    assert pin_ids == ["ancient_knowledge", "anna_protocol"]
    assert all(account.active for account in pins)


def test_inactive_platform_is_not_a_publish_target() -> None:
    brandings = load_brandings()
    wonder = next(row for row in brandings if row.branding_id == "wonder_feed")
    pin = wonder.platform("pinterest")
    facebook = wonder.platform("facebook")
    assert pin is not None and pin.active is False
    assert facebook is not None and facebook.active is False
    assert pin.token_env == "PINTEREST_ACCESS_TOKEN_WONDER_FEED"
    assert pin.account_id == "wonder_feed"
    active_pins = active_accounts(brandings, platform="pinterest")
    assert not any(account.branding == "wonder_feed" for account in active_pins)


def test_each_branding_declares_the_same_platforms() -> None:
    brandings = load_brandings()
    expected = {"pinterest", "youtube", "facebook"}
    assert len(brandings) >= 2
    for row in brandings:
        assert set(row.platforms) == expected


def test_missing_and_invalid_brandings_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(BrandingConfigError, match="not found"):
        load_brandings(missing)

    broken = tmp_path / "broken.yaml"
    broken.write_text("brandings: [\n", encoding="utf-8")
    with pytest.raises(BrandingConfigError, match="Invalid YAML"):
        load_brandings(broken)

    empty = tmp_path / "empty.yaml"
    empty.write_text("brandings: {}\n", encoding="utf-8")
    with pytest.raises(BrandingConfigError, match="no brandings"):
        load_brandings(empty)


def test_account_tokens_stay_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE", "token-ancient")
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN_ANNA_PROTOCOL", "token-anna")
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN", "token-legacy")
    pins = active_accounts(platform="pinterest")
    resolved = {account.account_id: resolve_pinterest_token(account) for account in pins}
    assert resolved == {
        "ancient_knowledge": "token-ancient",
        "anna_protocol": "token-anna",
    }


def test_legacy_token_used_when_account_env_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE", raising=False)
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN", "token-legacy")
    account = Account(
        branding="ancient_knowledge",
        platform="pinterest",
        account_id="ancient_knowledge",
        token_env="PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE",
        extra={"legacy_token_env": "PINTEREST_ACCESS_TOKEN"},
    )
    assert resolve_pinterest_token(account) == "token-legacy"


def test_channel_dotenv_stays_unread_while_dotenv_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A live channels_config/<brand>/.env must not beat the legacy test token."""
    root = tmp_path / "repo"
    channel = root / "channels_config" / "ancient_knowledge"
    channel.mkdir(parents=True)
    (channel / ".env").write_text("PINTEREST_ACCESS_TOKEN=from-channel-file\n", encoding="utf-8")
    monkeypatch.setattr("accounts.tokens.repo_root", lambda: root)
    monkeypatch.setenv("OMNI_SKIP_DOTENV", "1")
    monkeypatch.delenv("PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE", raising=False)
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN", "token-legacy")
    account = Account(
        branding="ancient_knowledge",
        platform="pinterest",
        account_id="ancient_knowledge",
        token_env="PINTEREST_ACCESS_TOKEN_ANCIENT_KNOWLEDGE",
        extra={"legacy_token_env": "PINTEREST_ACCESS_TOKEN"},
    )
    assert resolve_pinterest_token(account) == "token-legacy"
    monkeypatch.delenv("OMNI_SKIP_DOTENV", raising=False)
    assert resolve_pinterest_token(account) == "from-channel-file"


def test_rotated_token_is_written_to_the_account_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "PINTEREST_ACCESS_TOKEN=old\n"
        "PINTEREST_ACCESS_TOKEN_ANNA_PROTOCOL=old-anna\n"
        "OTHER=keep\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PINTEREST_ACCESS_TOKEN", "new-anna")
    monkeypatch.setenv("_OMNI_APPLIED_PINTEREST_TOKEN", "old-anna")
    monkeypatch.setenv("PINTEREST_REFRESH_TOKEN", "refresh-2")
    monkeypatch.setenv("_OMNI_APPLIED_PINTEREST_REFRESH", "refresh-1")
    account = Account(
        branding="anna_protocol",
        platform="pinterest",
        account_id="anna_protocol",
        token_env="PINTEREST_ACCESS_TOKEN_ANNA_PROTOCOL",
        refresh_token_env="PINTEREST_REFRESH_TOKEN_ANNA_PROTOCOL",
    )
    remember_rotated_token(account, env_path)
    text = env_path.read_text(encoding="utf-8")
    assert "PINTEREST_ACCESS_TOKEN=old\n" in text
    assert "PINTEREST_ACCESS_TOKEN_ANNA_PROTOCOL=new-anna\n" in text
    assert "PINTEREST_REFRESH_TOKEN_ANNA_PROTOCOL=refresh-2\n" in text
    assert "OTHER=keep\n" in text


def test_drip_interval_stays_inside_three_to_six_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIN_INTERVAL_HOURS", "3")
    monkeypatch.setenv("MAX_INTERVAL_HOURS", "6")
    rng = random.Random(7)
    for _ in range(30):
        seconds = interval_seconds(rng)
        assert 3 * 3600 <= seconds <= 6 * 3600


def test_pinterest_cli_still_accepts_channel(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["OUTPUT_PATH"] = str(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            "tools/pinterest_cli.py",
            "schedule",
            "--help",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--channel" in proc.stdout


def test_scheduler_cycle_publishes_only_active_pinterest(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def _fake_drip(account: Account, *, pins: int | None = None, dry_run: bool = False) -> dict:
        seen.append(account.branding)
        assert account.active is True
        assert pins is None
        assert dry_run is False
        return {"published": 0, "errors": 0}

    monkeypatch.setattr("distribution.workers.runtime.run_pinterest_drip", _fake_drip)
    results = run_cycle()
    assert seen == ["ancient_knowledge", "anna_protocol"]
    assert [row["branding"] for row in results] == seen
