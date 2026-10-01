"""The dots an advantage spent at the table wears — Luck, Determination, Edit Scene.

A few advantages are not standing bonuses but a **resource**: once per adventure per
rank (``usesPerAdventurePerRank`` in ``advantages.json``, read by
:func:`~mm_companion.core.rules.advantage_uses`). The sheet draws one dot per use — a
filled dot is a use still in hand, a hollow one is spent — and a click spends or
regains one, exactly as a hero-point pip does. Both the edit sheet's Advantages block
and the simple sheet draw the same widget, and both route the click through the block
(:meth:`~mm_companion.ui.sections.advantages.AdvantagesSection.set_advantage_used`), so
the history line is written in one place whichever of them was clicked.

The count is :attr:`~mm_companion.core.character.AdvantageSelection.used`, not which
dots are lit: unlike the hero-point row nobody needs to light the fourth dot on its
own, so spending always puts out the right-most lit one and regaining lights the
left-most hollow one.

Like the hero points the dots stay live on a **locked** sheet — a use is spent in
play, not built — so this widget has no locked state.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QWidget

from mm_companion.ui import theme
from mm_companion.ui.widgets import discard_widget


def advantage_use_note(name: str, left: int, total: int, *, spent: bool) -> str:
    """The history line for a use of *name* spent or regained.

    The count rides along for the reason :func:`~.sections.system_info.hero_point_note`
    gives: "used Luck" alone sends the table back to the sheet to count dots.
    """
    if spent:
        return f"used {name} — {left} of {total} left"
    return f"regained a use of {name} — {left} of {total} left"


class UsePips(QWidget):
    """A row of dots, one per use per adventure; filled while the use is unspent.

    Emits :attr:`usedChanged` with the new *used* count on a click, and
    :attr:`resetRequested` from the right-click menu. Neither writes the model — the
    owner does, so the note and the undo step have one author.

    ``interactive=False`` draws the same dots with no cursor, click or menu: a printed
    sheet shows how many uses are left but is not a control.
    """

    usedChanged = Signal(int)
    resetRequested = Signal()

    def __init__(
        self,
        total: int,
        used: int,
        parent: QWidget | None = None,
        *,
        interactive: bool = True,
        name: str = "",
    ) -> None:
        super().__init__(parent)
        self._interactive = interactive
        self._name = name
        self._total = 0
        self._used = 0
        self._buttons: list[QPushButton] = []
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(int(theme.metric("space.xs")))
        if interactive:
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.customContextMenuRequested.connect(self._show_menu)
        self.set_state(total, used)

    # -- state ---------------------------------------------------------------

    def total(self) -> int:
        return self._total

    def used(self) -> int:
        return self._used

    def left(self) -> int:
        return max(0, self._total - self._used)

    def set_state(self, total: int, used: int) -> None:
        """Draw *total* dots with *used* of them spent, without emitting anything."""
        total = max(0, int(total))
        self._used = max(0, int(used))
        if total != self._total:
            self._total = total
            while len(self._buttons) > total:
                button = self._buttons.pop()
                self._row.removeWidget(button)
                discard_widget(button)
            while len(self._buttons) < total:
                self._buttons.append(self._make_dot(len(self._buttons)))
        self._render()

    def _make_dot(self, index: int) -> QPushButton:
        size = int(theme.metric("column.use-pip"))
        button = QPushButton(self)
        button.setFlat(True)
        button.setFixedSize(size, size)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if self._interactive:
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, i=index: self._on_click(i))
        else:
            button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._row.addWidget(button)
        return button

    def _render(self) -> None:
        size = int(theme.metric("column.use-pip"))
        width = int(theme.metric("border.width.emphasis"))
        filled = theme.color("accent")
        hollow = theme.color("text.muted")
        left = self.left()
        for index, button in enumerate(self._buttons):
            lit = index < left
            fill = filled if lit else "transparent"
            ring = filled if lit else hollow
            wash = theme.wash("accent", 0.45 if lit else 0.25)
            hover = f"QPushButton:hover {{ background: {wash}; }}" if self._interactive else ""
            button.setStyleSheet(
                f"QPushButton {{ background: {fill}; border: {width}px solid {ring};"
                f" border-radius: {size // 2}px; padding: 0; }}" + hover
            )
            button.setToolTip(self._tip(lit))

    def _tip(self, lit: bool) -> str:
        what = self._name or "this advantage"
        state = f"{self.left()} of {self._total} uses of {what} left this adventure"
        if not self._interactive:
            return state
        action = "Click to spend a use" if lit else "Click to take a use back"
        return f"{state}\n{action} · right-click to reset for a new adventure"

    # -- interaction ---------------------------------------------------------

    def _on_click(self, index: int) -> None:
        """A lit dot spends one use, a hollow one gives one back."""
        if index < self.left():
            used = self._used + 1
        else:
            used = max(0, self._used - 1)
        if used == self._used:
            return
        self._used = used
        self._render()
        self.usedChanged.emit(used)

    def _show_menu(self, pos) -> None:
        menu = QMenu(self)
        action = menu.addAction("Reset uses — new adventure", self.resetRequested.emit)
        action.setEnabled(self._used > 0)
        menu.exec(self.mapToGlobal(pos))
