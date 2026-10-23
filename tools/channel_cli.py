# -*- coding: utf-8 -*-
"""One operator CLI. The channel is a parameter.

    python tools/channel_cli.py --channel endless_summer_paradise scan
    python tools/channel_cli.py --channel principles_of_wealth_finance_economics status
    python tools/channel_cli.py --channel anna_protocol schedule --help

A channel folder that contains ``cli.py`` owns its commands. A folder without
one uses the Pinterest CLI, which already takes ``--channel``. Adding a channel
is adding a folder; this module has no channel registry.

The old ``*_main.py`` paths stay as shims until M14.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _exit_code(exc: SystemExit) -> int:
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


def _call(handler, argv: list[str]) -> int:
    try:
        result = handler(argv)
    except SystemExit as exc:
        return _exit_code(exc)
    if result is None:
        return 0
    return int(result)


def _pinterest(channel: str, rest: list[str]) -> int:
    from tools.pinterest_cli import main as pin_main

    forwarded = list(rest)
    if "--channel" not in forwarded:
        forwarded.extend(["--channel", channel])
    return _call(pin_main, forwarded)


def _channel_handler(channel: str):
    folder = _ROOT / "channels_config" / channel
    if (folder / "cli.py").is_file():
        module = importlib.import_module(f"channels_config.{channel}.cli")
        return module.main, False
    return None, True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args in (["--help"], ["-h"]):
        parser = argparse.ArgumentParser(
            prog="channel_cli",
            description=(
                "Run a channel CLI. Pass --channel. "
                "Folders with cli.py use that file; other folders use the Pinterest CLI."
            ),
        )
        parser.add_argument(
            "--channel",
            required=True,
            help="Channel id. The folder channels_config/<channel>/ supplies the commands.",
        )
        parser.print_help()
        return 0

    channel: str | None = None
    rest: list[str]
    if args[0] == "--channel":
        if len(args) < 2 or not args[1] or args[1].startswith("-"):
            print("channel_cli: --channel needs a channel id", file=sys.stderr)
            return 2
        channel = args[1]
        rest = args[2:]
    elif not args[0].startswith("-"):
        channel = args[0]
        rest = args[1:]
    else:
        print("channel_cli: pass --channel <id> before the command", file=sys.stderr)
        return 2

    handler, use_pinterest = _channel_handler(channel)
    if use_pinterest:
        return _pinterest(channel, rest)
    return _call(handler, rest)


if __name__ == "__main__":
    raise SystemExit(main())
