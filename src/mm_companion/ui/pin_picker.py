"""The picker: every number on a character, and a way to pin one to its card.

The list a GM opens, filters, and right-clicks. It shows the *whole* surface —
abilities, resistances, the derived readouts, every skill row, every roll each
power calls for — with each row's current reading beside it, because "which of
these do I want" is answered by seeing them.

**Modeless**, which is the one real design decision here. A GM pinning things is
usually pinning three of them, and a modal dialog would make that three round
trips; this one emits :attr:`PinPickerDialog.pinRequested` as each is chosen and
the card fills in behind it.

Its shape follows :mod:`~mm_companion.ui.sections.condition_dialog`, the
established "pick something off this character" dialog.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import PinnedValue, PinRef, available_pins
from mm_companion.ui.widgets import muted_style

#: Where a row keeps the :class:`PinRef` it would pin.
PIN_ROLE = Qt.ItemDataRole.UserRole
#: Where a row keeps its plain caption, so the "already pinned" marker can be put
#: on and taken off without the caption accumulating pushpins.
LABEL_ROLE = Qt.ItemDataRole.UserRole + 1

HELP_TEXT = "Right-click a row (or double-click it) to pin it to the card."


class PinPickerDialog(QDialog):
    """Browse a character's parameters and pin them to a GM card.

    *pinned* seeds which rows read as already on the card; the dialog keeps that
    set up to date itself as rows are pinned, so a GM can see what they have just
    done without the owner having to push it back.
    """

    #: The :class:`PinRef` for a row the GM chose.
    pinRequested = Signal(object)

    def __init__(
        self,
        character: Character,
        data: GameData,
        pinned: list[PinRef] | None = None,
        parent: QWidget | None = None,
        *,
        title: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Pin a parameter — {title}" if title else "Pin a parameter")
        self.resize(360, 480)

        self._character = character
        self._data = data
        self._pinned: set[PinRef] = set(pinned or ())

        layout = QVBoxLayout(self)

        help_label = QLabel(HELP_TEXT)
        help_label.setWordWrap(True)
        help_label.setStyleSheet(muted_style())
        layout.addWidget(help_label)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Parameter", "Now"])
        self._tree.setRootIsDecorated(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_row_menu)
        self._tree.itemDoubleClicked.connect(lambda item, _c: self._pin(item))
        layout.addWidget(self._tree, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        self.reload()

    # -- contents ------------------------------------------------------------

    def reload(self) -> None:
        """Rebuild the list from the character, keeping the filter in place."""
        self._tree.clear()
        for group in available_pins(self._character, self._data):
            parent = QTreeWidgetItem([group.title, ""])
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled)  # a heading, not a choice
            self._tree.addTopLevelItem(parent)
            for value in group.values:
                parent.addChild(self._row(value))
            parent.setExpanded(True)
        self._tree.resizeColumnToContents(0)
        self._apply_filter(self._filter.text())

    def _row(self, value: PinnedValue) -> QTreeWidgetItem:
        """One pinnable value. Its roll's prose goes in the tooltip, which is how
        a power's two rows — an attack and the save it forces, both captioned with
        the power's name — say which is which."""
        item = QTreeWidgetItem([value.label, value.value])
        item.setData(0, PIN_ROLE, value.ref)
        item.setData(0, LABEL_ROLE, value.label)
        if value.hint:
            item.setToolTip(0, value.hint)
            item.setToolTip(1, value.hint)
        self._mark_pinned(item, value.ref in self._pinned)
        return item

    @staticmethod
    def _mark_pinned(item: QTreeWidgetItem, pinned: bool) -> None:
        """Show whether this row is already on the card, without disabling it.

        A disabled row would be unreadable, and re-pinning is harmless anyway —
        :meth:`~mm_companion.ui.pin_panel.PinPanel.add_pin` refuses a duplicate on
        its own. The caption is rebuilt from the plain label kept in
        :data:`LABEL_ROLE` rather than edited in place, so pinning the same row
        twice cannot stack two pushpins on it.
        """
        label = str(item.data(0, LABEL_ROLE) or item.text(0))
        item.setText(0, f"📌 {label}" if pinned else label)
        font = item.font(0)
        font.setBold(pinned)
        item.setFont(0, font)

    # -- pinning -------------------------------------------------------------

    def set_pinned(self, pinned: list[PinRef]) -> None:
        """Tell the dialog which refs are on the card now."""
        self._pinned = set(pinned)
        self.reload()

    def _pin(self, item: QTreeWidgetItem | None) -> None:
        ref = None if item is None else item.data(0, PIN_ROLE)
        if not isinstance(ref, PinRef):
            return  # a group heading
        self._pinned.add(ref)
        self._mark_pinned(item, True)
        self.pinRequested.emit(ref)

    def _show_row_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        ref = None if item is None else item.data(0, PIN_ROLE)
        if not isinstance(ref, PinRef):
            return
        menu = QMenu(self._tree)
        menu.addAction("Pin", lambda: self._pin(item))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    # -- filtering -----------------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        """Show rows matching every word of *text*, folding away emptied groups.

        Every word rather than the whole string, so "per aw" finds Perception —
        the same rule the theme editor's filter follows.
        """
        words = text.lower().split()
        for index in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(index)
            shown = 0
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                label = str(child.data(0, LABEL_ROLE) or child.text(0))
                haystack = f"{label} {child.text(1)} {group.text(0)}".lower()
                matches = all(word in haystack for word in words)
                child.setHidden(not matches)
                shown += int(matches)
            group.setHidden(shown == 0)
