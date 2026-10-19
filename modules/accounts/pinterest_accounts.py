"""Load ``config/pinterest_accounts.yaml``.

Adding an account is a new block in that file plus env vars on the VM.
The scheduler does not need a code change to grow past ten accounts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from core.publishing.brandings import repo_root
from core.publishing.models import Account
from modules.accounts.tokens import is_unset

DEFAULT_ACCOUNTS_PATH = repo_root() / "config" / "pinterest_accounts.yaml"

_REQUIRED = (
    "channel_id",
    "access_token_env",
    "refresh_token_env",
    "app_id_env",
    "app_secret_env",
    "b2_prefix",
    "site_url",
    "niche",
)


class PinterestAccountsError(ValueError):
    """``pinterest_accounts.yaml`` is missing or does not match the expected shape."""


def load_pinterest_accounts(path: Path | None = None) -> list[Account]:
    """Every account block, including ones that are not ready to publish."""
    source = Path(path) if path is not None else DEFAULT_ACCOUNTS_PATH
    if not source.is_file():
        raise PinterestAccountsError(f"Pinterest accounts file not found: {source}")
    try:
        loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PinterestAccountsError(f"Invalid YAML in {source}: {exc}") from exc
    rows = loaded.get("accounts") if isinstance(loaded, dict) else None
    if not isinstance(rows, list) or not rows:
        raise PinterestAccountsError(f"{source} must contain a non-empty accounts list")

    accounts: list[Account] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise PinterestAccountsError(f"accounts[{index}] must be a mapping")
        account = _account_from_row(raw, index)
        if account.account_id in seen:
            raise PinterestAccountsError(f"duplicate channel_id: {account.account_id}")
        seen.add(account.account_id)
        accounts.append(account)
    return accounts


def active_pinterest_accounts(path: Path | None = None) -> list[Account]:
    """Accounts with ``active: true``. Not-ready rows are still returned.

    The drip skips a row whose site or board is still TODO. Keeping it in
    this list means the scheduler log shows why it was skipped.
    """
    return [account for account in load_pinterest_accounts(path) if account.active]


def account_blockers(account: Account) -> list[str]:
    """Names of fields that are still TODO. An empty list means the row can publish."""
    blocked: list[str] = []
    if is_unset(account.extra.get("site_url", "")):
        blocked.append("site_url")
    board_id = (account.extra.get("board_id") or "").strip()
    board_name = account.board_name.strip()
    board_env = (account.board_id_env or "").strip()
    # A board id env var may be filled on the VM even while the yaml name is TODO.
    from modules.accounts.tokens import (  # noqa: PLC0415
        resolve_pinterest_board_id,
        resolve_pinterest_board_name,
    )

    if is_unset(board_id) and is_unset(board_name) and not board_env:
        blocked.append("board")
    elif (
        is_unset(resolve_pinterest_board_id(account))
        and is_unset(resolve_pinterest_board_name(account))
    ):
        blocked.append("board")
    return blocked


def _as_bool(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _required_str(raw: dict[str, Any], key: str, index: int) -> str:
    if key not in raw or raw.get(key) is None or str(raw.get(key)).strip() == "":
        raise PinterestAccountsError(f"accounts[{index}] is missing {key}")
    return str(raw[key]).strip()


def _account_from_row(raw: dict[str, Any], index: int) -> Account:
    for key in _REQUIRED:
        _required_str(raw, key, index)
    channel_id = _required_str(raw, "channel_id", index)
    board_name = str(raw.get("board_name") or "").strip()
    board_id = str(raw.get("board_id") or "").strip()
    fallback_env = f"PINTEREST_BOARD_ID_{channel_id.upper()}"
    board_id_env = str(raw.get("board_id_env") or fallback_env).strip()
    named_env = str(raw.get("board_id_env") or "").strip()
    if is_unset(board_name) and is_unset(board_id) and not named_env:
        raise PinterestAccountsError(
            f"accounts[{index}] ({channel_id}) needs board_name, board_id, or board_id_env"
        )
    extra = {
        "app_id_env": _required_str(raw, "app_id_env", index),
        "app_secret_env": _required_str(raw, "app_secret_env", index),
        "site_url": _required_str(raw, "site_url", index),
        "niche": _required_str(raw, "niche", index),
        "b2_prefix": _normalize_prefix(_required_str(raw, "b2_prefix", index)),
        "b2_filename_pattern": str(raw.get("b2_filename_pattern") or "").strip(),
        "article_tls_verify": (
            "true" if _as_bool(raw.get("article_tls_verify"), default=True) else "false"
        ),
        "use_featured_image": (
            "true" if _as_bool(raw.get("use_featured_image"), default=True) else "false"
        ),
        "runner": _runner(raw.get("runner")),
        "facebook_postplanner": (
            "true" if _as_bool(raw.get("facebook_postplanner"), default=False) else "false"
        ),
        "facebook_page_name": str(
            raw.get("facebook_page_name") or raw.get("display_name") or channel_id
        ).strip(),
        "facebook_caption_template": str(raw.get("facebook_caption_template") or "").strip(),
        "drive_folder_id": str(raw.get("drive_folder_id") or "").strip(),
        "channel_id": channel_id,
        "display_name": str(raw.get("display_name") or channel_id).strip(),
        "legacy_token_env": "PINTEREST_ACCESS_TOKEN",
        "legacy_board_id_env": "PINTEREST_BOARD_ID",
        "legacy_board_name_env": "PINTEREST_BOARD_NAME",
    }
    if board_id:
        extra["board_id"] = board_id
    return Account(
        branding=channel_id,
        platform="pinterest",
        account_id=channel_id,
        active=_as_bool(raw.get("active"), default=True),
        token_env=_required_str(raw, "access_token_env", index),
        board_id_env=board_id_env,
        board_name=board_name,
        board_name_env=str(raw.get("board_name_env") or "").strip(),
        refresh_token_env=_required_str(raw, "refresh_token_env", index),
        extra=extra,
    )


def _runner(value: Any) -> str:
    text = str(value or "both").strip().lower()
    if text in {"local", "docker", "both", "off"}:
        return text
    raise PinterestAccountsError(
        f"runner must be local, docker, both, or off (got {value!r})"
    )


def _normalize_prefix(prefix: str) -> str:
    text = prefix.strip().lstrip("/")
    if text and not text.endswith("/"):
        text += "/"
    return text
