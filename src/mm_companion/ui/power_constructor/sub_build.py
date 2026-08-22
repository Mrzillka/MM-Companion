"""The effect card's row of nested characters (``docs/notes/powers.md``).

A Summon buys a minion and a Metamorph buys one alternate form per rank — whole
characters, built on a budget the power hands them
(:mod:`mm_companion.core.rules.subbuilds`). This is the strip of buttons that reaches
them: one per build, plus one more to start the next while the power still buys one.

**One panel for both levels.** A slot is declared either by the *effect* (Summon) or by
a *modifier attached to it* (Metamorph), and the panel shows every slot the card owns
without caring which — because the card is where both are edited, and a second strip
inside the chip would be a second place to look for the same thing.

The editor itself is :class:`~mm_companion.ui.sub_build_window.SubBuildWindow`, imported
**inside the handler**. At module scope it would close a loop: the constructor would
reach the main window, which reaches the sheet, which reaches the sections, which reach
back here. Deferred it is safe either way round — by the time a button is clicked this
package is fully imported, whether or not a sheet was ever opened. (Pass 10 hit the same
wall from the other side; see the roll-button note in ``docs/notes/powers.md``.)
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.library import display_name
from mm_companion.core.powers import PowerEffectInstance
from mm_companion.core.rules import (
    SubBuildSlot,
    effect_sub_build_slots,
    new_sub_build,
    power_points_spent,
    remove_sub_build,
    store_sub_build,
    sub_build_characters,
)
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout


class SubBuildPanel(QWidget):
    """Every sub-build slot one effect card owns, rebuilt from the live power."""

    #: A build was created, edited or removed — the card's cost and the constructor's
    #: warnings both re-read from the power.
    changed = Signal()

    def __init__(
        self,
        instance: PowerEffectInstance,
        game_data: GameData,
        character: Character | None = None,
    ) -> None:
        super().__init__()
        self.instance = instance
        self._data = game_data
        self._character = character
        # Open editors, kept referenced so they are not collected the moment the click
        # handler returns, and so a second click on the same build raises the one window
        # rather than opening a rival copy of the same character.
        self._windows: dict[tuple[str, int], QWidget] = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the strip from the effect's current slots.

        Called from the card's cost refresh, which is what every edit already reaches —
        a rank change moves a Summon's budget, and attaching or dropping a Metamorph
        chip adds or removes the slot outright.
        """

        while (item := self._layout.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.deleteLater()
        slots = effect_sub_build_slots(self.instance, self._data, self._character)
        for slot in slots:
            self._layout.addWidget(self._slot_row(slot))
        self.setVisible(bool(slots))

    # -- one slot ------------------------------------------------------------

    def _slot_row(self, slot: SubBuildSlot) -> QWidget:
        host = QWidget()
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)

        heading = QLabel(self._heading(slot))
        heading.setStyleSheet("font-weight: bold;")
        if slot.spec.hint:
            heading.setToolTip(slot.spec.hint)
        column.addWidget(heading)

        strip = FlowContainer()
        flow = FlowLayout(strip)
        builds = sub_build_characters(slot)
        for index, build in enumerate(builds):
            flow.addWidget(self._build_button(slot, index, build))
            flow.addWidget(self._remove_button(slot, index))
        next_index = len(builds)
        # A menu slot is never full: a Variable Type Summon is entitled to as many
        # minions as the player cares to build, since it still summons one at a time.
        if slot.menu or next_index < slot.count:
            first = "" if builds else f"+ Build {slot.label.lower()}…"
            add = QPushButton(first or f"+ Another {slot.label.lower()}…")
            add.setToolTip(slot.spec.hint or f"Start a new {slot.label.lower()}.")
            add.clicked.connect(lambda _=False, s=slot, i=next_index: self._create(s, i))
            flow.addWidget(add)
        column.addWidget(strip)
        return host

    def _heading(self, slot: SubBuildSlot) -> str:
        """ "Minion — 90 PP", or just the label when no budget can be worked out.

        A power built with no character open cannot know a Metamorph's budget (it is the
        wielder's own point total), and saying so is better than printing a zero.

        A **menu** slot counts what has been built rather than what is allowed, and says
        the rule instead of a number — "Minions (3), one at a time" — because there is no
        allowance to print: the player may make as many as they like and the power still
        summons one.
        """

        if slot.menu:
            built = len(sub_build_characters(slot))
            head = f"{slot.label}s ({built}), one at a time" if built else f"{slot.label}s"
            return head if slot.budget is None else f"{head} — {slot.budget} PP each"
        plural = f"{slot.label}s" if slot.count > 1 else slot.label
        if slot.budget is None:
            return f"{plural} ({slot.count})" if slot.count > 1 else plural
        each = " each" if slot.count > 1 else ""
        head = f"{plural} ({slot.count})" if slot.count > 1 else plural
        return f"{head} — {slot.budget} PP{each}"

    def _build_button(self, slot: SubBuildSlot, index: int, build: Character) -> QPushButton:
        spent = power_points_spent(build, self._data)
        name = display_name(build)
        over = slot.budget is not None and spent > slot.budget
        budget = "" if slot.budget is None else f" / {slot.budget}"
        button = QPushButton(f"{'⚠ ' if over else ''}{name} — {spent}{budget} PP")
        button.setToolTip(
            f"Open this {slot.label.lower()} in its own sheet."
            + (f"\nOver budget by {spent - slot.budget} PP." if over else "")
        )
        button.clicked.connect(lambda _=False, s=slot, i=index: self._open(s, i))
        return button

    def _remove_button(self, slot: SubBuildSlot, index: int) -> QToolButton:
        button = QToolButton()
        button.setText("×")
        button.setToolTip(f"Delete this {slot.label.lower()}.")
        button.clicked.connect(lambda _=False, s=slot, i=index: self._remove(s, i))
        return button

    # -- actions -------------------------------------------------------------

    def _create(self, slot: SubBuildSlot, index: int) -> None:
        store_sub_build(slot, index, new_sub_build(slot, self._data))
        self.changed.emit()
        self.refresh()
        self._open(slot, index)

    def _remove(self, slot: SubBuildSlot, index: int) -> None:
        window = self._windows.pop((slot.key, index), None)
        if window is not None:
            window.close()
        remove_sub_build(slot, index)
        self.changed.emit()
        self.refresh()

    def _open(self, slot: SubBuildSlot, index: int) -> None:
        """Open (or raise) the sheet for one build, writing every edit back as it lands."""

        # See the module docstring — this import cannot be at module scope.
        from mm_companion.ui.sub_build_window import SubBuildWindow

        existing = self._windows.get((slot.key, index))
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        builds = sub_build_characters(slot)
        if not 0 <= index < len(builds):
            return
        window = SubBuildWindow(
            self,
            character=builds[index],
            label=slot.label if slot.count == 1 else f"{slot.label} {index + 1}",
        )
        window.committed.connect(lambda s=slot, i=index, w=window: self._commit(s, i, w))
        window.closed.connect(lambda s=slot, i=index: self._windows.pop((s.key, i), None))
        self._windows[(slot.key, index)] = window
        window.show()

    def _commit(self, slot: SubBuildSlot, index: int, window) -> None:
        """Store what the editor now holds, and restate this card's buttons."""
        store_sub_build(slot, index, window.sheet.character)
        self.changed.emit()
        self.refresh()
