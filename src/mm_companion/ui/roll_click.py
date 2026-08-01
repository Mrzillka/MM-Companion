"""Turning a click on a loose stat readout into a roll.

The stat blocks are tables now, and a table row answers ``cellClicked`` /
``cellDoubleClicked`` on its own. What is left for this module is the readouts that
are *not* in a table — the System block's Initiative — where there is no row to
hang a handler on and an event filter on the widget is the way in.

Three rules the sections rely on:

* **One click loads, two roll.** A single click puts the line in the roller's chip
  so the sliders and the DC can be set before anything is thrown; a double-click
  throws it. A double-click necessarily fires the single first, which is harmless:
  rolling loads the same spec anyway, so the pair is load → (load + roll) on one
  spec. Deferring the load by the double-click interval would only make a plain
  click feel a beat late.
* **A locked sheet is the play view.** Rolling is a mid-play action, so it works
  whether or not the sheet is locked — the same bargain a power's on/off switch
  strikes. What *does* depend on the lock is a spin box: unlocked, clicking inside
  one places the caret and double-clicking selects the number for retyping, and
  stealing either would make editing hostile. That is what
  :func:`attach_roll_click`'s ``enabled`` guard is for, and why the load path never
  swallows the event it acted on.
* **A spec is built at click time, not at wire time.** The callback is a factory
  because the number to roll changes constantly (an ability edit, a power toggled
  on, a condition applied) and a spec captured when the row was built would be
  stale by the time anyone rolled it.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractSpinBox, QWidget

from mm_companion.core.rules import RollSpec

#: What a rollable widget says for itself. Kept here so every line says it the same.
ROLL_TOOLTIP = "Click to load, double-click to roll"

SpecFactory = Callable[[], "RollSpec | None"]
Guard = Callable[[], bool]


class _RollClicker(QObject):
    """Event filter turning left clicks on a widget into calls to *factory*.

    A release calls *on_load*, a double-click *on_roll*. Parented to the widget it
    watches, so it lives and dies with the readout and no section has to keep a
    reference to it.
    """

    def __init__(
        self,
        factory: SpecFactory,
        on_roll: Callable[[RollSpec], None],
        on_load: Callable[[RollSpec], None] | None,
        guard: Guard | None,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._factory = factory
        self._on_roll = on_roll
        self._on_load = on_load
        self._guard = guard

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        kind = event.type()
        if kind not in (QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseButtonRelease):
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._guard is not None and not self._guard():
            return False

        if kind == QEvent.Type.MouseButtonRelease:
            if self._on_load is None:
                return False
            spec = self._factory()
            if spec is not None:
                self._on_load(spec)
            # Never swallowed. A plain click has other work to do underneath —
            # placing a caret, moving a selection — and loading a chip is not a
            # reason to take that away.
            return False

        spec = self._factory()
        if spec is None:
            return False
        self._on_roll(spec)
        # Swallowed: a double-click that rolled must not also select text or step a
        # spin box underneath it.
        return True


def attach_roll_click(
    widget: QWidget,
    factory: SpecFactory,
    sink: Callable[[RollSpec], None],
    *,
    load_sink: Callable[[RollSpec], None] | None = None,
    enabled: Guard | None = None,
    tooltip: bool = True,
) -> None:
    """Make clicking *widget* load whatever *factory* returns, and double-clicking roll it.

    *sink* receives the spec on a double-click (a section's ``rollRequested.emit``);
    *load_sink* receives it on a single click (``loadRequested.emit``), and omitting
    it leaves the widget double-click-only. *enabled* is an optional guard consulted
    at click time — a spin box passes "only while locked". A *factory* returning
    ``None`` declines the click, which is how a readout that isn't rollable right now
    stays silent.

    A :class:`QAbstractSpinBox` is watched through its internal line edit as well as
    itself: the text area is a child widget and receives the press first, so a filter
    on the spin box alone would never see it. Both targets share one filter object
    per target, and since a click reaches exactly one of them the load fires once.
    """

    targets: list[QWidget] = [widget]
    if isinstance(widget, QAbstractSpinBox):
        line_edit = widget.lineEdit()
        if line_edit is not None:
            targets.append(line_edit)
    for target in targets:
        target.installEventFilter(_RollClicker(factory, sink, load_sink, enabled, target))
    if tooltip and not widget.toolTip():
        widget.setToolTip(ROLL_TOOLTIP)
