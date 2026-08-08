"""The gear catalog, and a way to put a piece of it on the sheet.

Equipment's counterpart to the Power Constructor: a power is *assembled*, an item is
*chosen*, so what the Equipment block opens is a browsable list rather than a
brick-builder. It shows the whole catalog grouped by the same categories the block
files its cards under, with each item's printed Equipment Point price beside it,
because "what can I afford" is most of the question being asked.

**Modeless**, and for the same reason :class:`~mm_companion.ui.pin_picker.PinPickerDialog`
is: someone kitting a character out is picking four things, and a modal dialog would
make that four round trips. It emits :attr:`EquipmentPickerDialog.entryChosen` as each
is picked and the block fills in behind it.

Its shape follows that picker — a filter box over a two-column tree, right-click or
double-click to act — so the two "pick something off a list" dialogs read the same.
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

from mm_companion.core.data_loader import EquipmentEntry, GameData
from mm_companion.core.equipment import PER_RANK_COST_KINDS
from mm_companion.ui.widgets import muted_style

#: Where a row keeps the :class:`EquipmentEntry` it would add.
ENTRY_ROLE = Qt.ItemDataRole.UserRole

HELP_TEXT = "Double-click an item (or right-click it) to add it to the character."


def cost_text(entry: EquipmentEntry, unit: str) -> str:
    """An entry's printed price as it reads in a list — ``"3 EP"``, ``"1 EP / rank"``.

    An item with no single printed number (``cost_kind`` ``"built"``: it is assembled
    from a trait table) shows its own ``cost_note`` instead of an empty cell, since
    "nothing here" and "priced some other way" are different answers.
    """

    if entry.cost is None:
        return entry.cost_note or "—"
    if entry.cost_kind in PER_RANK_COST_KINDS:
        return f"{entry.cost} {unit} / rank"
    return f"{entry.cost} {unit}"


class EquipmentPickerDialog(QDialog):
    """Browse the equipment catalog and add an item to the character.

    Every entry the loaded ruleset defines is listed — including one a mod added, and
    including the stock vehicles, since the catalog is just
    :meth:`~mm_companion.core.data_loader.GameData.equipment_catalog` and a vehicle
    enters it as one more entry. Groups come from ``equipment_categories`` in declared
    order, and an entry whose category no row names is offered under its own raw id
    rather than dropped: a mod may ship items before it ships the heading for them.
    """

    #: The :class:`EquipmentEntry` the user picked.
    entryChosen = Signal(object)

    def __init__(
        self,
        data: GameData,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Equipment")
        self.resize(420, 520)

        self._data = data
        self._unit = data.equipment_rules.currency_abbreviation

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
        self._tree.setHeaderLabels(["Item", "Cost"])
        self._tree.setRootIsDecorated(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_row_menu)
        self._tree.itemDoubleClicked.connect(lambda item, _c: self._choose(item))
        layout.addWidget(self._tree, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        self.reload()

    # -- contents ------------------------------------------------------------

    def reload(self) -> None:
        """Rebuild the catalog tree, keeping the filter in place."""
        self._tree.clear()
        for category, title, entries in self._groups():
            parent = QTreeWidgetItem([title, ""])
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled)  # a heading, not a choice
            parent.setData(0, ENTRY_ROLE, None)
            parent.setToolTip(0, category)
            self._tree.addTopLevelItem(parent)
            for entry in entries:
                parent.addChild(self._row(entry))
            parent.setExpanded(True)
        self._tree.resizeColumnToContents(0)
        self._apply_filter(self._filter.text())

    def _groups(self) -> list[tuple[str, str, list[EquipmentEntry]]]:
        """``(category id, heading, entries)`` in the ruleset's declared order."""
        by_category: dict[str, list[EquipmentEntry]] = {}
        for entry in self._data.equipment_catalog().values():
            by_category.setdefault(entry.category, []).append(entry)

        groups: list[tuple[str, str, list[EquipmentEntry]]] = []
        for category in self._data.equipment_categories:
            entries = by_category.pop(category.id, [])
            if entries:
                groups.append((category.id, category.title or category.id, entries))
        # Anything the category axis does not name still has to be reachable.
        for category, entries in by_category.items():
            groups.append((category, category or "Other", entries))
        return groups

    def _row(self, entry: EquipmentEntry) -> QTreeWidgetItem:
        item = QTreeWidgetItem([entry.name, cost_text(entry, self._unit)])
        item.setData(0, ENTRY_ROLE, entry)
        tip = entry.description or entry.name
        if entry.cost_note:
            tip = f"{tip}\n{entry.cost_note}"
        item.setToolTip(0, tip)
        item.setToolTip(1, tip)
        return item

    # -- choosing ------------------------------------------------------------

    def _choose(self, item: QTreeWidgetItem | None) -> None:
        entry = None if item is None else item.data(0, ENTRY_ROLE)
        if isinstance(entry, EquipmentEntry):
            self.entryChosen.emit(entry)

    def _show_row_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        entry = None if item is None else item.data(0, ENTRY_ROLE)
        if not isinstance(entry, EquipmentEntry):
            return
        menu = QMenu(self._tree)
        menu.addAction(f"Add {entry.name}", lambda: self._choose(item))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    # -- filtering -----------------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        """Show rows matching every word of *text*, folding away emptied groups.

        Every word rather than the whole string, so "hea arm" finds Heavy Armor —
        the same rule the pin picker and the theme editor's filter follow. The
        haystack includes the item's description, since gear is often looked for by
        what it does rather than by what it is called.
        """
        words = text.lower().split()
        for index in range(self._tree.topLevelItemCount()):
            group = self._tree.topLevelItem(index)
            shown = 0
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                entry = child.data(0, ENTRY_ROLE)
                description = entry.description if isinstance(entry, EquipmentEntry) else ""
                haystack = f"{child.text(0)} {child.text(1)} {group.text(0)} {description}".lower()
                matches = all(word in haystack for word in words)
                child.setHidden(not matches)
                shown += int(matches)
            group.setHidden(shown == 0)
