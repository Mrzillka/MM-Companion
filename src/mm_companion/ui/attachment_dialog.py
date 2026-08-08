"""Choosing what an accessory lends the thing it is fitted to.

An accessory is the one kind of gear with no effects of its own to hang a modifier on
— a laser sight's Accurate belongs to the *rifle*, which is the whole point of
``docs/mm-equipment-design.md`` §2 pattern I. So its modifiers live in
:attr:`~mm_companion.core.equipment.EquipmentItem.attachment` rather than on a build,
and this is where a player picks them.

A checklist rather than the constructor's drag-and-drop palette, and deliberately: the
palette drops a brick onto an *effect card*, and an accessory has no card to drop onto.
Sixty-odd general modifiers is a list you filter, not a board you arrange.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.data_loader import GameData, Modifier
from mm_companion.core.powers import ModifierSelection
from mm_companion.ui.widgets import make_spin_box

#: The column the rank spin lives in. Column 0 is the checkable name.
RANK_COLUMN = 1


class AttachmentDialog(QDialog):
    """Pick the modifiers an accessory lends its host, with their ranks.

    Only the **general** pool (``modifiers.json``) is offered, not the effect-specific
    one: an effect-specific modifier is declared against an effect id, and an accessory
    does not know what it will be fitted to — a scope goes on any ranged weapon.

    A ranked modifier's spin box is only enabled while its row is checked, so a rank
    typed against something unticked cannot quietly become part of the answer.
    """

    def __init__(
        self,
        data: GameData,
        selections: list[ModifierSelection],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Lends to its host")
        self._data = data
        self._rows: dict[str, tuple[QTreeWidgetItem, QWidget | None]] = {}

        layout = QVBoxLayout(self)
        blurb = QLabel(
            "These modifiers are added to every effect of whatever this is fitted to. "
            "They are priced on this accessory, not on its host."
        )
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Modifier", "Rank"])
        self._tree.setRootIsDecorated(False)
        self._tree.setIndentation(0)
        layout.addWidget(self._tree)

        chosen = {s.modifier_id: s for s in selections}
        for category, title in (("extra", "Extras"), ("flaw", "Flaws")):
            header = QTreeWidgetItem([title, ""])
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = header.font(0)
            font.setBold(True)
            header.setFont(0, font)
            self._tree.addTopLevelItem(header)
            for modifier in self._data.modifiers:
                if modifier.category != category:
                    continue
                self._add_row(modifier, chosen.get(modifier.id))

        self._tree.itemChanged.connect(self._sync_enabled)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._tree.resizeColumnToContents(0)

    def _add_row(self, modifier: Modifier, selection: ModifierSelection | None) -> None:
        item = QTreeWidgetItem([modifier.name, ""])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            0,
            Qt.CheckState.Checked if selection is not None else Qt.CheckState.Unchecked,
        )
        if modifier.description:
            item.setToolTip(0, modifier.description)
        self._tree.addTopLevelItem(item)

        spin: QWidget | None = None
        if modifier.ranked:
            spin = make_spin_box(1, 100, value=selection.rank if selection else 1)
            spin.setEnabled(selection is not None)
            host = QWidget()
            row = QHBoxLayout(host)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(spin)
            self._tree.setItemWidget(item, RANK_COLUMN, host)
        self._rows[modifier.id] = (item, spin)

    def _sync_enabled(self, item: QTreeWidgetItem, column: int) -> None:
        """A rank only means something while its row is ticked."""
        if column != 0:
            return
        for row, spin in self._rows.values():
            if row is item and spin is not None:
                spin.setEnabled(item.checkState(0) == Qt.CheckState.Checked)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row, _spin in self._rows.values():
            row.setHidden(bool(needle) and needle not in row.text(0).lower())

    def selections(self) -> list[ModifierSelection]:
        """What was ticked, in the ruleset's own order, with the ranks as typed."""
        chosen: list[ModifierSelection] = []
        for modifier_id, (row, spin) in self._rows.items():
            if row.checkState(0) != Qt.CheckState.Checked:
                continue
            rank = spin.value() if spin is not None else 1
            chosen.append(ModifierSelection(modifier_id=modifier_id, rank=rank))
        return chosen
