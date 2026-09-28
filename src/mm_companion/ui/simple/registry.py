"""Which blocks the simple sheet draws for itself, and which it borrows.

The simple sheet has two ways of showing a block, and the registry is where a block
says which it wants:

* **a view of its own** — a small widget built from the shared model (and the block's
  live section, for anything a player *does*), that shows only what a player reads at
  the table. Name & Details, the stat blocks, Skills, Advantages: nothing about them
  changes mid-play except through something else, so they are redrawn rather than
  borrowed. A view is a ``factory(context) -> widget`` whose widget exposes
  ``refresh()``;
* **the block's own section, borrowed** — for a block whose play-time controls are its
  whole point and too intricate to draw twice: the power cards (on/off, arrays, a
  Dynamic array's share dials, Extra Effort), the gear, the roller, the notes. The
  sheet lends the live widget out of its frame and takes it back afterwards; a section
  that has a quieter look for the simple sheet says so with ``set_simple(bool)``.

Anything not registered is borrowed as it is, which is what makes a mod's block work on
the simple sheet with nothing to do: it simply looks the way it does on the edit page.
A mod that wants a view of its own calls :func:`register_simple_view` — the same
extend-a-registry seam as ``register_block``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.registry import Registry
from mm_companion.core.rules import RollSpec


@dataclass
class SimpleContext:
    """What a simple view is built from.

    ``section`` is the block's live section on a sheet the player is using — the thing
    a view spends through, since a hero point, a condition or a roll has one funnel on
    the sheet and a second writer would be a second answer. It is ``None`` when the view
    is being built for **printing**, where nothing is clickable and there is nothing to
    spend through; a view must draw correctly without it.
    """

    data: GameData
    character: Character
    section: QObject | None = None
    #: A GM's NPC: no point budget, an estimated Power Level, no hero points.
    npc: bool = False
    #: Which block this view is of — ``"notes#2"`` for a second Notes block.
    key: str = ""

    @property
    def live(self) -> bool:
        """Whether the view is on a sheet someone is using (rather than on paper)."""
        return self.section is not None

    def _emit(self, signal_name: str, payload: object) -> bool:
        signal = getattr(self.section, signal_name, None) if self.section is not None else None
        if signal is None:
            return False
        signal.emit(payload)
        return True

    def roll(self, spec: RollSpec | None) -> None:
        """Throw *spec*, through the section's own roll signal (and so the sheet's bus)."""
        if spec is not None:
            self._emit("rollRequested", spec)

    def load(self, spec: RollSpec | None) -> None:
        """Put *spec* in the roller's chip without throwing it."""
        if spec is not None:
            self._emit("loadRequested", spec)

    def can_roll(self) -> bool:
        return self.section is not None and hasattr(self.section, "rollRequested")


ViewFactory = Callable[[SimpleContext], QWidget]


@dataclass(frozen=True)
class SimpleView:
    """How the simple sheet shows one kind of block.

    ``factory`` is ``None`` for a block that is *borrowed*. ``printable`` is whether it
    belongs on paper at all — a dice roller does not. ``heading`` is whether the box
    carries its block's name over it; Name & Details does not, since the name it holds
    is the whole sheet's heading.

    ``print_factory`` is a view used on paper *instead of* whatever the screen shows —
    for a borrowed block whose live widget does not print well (a note is an editor
    with its own scroll bar; on paper it is the text). Without one, a printed borrowed
    block is a freshly built copy of its section in its simple look.
    """

    key: str
    factory: ViewFactory | None = None
    printable: bool = True
    heading: bool = True
    print_factory: ViewFactory | None = None

    @property
    def borrowed(self) -> bool:
        return self.factory is None


SIMPLE_VIEWS: Registry[SimpleView] = Registry("simple views")


def register_simple_view(
    key: str,
    factory: ViewFactory | None = None,
    *,
    printable: bool = True,
    heading: bool = True,
    print_factory: ViewFactory | None = None,
    replace: bool = False,
) -> SimpleView:
    """Say how the simple sheet shows the block *key* (a template key for a multi block)."""
    view = SimpleView(
        key, factory, printable=printable, heading=heading, print_factory=print_factory
    )
    SIMPLE_VIEWS.register(key, view, replace=replace)
    return view


def simple_view(key: str) -> SimpleView:
    """The registered view for *key*, or the borrow-it-as-it-is default."""
    from mm_companion.ui.blocks.base import instance_template

    template = instance_template(key)
    if template in SIMPLE_VIEWS:
        registered = SIMPLE_VIEWS.get(template)
        return SimpleView(
            key,
            registered.factory,
            registered.printable,
            registered.heading,
            registered.print_factory,
        )
    return SimpleView(key)
