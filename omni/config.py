"""Load ``config/brandings.yaml`` (N brandings x N platforms)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from omni.models import Account

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRANDINGS_PATH = _REPO_ROOT / "config" / "brandings.yaml"

_PINTEREST_LEGACY = {
    "legacy_token_env": "PINTEREST_ACCESS_TOKEN",
    "legacy_board_id_env": "PINTEREST_BOARD_ID",
    "legacy_board_name_env": "PINTEREST_BOARD_NAME",
}


class BrandingConfigError(ValueError):
    """``brandings.yaml`` is missing or does not match the expected shape."""


@dataclass
class PlatformSpec:
    branding: str
    display_name: str
    platform: str
    active: bool
    account_id: str
    token_env: str = ""
    board_id_env: str = ""
    board_name: str = ""
    board_name_env: str = ""
    refresh_token_env: str = ""
    legacy_token_env: str = "PINTEREST_ACCESS_TOKEN"
    legacy_board_id_env: str = "PINTEREST_BOARD_ID"
    legacy_board_name_env: str = "PINTEREST_BOARD_NAME"
    token_file: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def to_account(self) -> Account:
        return Account(
            branding=self.branding,
            platform=self.platform,
            account_id=self.account_id,
            active=self.active,
            token_env=self.token_env,
            board_id_env=self.board_id_env,
            board_name=self.board_name,
            board_name_env=self.board_name_env,
            refresh_token_env=self.refresh_token_env,
            token_file=self.token_file,
            extra={
                "legacy_token_env": self.legacy_token_env,
                "legacy_board_id_env": self.legacy_board_id_env,
                "legacy_board_name_env": self.legacy_board_name_env,
                "display_name": self.display_name,
                **self.extra,
            },
        )


@dataclass
class Branding:
    branding_id: str
    display_name: str
    platforms: dict[str, PlatformSpec]

    def platform(self, name: str) -> PlatformSpec | None:
        return self.platforms.get(name)


def repo_root() -> Path:
    return _REPO_ROOT


def _slug_env(prefix: str, branding_id: str) -> str:
    return f"{prefix}_{branding_id.strip().upper()}"


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _apply_platform_defaults(
    branding_id: str, platform: str, raw: dict[str, Any]
) -> dict[str, Any]:
    data = dict(raw)
    data.setdefault("account_id", branding_id)
    if platform == "pinterest":
        data.setdefault("token_env", _slug_env("PINTEREST_ACCESS_TOKEN", branding_id))
        data.setdefault("board_id_env", _slug_env("PINTEREST_BOARD_ID", branding_id))
        data.setdefault("board_name_env", _slug_env("PINTEREST_BOARD_NAME", branding_id))
        data.setdefault("refresh_token_env", _slug_env("PINTEREST_REFRESH_TOKEN", branding_id))
        for key, value in _PINTEREST_LEGACY.items():
            data.setdefault(key, value)
    elif platform == "youtube":
        data.setdefault(
            "token_file",
            f"credentials/tokens/youtube_token_{branding_id}.json",
        )
    return data


def _parse_platform(
    branding_id: str,
    display_name: str,
    platform: str,
    raw: Any,
) -> PlatformSpec:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise BrandingConfigError(
            f"{branding_id}.{platform} must be a mapping, got {type(raw).__name__}"
        )
    data = _apply_platform_defaults(branding_id, platform, raw)
    account_id = str(data.get("account_id") or branding_id).strip()
    if not account_id:
        raise BrandingConfigError(f"{branding_id}.{platform} is missing account_id")
    known = {
        "account_id",
        "active",
        "token_env",
        "board_id_env",
        "board_name",
        "board_name_env",
        "refresh_token_env",
        "legacy_token_env",
        "legacy_board_id_env",
        "legacy_board_name_env",
        "token_file",
    }
    extra = {
        str(key): str(value)
        for key, value in data.items()
        if key not in known and value is not None
    }
    return PlatformSpec(
        branding=branding_id,
        display_name=display_name,
        platform=platform,
        active=_as_bool(data.get("active"), default=False),
        account_id=account_id,
        token_env=str(data.get("token_env") or ""),
        board_id_env=str(data.get("board_id_env") or ""),
        board_name=str(data.get("board_name") or ""),
        board_name_env=str(data.get("board_name_env") or ""),
        refresh_token_env=str(data.get("refresh_token_env") or ""),
        legacy_token_env=str(data.get("legacy_token_env") or "PINTEREST_ACCESS_TOKEN"),
        legacy_board_id_env=str(data.get("legacy_board_id_env") or "PINTEREST_BOARD_ID"),
        legacy_board_name_env=str(
            data.get("legacy_board_name_env") or "PINTEREST_BOARD_NAME"
        ),
        token_file=str(data.get("token_file") or ""),
        extra=extra,
    )


def load_brandings(path: Path | None = None) -> list[Branding]:
    """Read brandings.yaml. Publish targets are the rows with ``active: true``."""
    source = Path(path) if path is not None else DEFAULT_BRANDINGS_PATH
    if not source.is_file():
        raise BrandingConfigError(f"Brandings file not found: {source}")
    try:
        loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise BrandingConfigError(f"Invalid YAML in {source}: {exc}") from exc
    if not isinstance(loaded, dict) or not isinstance(loaded.get("brandings"), dict):
        raise BrandingConfigError(f"{source} must contain a 'brandings' mapping")

    brandings: list[Branding] = []
    for branding_id, body in loaded["brandings"].items():
        slug = str(branding_id).strip()
        if not slug:
            raise BrandingConfigError("branding id must be a non-empty string")
        if not isinstance(body, dict):
            raise BrandingConfigError(f"{slug} must be a mapping")
        display_name = str(body.get("display_name") or slug).strip()
        platforms_raw = body.get("platforms") or {}
        if not isinstance(platforms_raw, dict) or not platforms_raw:
            raise BrandingConfigError(f"{slug} needs a non-empty platforms mapping")
        platforms = {
            str(name): _parse_platform(slug, display_name, str(name), spec)
            for name, spec in platforms_raw.items()
        }
        brandings.append(
            Branding(branding_id=slug, display_name=display_name, platforms=platforms)
        )
    if not brandings:
        raise BrandingConfigError(f"{source} has no brandings")
    return brandings


def active_accounts(
    brandings: list[Branding] | None = None,
    *,
    platform: str | None = None,
    path: Path | None = None,
) -> list[Account]:
    """Accounts whose ``active`` flag is true. Inactive rows are omitted."""
    rows = brandings if brandings is not None else load_brandings(path)
    selected: list[Account] = []
    for branding in rows:
        for spec in branding.platforms.values():
            if not spec.active:
                continue
            if platform is not None and spec.platform != platform:
                continue
            selected.append(spec.to_account())
    return selected
