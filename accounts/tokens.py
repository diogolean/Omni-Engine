"""Resolve per-branding Pinterest tokens without dropping the legacy single-token path."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path

from accounts.brandings import repo_root
from distribution.publishers.base import Account


def _read_env_file(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_UNSET = {"", "todo", "tbd", "unknown", "none"}


def is_unset(value: str | None) -> bool:
    """True for blank and for explicit TODO placeholders in account config."""
    return (value or "").strip().lower() in _UNSET


def channel_env_file(branding: str) -> Path | None:
    """Same candidate order as the Pinterest CLI, excluding the shared root ``.env``.

    ``channels_env/`` is the read-write mount of the host ``channels_config``
    tree, so a refreshed token written there survives the next container.
    """
    root = repo_root()
    slug = branding.strip()
    candidates = [
        root / "channels_env" / slug / ".env",
        root / "channels_env" / slug / f".env.{slug}",
        root / "channels_config" / slug / ".env",
        root / "channels_config" / slug / f".env.{slug}",
        root / f".env.{slug}",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _env_value(name: str) -> str:
    if not name:
        return ""
    return os.getenv(name, "").strip()


def _from_channel_file(account: Account, key: str) -> str:
    path = channel_env_file(account.branding)
    if path is None:
        return ""
    return _read_env_file(path).get(key, "").strip()


def resolve_pinterest_token(account: Account) -> str:
    dedicated = _env_value(account.token_env)
    if dedicated:
        return dedicated
    from_file = _from_channel_file(account, "PINTEREST_ACCESS_TOKEN")
    if from_file:
        return from_file
    return _env_value(account.extra.get("legacy_token_env", "PINTEREST_ACCESS_TOKEN"))


def resolve_pinterest_board_id(account: Account) -> str:
    literal = (account.extra.get("board_id") or "").strip()
    if literal and not is_unset(literal):
        return literal
    dedicated = _env_value(account.board_id_env)
    if dedicated and not is_unset(dedicated):
        return dedicated
    from_file = _from_channel_file(account, "PINTEREST_BOARD_ID")
    if from_file and not is_unset(from_file):
        return from_file
    legacy = _env_value(account.extra.get("legacy_board_id_env", "PINTEREST_BOARD_ID"))
    return "" if is_unset(legacy) else legacy


def resolve_pinterest_board_name(account: Account) -> str:
    dedicated = _env_value(account.board_name_env)
    if dedicated and not is_unset(dedicated):
        return dedicated
    if account.board_name.strip() and not is_unset(account.board_name):
        return account.board_name.strip()
    from_file = _from_channel_file(account, "PINTEREST_BOARD_NAME")
    if from_file and not is_unset(from_file):
        return from_file
    legacy = _env_value(account.extra.get("legacy_board_name_env", "PINTEREST_BOARD_NAME"))
    return "" if is_unset(legacy) else legacy


def resolve_pinterest_app_id(account: Account) -> str:
    dedicated = _env_value(account.extra.get("app_id_env", ""))
    if dedicated:
        return dedicated
    from_file = _from_channel_file(account, "PINTEREST_APP_ID")
    if from_file:
        return from_file
    return _env_value("PINTEREST_APP_ID")


def resolve_pinterest_app_secret(account: Account) -> str:
    dedicated = _env_value(account.extra.get("app_secret_env", ""))
    if dedicated:
        return dedicated
    from_file = _from_channel_file(account, "PINTEREST_APP_SECRET")
    if from_file:
        return from_file
    return _env_value("PINTEREST_APP_SECRET")


def resolve_pinterest_refresh_token(account: Account) -> str:
    dedicated = _env_value(account.refresh_token_env)
    if dedicated:
        return dedicated
    from_file = _from_channel_file(account, "PINTEREST_REFRESH_TOKEN")
    if from_file:
        return from_file
    return _env_value("PINTEREST_REFRESH_TOKEN")


def upsert_env_key(env_path: Path, key: str, value: str) -> None:
    """Update or append ``key=value`` in an env file. Other lines stay put."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    text = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    pattern = rf"^{re.escape(key)}\s*=.*$"
    new_line = f"{key}={value}"
    if re.search(pattern, text, flags=re.MULTILINE):
        text = re.sub(pattern, new_line, text, count=1, flags=re.MULTILINE)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += new_line + "\n"
    env_path.write_text(text, encoding="utf-8")
    os.environ[key] = value


def remember_rotated_token(account: Account, env_path: Path | None = None) -> None:
    """If the legacy publisher refreshed the access token, keep the suffixed key in sync.

    ``PinterestPublisher`` writes ``PINTEREST_ACCESS_TOKEN``. Multi-account runs
    read ``PINTEREST_ACCESS_TOKEN_<BRANDING>``, so a refresh has to land on that
    key too. The generic key is left as the publisher wrote it.
    """
    if not account.token_env:
        return
    current = os.getenv("PINTEREST_ACCESS_TOKEN", "").strip()
    applied = os.getenv("_OMNI_APPLIED_PINTEREST_TOKEN", "").strip()
    if not current or not applied or current == applied:
        return
    primary = env_path or (repo_root() / ".env")
    for path in _token_write_paths(primary):
        upsert_env_key(path, account.token_env, current)
        refresh = os.getenv("PINTEREST_REFRESH_TOKEN", "").strip()
        applied_refresh = os.getenv("_OMNI_APPLIED_PINTEREST_REFRESH", "").strip()
        if refresh and account.refresh_token_env and refresh != applied_refresh:
            upsert_env_key(path, account.refresh_token_env, refresh)


def _token_write_paths(primary: Path) -> list[Path]:
    """Channel env plus the root ``.env`` when the channel file is the mount.

    A container restart loads ``/opt/omni-engine/.env`` into the process, so a
    refresh that lands only on the channel file would be hidden by the stale
    suffixed key. Tests that pass a temp file outside ``channels_env`` do not
    touch the repository ``.env``.
    """
    paths = [primary]
    root = repo_root() / ".env"
    mount = repo_root() / "channels_env"
    try:
        primary.resolve().relative_to(mount.resolve())
        on_mount = True
    except ValueError:
        on_mount = False
    if on_mount and root.resolve() != primary.resolve():
        paths.append(root)
    return paths


@contextmanager
def pinterest_account_env(account: Account):
    """Point the legacy publisher at this account's token and board for the duration."""
    token = resolve_pinterest_token(account)
    board_id = resolve_pinterest_board_id(account)
    board_name = resolve_pinterest_board_name(account)
    refresh = resolve_pinterest_refresh_token(account)
    app_id = resolve_pinterest_app_id(account)
    app_secret = resolve_pinterest_app_secret(account)
    updates: dict[str, str] = {}
    if token:
        updates["PINTEREST_ACCESS_TOKEN"] = token
        updates["_OMNI_APPLIED_PINTEREST_TOKEN"] = token
    if board_id:
        updates["PINTEREST_BOARD_ID"] = board_id
    if board_name:
        updates["PINTEREST_BOARD_NAME"] = board_name
    if refresh:
        updates["PINTEREST_REFRESH_TOKEN"] = refresh
        updates["_OMNI_APPLIED_PINTEREST_REFRESH"] = refresh
    if app_id:
        updates["PINTEREST_APP_ID"] = app_id
    if app_secret:
        updates["PINTEREST_APP_SECRET"] = app_secret
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
