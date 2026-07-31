"""Turning a double-click on a stat line into a roll.

Every rollable line on the sheet is made of ordinary widgets — a name label, a
spin box, a "→ total" — laid out in a grid or a table, with nothing per-row to
hang a handler on. Rather than restructure four sections around that, this module
attaches an event filter to whichever widgets make up a line and calls back with
the line's :class:`~mm_companion.core.rules.RollSpec` when one is double-clicked.

Two rules the sections rely on:

* **A locked sheet is the play view.** Rolling is a mid-play action, so it works
  whether or not the sheet is locked — the same bargain a power's on/off switch
  strikes. What *does* depend on the lock is the spin box: unlocked, a
  double-click inside one selects the number for retyping, and stealing that
  would make editing hostile. So a spin box only rolls while the sheet is locked
  (:func:`attach_roll_click`'s ``enabled`` guard), and the labels around it always
  do.
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
ROLL_TOOLTIP = "Double-click to roll"

SpecFactory = Callable[[], "RollSpec | None"]
Guard = Callable[[], bool]


class _DoubleClickRoller(QObject):
    """Event filter turning a left double-click into a call to *factory*.

    Parented to the widget it watches, so it lives and dies with the row and no
    section has to keep a reference to it.
    """

    def __init__(
        self,
        factory: SpecFactory,
        sink: Callable[[RollSpec], None],
        guard: Guard | None,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._factory = factory
        self._sink = sink
        self._guard = guard

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() != QEvent.Type.MouseButtonDblClick:
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._guard is not None and not self._guard():
            return False
        spec = self._factory()
        if spec is None:
            return False
        self._sink(spec)
        # Swallowed: a double-click that rolled must not also select text or step a
        # spin box underneath it.
        return True


def attach_roll_click(
    widget: QWidget,
    factory: SpecFactory,
    sink: Callable[[RollSpec], None],
    *,
    enabled: Guard | None = None,
    tooltip: bool = True,
) -> None:
    """Make double-clicking *widget* roll whatever *factory* returns.

    *sink* receives the spec (a section's ``rollRequested.emit``). *enabled* is an
    optional guard consulted at click time — the spin boxes pass "only while
    locked". A *factory* returning ``None`` declines the click, which is how a row
    that isn't rollable right now stays silent.

    A :class:`QAbstractSpinBox` is watched through its internal line edit as well
    as itself: the text area is a child widget and receives the press first, so a
    filter on the spin box alone would never see it.
    """

    targets: list[QWidget] = [widget]
    if isinstance(widget, QAbstractSpinBox):
        line_edit = widget.lineEdit()
        if line_edit is not None:
            targets.append(line_edit)
    for target in targets:
        target.installEventFilter(_DoubleClickRoller(factory, sink, enabled, target))
    if tooltip and not widget.toolTip():
        widget.setToolTip(ROLL_TOOLTIP)
