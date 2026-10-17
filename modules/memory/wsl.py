# -*- coding: utf-8 -*-
"""Optional Windows-only launch of a local supermemory-server.

Off unless ``SUPERMEMORY_AUTOSTART_WSL`` is set. Other platforms never spawn
``wsl``.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time

from modules.memory.config import env_flag

_LOG = logging.getLogger("omni.memory")
_START_ATTEMPTED = False

_PROVIDER_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
)


def reset_start_attempt() -> None:
    """Test helper. Allows another launch check in the same process."""
    global _START_ATTEMPTED
    _START_ATTEMPTED = False


def _port_open(host: str = "127.0.0.1", port: int = 6767, *, timeout_s: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _load_environment() -> None:
    try:
        from channels_config.aiwake.settings import load_environment
    except ImportError:
        return
    try:
        load_environment()
    except Exception:  # noqa: BLE001 — missing dotenv must not abort a debate
        return


def ensure_supermemory_running(
    *,
    host: str = "127.0.0.1",
    port: int = 6767,
    wait_s: float = 8.0,
) -> bool:
    """Start the workstation server inside WSL when that option is on.

    Returns True when the TCP port is open. A process makes at most one
    launch attempt. Paths stay overridable for a machine that is not the
    default workstation.
    """
    global _START_ATTEMPTED

    if _port_open(host, port):
        return True
    if not env_flag("SUPERMEMORY_AUTOSTART_WSL"):
        return False
    if _START_ATTEMPTED:
        return False
    _START_ATTEMPTED = True

    if sys.platform != "win32":
        _LOG.warning("Supermemory autostart is Windows-only; the service stays offline")
        return False

    _load_environment()
    distro = (os.getenv("SUPERMEMORY_WSL_DISTRO") or "Ubuntu").strip()
    user = (os.getenv("SUPERMEMORY_WSL_USER") or "freedom_or_death").strip()
    server = (
        os.getenv("SUPERMEMORY_WSL_SERVER")
        or "/home/freedom_or_death/.supermemory/bin/supermemory-server"
    ).strip()
    command = ["wsl", "-d", distro, "-u", user, server]
    child_env = os.environ.copy()
    shared_keys = [name for name in _PROVIDER_KEYS if child_env.get(name)]
    if shared_keys:
        existing = [entry for entry in child_env.get("WSLENV", "").split(":") if entry]
        existing.extend(name for name in shared_keys if name not in existing)
        child_env["WSLENV"] = ":".join(existing)
    try:
        subprocess.Popen(  # noqa: S603 — fixed argv, optional workstation launch
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            close_fds=True,
            env=child_env,
        )
    except OSError as exc:
        _LOG.warning("could not auto-start Supermemory via WSL (%s)", exc)
        return False

    _LOG.info("Supermemory was offline; started it via WSL and am waiting for port %d", port)
    deadline = time.monotonic() + max(2.0, wait_s)
    while time.monotonic() < deadline:
        if _port_open(host, port):
            _LOG.info("Supermemory is accepting connections on %s:%d", host, port)
            return True
        time.sleep(0.25)
    _LOG.warning("Supermemory did not become ready within %.1fs", wait_s)
    return False
