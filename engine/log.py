# -*- coding: utf-8 -*-
"""Logger proxy so `python main.py` still logs as `__main__`."""
from __future__ import annotations

import logging
from typing import Any


class _LoggerProxy:
    def __init__(self) -> None:
        self._logger = logging.getLogger("main")

    def bind(self, logger: logging.Logger) -> None:
        self._logger = logger

    def __getattr__(self, name: str) -> Any:
        return getattr(self._logger, name)


_LOG = _LoggerProxy()


def bind_logger(logger: logging.Logger) -> None:
    _LOG.bind(logger)
