# -*- coding: utf-8 -*-
"""Refuse ffmpeg commands that can run without an end.

The guard reads a command before it starts:

* ``amix`` must end on its first or shortest input. Its default,
  ``longest``, waits for the longest stream, so a bare ``amix`` is refused.
* ``apad`` must carry ``whole_dur``, ``whole_len``, ``pad_dur`` or ``pad_len``,
  unless the output itself is cut with ``-t``, ``-to`` or ``-frames``.
* A ``lavfi`` generator needs ``d=``/``duration=`` or a ``-t``/``-frames``
  limit, and so does an input repeated with ``-stream_loop -1`` or ``-loop 1``.

:func:`run_bounded` then runs the command through ``shared.ffmpeg.run_ffmpeg``,
which kills the whole process group when the timeout hits.
"""
from __future__ import annotations

import re
import subprocess

from shared.ffmpeg import resolve_ffmpeg, run_ffmpeg

_FILTER = re.compile(r"\b(amix|apad)\b(?:=([^,;\[\]\s'\"]*))?", re.IGNORECASE)
_AMIX_POSITIONAL = ("inputs", "duration", "dropout_transition", "weights", "normalize")
_APAD_POSITIONAL = ("packet_size", "pad_len", "whole_len", "pad_dur", "whole_dur")
_APAD_BOUNDS = frozenset({"pad_len", "whole_len", "pad_dur", "whole_dur"})
_GENERATOR_DURATION = re.compile(r"(?:^|[:=,])(?:d|duration)=\S", re.IGNORECASE)
_LIMITS = frozenset({"-t", "-to", "-frames", "-frames:v", "-frames:a", "-vframes", "-aframes", "-fs"})


class UnboundedFfmpeg(ValueError):
    """The command would not be allowed to start."""


def assert_bounded_ffmpeg(cmd: list[str]) -> None:
    """Raise :class:`UnboundedFfmpeg` when a filter or an input can run forever."""
    tokens = [str(token) for token in cmd]
    inputs, output_options = _split_inputs(tokens)
    output_limited = any(token in _LIMITS for token in output_options)
    for match in _FILTER.finditer(" ".join(tokens)):
        name = match.group(1).lower()
        options = _options(match.group(2) or "", _AMIX_POSITIONAL if name == "amix" else _APAD_POSITIONAL)
        if name == "amix" and options.get("duration", "longest").lower() not in {"first", "shortest"}:
            raise UnboundedFfmpeg(
                f"amix must set duration=first or duration=shortest: {match.group(0)}"
            )
        if name == "apad" and not (_APAD_BOUNDS & options.keys()) and not output_limited:
            raise UnboundedFfmpeg(f"apad needs whole_dur, whole_len, pad_dur, or pad_len: {match.group(0)}")
    for options, source in inputs:
        limited = output_limited or any(token in _LIMITS for token in options)
        if _flag_value(options, "-f") == "lavfi" and not _GENERATOR_DURATION.search(source) and not limited:
            raise UnboundedFfmpeg(f"lavfi source has no duration and no -t/-frames limit: {source}")
        looped = _flag_value(options, "-stream_loop") or ""
        image_loop = _flag_value(options, "-loop") or "0"
        if (looped.startswith("-") or image_loop not in {"", "0"}) and not limited:
            raise UnboundedFfmpeg(f"looped input has no -t/-frames limit: {source}")


def run_bounded(
    cmd: list[str],
    *,
    timeout: float,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run one ffmpeg or ffprobe command that is both finite and timed."""
    if timeout <= 0:
        raise ValueError("ffmpeg timeout must be positive")
    assert_bounded_ffmpeg(cmd)
    return run_ffmpeg(cmd, timeout=timeout, check=check)


def ffmpeg_binary() -> str:
    return resolve_ffmpeg()


def _split_inputs(tokens: list[str]) -> tuple[list[tuple[list[str], str]], list[str]]:
    """``(options, source)`` per ``-i``, then the options after the last input."""
    inputs: list[tuple[list[str], str]] = []
    pending: list[str] = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-i" and index + 1 < len(tokens):
            inputs.append((pending, tokens[index + 1]))
            pending = []
            index += 2
            continue
        pending.append(token)
        index += 1
    return inputs, pending


def _flag_value(options: list[str], flag: str) -> str | None:
    value = None
    for index, token in enumerate(options[:-1]):
        if token == flag:
            value = options[index + 1]
    return value


def _options(raw: str, positional: tuple[str, ...]) -> dict[str, str]:
    options: dict[str, str] = {}
    for index, part in enumerate(item for item in raw.split(":") if item):
        key, sep, value = part.partition("=")
        if sep:
            options[key.lower()] = value
        elif index < len(positional):
            options[positional[index]] = part
    return options


__all__ = ["UnboundedFfmpeg", "assert_bounded_ffmpeg", "ffmpeg_binary", "run_bounded"]
