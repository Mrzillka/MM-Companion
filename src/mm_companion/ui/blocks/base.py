"""The block descriptor and the contract every character-sheet block satisfies.

A :class:`BlockDescriptor` is the data-first description of one block: its key
(names it for the layout model and the size table), its dock title (shown in the
block frame's title bar), the *factory* that builds its widget, its size
constraints, and where it lands in the default arrangement. The character sheet
and the block canvas iterate a registry of these descriptors rather than
hardcoding the block set in three places, so a mod can add a block by registering
one more descriptor.

Every block widget already follows a uniform contract — construct with
``(data, character, parent=None)`` and expose ``set_locked(bool)`` — captured by
the :class:`Block` protocol. Cross-block reactivity flows over a topic signal bus
(:mod:`mm_companion.ui.blocks.bus`): a descriptor's ``publishes`` maps one of the
block's Qt signals to the topics it raises, and ``subscribes`` maps a topic to
the block method that recomputes on it; ``requests``/``serves`` are the same pair
for the bus's payload channel, where a block asks another to act on something
specific (roll this trait). The sheet wires the whole web from these tables, so a
mod block joins it without a :mod:`mm_companion.ui.character_sheet` edit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from PySide6.QtWidgets import QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.ui.block_sizes import BlockSize

# A block widget is built from the shared game data + character model. Sections
# accept an optional parent, but the sheet always constructs them parentless (the
# block frame reparents them), so the factory is called with just the two.
BlockFactory = Callable[[GameData, Character], QWidget]

#: Separates a multi-instance block's template key from its instance number
#: (``"notes#2"``). Chosen because no registered key contains it, so
#: :func:`instance_template` is exact rather than a guess.
INSTANCE_SEPARATOR = "#"


def instance_template(key: str) -> str:
    """The template key behind a block key: ``"notes#2" -> "notes"``.

    The single rule for it. Anything keyed by *kind* of block rather than by the
    particular one — ``block_sizes.json``, a preset's ``blocks`` overrides, the
    merge test — asks this, so an instance is sized and themed like its template
    and a per-instance key never has to appear in a config file.
    """
    return key.split(INSTANCE_SEPARATOR, 1)[0]


def instance_key(template: str, number: int) -> str:
    """The key of instance *number* of *template* (instance 1 is the bare key)."""
    return template if number <= 1 else f"{template}{INSTANCE_SEPARATOR}{number}"


@runtime_checkable
class Block(Protocol):
    """The minimal contract a sheet block widget satisfies.

    Sections are ``QGroupBox`` subclasses constructed as ``Section(data, character)``
    and toggled between edit and read-only view with :meth:`set_locked`. This
    protocol documents that shared shape (it is not enforced at construction).
    """

    def set_locked(self, locked: bool) -> None: ...


@dataclass(frozen=True)
class BlockDescriptor:
    """A data-first description of one character-sheet block.

    ``default_row``/``default_col`` place the block in the default arrangement:
    blocks sharing a ``default_row`` sit side by side in that row, ordered by
    ``default_col``; rows stack in ascending ``default_row`` order.

    ``default_pinned`` moves the block off the page entirely: it starts in the
    pinned strip (the region that does not scroll) rather than in a row, on its own
    line, ordered against the other pinned blocks by the same ``default_row`` /
    ``default_col`` fields. A block is in *either* the rows or the strip, never
    both — the arrangement model requires every block exactly once.

    ``publishes`` and ``subscribes`` describe the block's place on the topic
    signal bus (:mod:`mm_companion.ui.blocks.bus`). ``publishes`` maps the name of
    one of the block's Qt signals to the tuple of topics firing it raises;
    ``subscribes`` maps a topic to the name of the block method that recomputes
    when it fires. Both default empty (a purely presentational block).

    ``coalesces`` names the subscriber methods that may be run **once at the end of
    the turn** rather than once per publish — for a handler expensive enough that
    running it per spin-box step is what makes an edit feel slow. See
    :meth:`~mm_companion.ui.blocks.bus.SignalBus.subscribe` for what a block owes in
    exchange.

    ``instance_factory`` is what makes a block one the sheet may build *more than
    one of* — today only Notes. Supplying it makes the registered descriptor a
    **template**: further instances take a key of ``"<key>#<n>"``, are built by
    calling it with that key, and are held by the sheet rather than by the
    registry (which, being keyed by block key, can only hold one of anything).
    One field rather than a ``multi`` flag beside a lookup table, so a mod ships
    a multi-instance block with nothing to register but its descriptor.
    :func:`instance_template` is the one rule for reading a template key back out
    of an instance key, and every lookup keyed by block (the size table, a
    preset's ``blocks`` override) goes through it so an instance inherits the
    template's entry.

    ``requests`` and ``serves`` are the same pair for the bus's **payload**
    channel, where one block asks another to *do* something rather than announcing
    that something changed: ``requests`` maps a Qt signal carrying the payload
    (``rollRequested(object)``) to the request topics it raises, and ``serves`` maps
    a request topic to the block method that answers it, which takes the payload as
    its one argument.
    """

    key: str
    title: str
    factory: BlockFactory
    size: BlockSize = BlockSize()
    default_row: int = 0
    default_col: int = 0
    default_pinned: bool = False
    publishes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    subscribes: Mapping[str, str] = field(default_factory=dict)
    requests: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    serves: Mapping[str, str] = field(default_factory=dict)
    instance_factory: Callable[[str], BlockFactory] | None = None
    # Belongs beside ``subscribes``, and is last anyway: a dataclass's field order is
    # a public signature, and a mod that passes the bus tables positionally would have
    # had ``requests`` silently land in a field added between them. New fields go on
    # the end.
    coalesces: frozenset[str] = frozenset()

    @property
    def multi(self) -> bool:
        """Whether the sheet may build more than one of this block."""
        return self.instance_factory is not None
