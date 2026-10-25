# -*- coding: utf-8 -*-
"""Call across production and distribution without a static import.

``layer_wiring`` is the composition root (a root module, not a layer).
Import-linter does not follow it. Layers call :func:`port` or hold a
:class:`Symbol` / :class:`Port` and never import the other layer.
"""
from __future__ import annotations

import operator
from typing import Any


def port(module: str) -> Any:
    """Return the loaded module. The first call loads ``layer_wiring``."""
    import layer_wiring

    return layer_wiring.load(module)


def _attr(module: str, name: str) -> Any:
    """Look up ``name`` on ``module``.

    A child module is not an attribute until something imports it.
    ``from package import submodule`` loads that child; ``getattr`` does
    not. Fall back to importing ``module.name`` so a Symbol or Port of a
    package submodule behaves like that import.
    """
    loaded = port(module)
    try:
        return getattr(loaded, name)
    except AttributeError:
        pass
    try:
        return port(f"{module}.{name}")
    except ModuleNotFoundError:
        raise AttributeError(
            f"module {module!r} has no attribute {name!r}"
        ) from None


class Port:
    """Attribute access loads ``module`` at use time, not at import time."""

    def __init__(self, module: str) -> None:
        object.__setattr__(self, "_module", module)

    def __getattr__(self, name: str) -> Any:
        return _attr(object.__getattribute__(self, "_module"), name)

    def __repr__(self) -> str:
        return f"Port({object.__getattribute__(self, '_module')!r})"


class Symbol:
    """A function, class, constant, or child module looked up on each use.

    Monkeypatches of the real module attribute stay visible. A child module
    is imported when it is not already an attribute. ``except`` clauses
    still need a real class; :class:`shared.youtube_errors.YouTubeQuotaExceededError`
    is that class.
    """

    def __init__(self, module: str, name: str) -> None:
        object.__setattr__(self, "_module", module)
        object.__setattr__(self, "_name", name)

    def _target(self) -> Any:
        module = object.__getattribute__(self, "_module")
        name = object.__getattribute__(self, "_name")
        return _attr(module, name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._target()(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target(), name)

    def __eq__(self, other: object) -> bool:
        return self._target() == other

    def __hash__(self) -> int:
        return hash(self._target())

    def __bool__(self) -> bool:
        return bool(self._target())

    def __int__(self) -> int:
        return int(self._target())

    def __index__(self) -> int:
        return operator.index(self._target())

    def __str__(self) -> str:
        return str(self._target())

    def __format__(self, spec: str) -> str:
        return format(self._target(), spec)

    def __repr__(self) -> str:
        module = object.__getattribute__(self, "_module")
        name = object.__getattribute__(self, "_name")
        return f"Symbol({module!r}, {name!r})"

    def __iter__(self):  # noqa: ANN204
        return iter(self._target())

    def __len__(self) -> int:
        return len(self._target())

    def __getitem__(self, item: Any) -> Any:
        return self._target()[item]

    def __contains__(self, item: object) -> bool:
        return item in self._target()

