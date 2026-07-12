"""A generic string-keyed handler registry — the extension seam for pluggable mechanics.

The rules engine dispatches several behaviours through *fixed enum vocabularies*
(readout ``kind``, statIntegration ``pattern``, condition mechanisms, config-field
``type``). Each such vocabulary is backed by a :class:`Registry`: the base package
registers its built-in handlers at import time, and a mod's Python module can
:meth:`register` a new key without editing the engine. The subsystem that owns a
vocabulary looks a handler up by key and calls it — an unknown key resolves to
``None`` and the caller falls back to its no-op default (unchanged behaviour).

This module is pure Python and imports nothing from the app; a registry is a plain
in-process table, so registrations from a loaded mod persist for the process.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Generic, TypeVar

H = TypeVar("H")


class Registry(Generic[H]):
    """A named ``str -> handler`` table with register / lookup / iterate.

    ``name`` is only for error messages. Handlers are stored verbatim; the registry
    imposes no signature — each vocabulary documents what its handlers receive. A
    duplicate key raises unless ``replace=True``, so a base double-registration is a
    hard error while a mod deliberately overriding a base handler is explicit.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._handlers: dict[str, H] = {}

    @property
    def name(self) -> str:
        return self._name

    def register(self, key: str, handler: H, *, replace: bool = False) -> H:
        """Bind ``key`` to ``handler``; return the handler (usable as a decorator wrapper)."""
        if key in self._handlers and not replace:
            raise KeyError(
                f"{self._name!r} already has a handler for {key!r} "
                f"(pass replace=True to override it)"
            )
        self._handlers[key] = handler
        return handler

    def handler(self, key: str, *, replace: bool = False) -> Callable[[H], H]:
        """Decorator form of :meth:`register` — ``@reg.handler("foo")`` over a function."""

        def decorate(fn: H) -> H:
            self.register(key, fn, replace=replace)
            return fn

        return decorate

    def unregister(self, key: str) -> None:
        """Drop ``key`` if present (no error when it is absent)."""
        self._handlers.pop(key, None)

    def get(self, key: str) -> H | None:
        """The handler for ``key``, or ``None`` when nothing is registered."""
        return self._handlers.get(key)

    def __contains__(self, key: object) -> bool:
        return key in self._handlers

    def keys(self) -> tuple[str, ...]:
        """Every registered key, in insertion order."""
        return tuple(self._handlers)

    def __iter__(self) -> Iterator[str]:
        return iter(self._handlers)

    def __len__(self) -> int:
        return len(self._handlers)
