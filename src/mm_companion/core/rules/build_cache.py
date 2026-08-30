"""A scope in which the build math is worked out once and reused.

:func:`~.derived.trait_contributions` gathers *everything* standing on a character —
its size, every power in the tree, the advantages (bought and granted), every worn
item, and whatever Extra Effort is pushing — and it sits underneath every derived
number the sheet prints. Nothing above it holds the answer, so each readout gathers
the whole build again:

* ``effective_ability`` is one gather, ``skill_bonus`` another;
* ``skill_total`` is therefore two, and ``skill_modifiers`` a third;
* ``resistance_total`` is two, and Dodge derives from Defence, so that pair is four.

The Skills block asks for three of those per row. On a sheet with forty rows that is
a hundred and sixty gathers of the whole build — for **one step of an ability spin
box**, before the Resistances block, the Power Level caps and every power and
equipment card ask for their own. That is the lag, and it is quadratic in the size of
the character: the bigger the build, the more numbers it prints *and* the more each
one costs.

The fix is not to make the gather cheaper but to stop repeating it. A refresh pass is
a *read* — the sheet is redrawing itself from a model nobody is touching — so within
one pass the answer cannot change, and computing it twice is waste by construction.

**A scope, not a cache.** Outside :func:`stable_build` nothing is memoized at all and
every call behaves exactly as it always did, so there is no invalidation to get right
and no stale value to reason about: a caller that has not promised to hold the model
still gets a fresh answer every time. The promise is the whole contract, and it is one
line — **do not mutate the character inside a scope**. Whoever opens one is saying the
model is stable for its duration.

Today the scopes are opened by :class:`~mm_companion.ui.blocks.bus.SignalBus`, around
each subscriber individually rather than around a whole fan-out: a handler that *does*
write the model then cannot hand a stale answer to the next one. The one that does —
``PowersSection._rebuild_list`` normalizes its arrays before drawing — calls
:func:`invalidate_build_cache` immediately after writing, which drops the memo without
leaving the scope.

The memoized values are shared, not copied, so a caller must treat what it gets back
as read-only. Every caller already does; a future one that wants to mutate should copy
first, exactly as it would with any other shared structure.

Thread-local throughout: the session layer runs reader threads, and a scope opened on
the Qt thread must not leak into one of them.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any, TypeVar

_state = threading.local()

T = TypeVar("T")


@contextmanager
def stable_build() -> Iterator[None]:
    """Promise the character will not change, so the build math may be reused.

    Re-entrant: an inner scope joins the outer one rather than starting its own, and
    only the outermost drops the memo on the way out. That matters because the scopes
    nest in practice — a bus subscriber inside a scope may itself call something that
    opens one.
    """

    depth = getattr(_state, "depth", 0)
    if depth == 0:
        _state.memos = {}
    _state.depth = depth + 1
    try:
        yield
    finally:
        _state.depth = depth
        if depth == 0:
            _state.memos = None


def invalidate_build_cache() -> None:
    """Forget everything memoized so far, without leaving the scope.

    For the one caller that writes the model in the middle of a pass. A no-op outside
    a scope, where nothing was remembered in the first place.
    """

    memos = getattr(_state, "memos", None)
    if memos is not None:
        memos.clear()


def build_scoped(func: Callable[..., T]) -> Callable[..., T]:
    """Memoize ``func(char, game_data)`` for the duration of a :func:`stable_build`.

    Only the two-argument call is memoized. A call carrying anything further is asking
    a narrower question that the key does not describe, so it is simply passed through
    — which is what keeps the key honest rather than approximately right.

    The character and the game data are held in the memo alongside the value. That is
    deliberate: the key is built from their identities, and an object that had been
    freed could otherwise let a new one land on the same address inside a live scope
    and answer with its predecessor's numbers.
    """

    @wraps(func)
    def wrapper(char: Any, game_data: Any, *args: Any, **kwargs: Any) -> T:
        memos = getattr(_state, "memos", None)
        if memos is None or args or kwargs:
            return func(char, game_data, *args, **kwargs)
        key = (func.__qualname__, id(char), id(game_data))
        hit = memos.get(key)
        if hit is not None:
            return hit[2]
        value = func(char, game_data)
        memos[key] = (char, game_data, value)
        return value

    return wrapper
