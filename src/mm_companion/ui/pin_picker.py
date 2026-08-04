"""The picker: every number on a character, and a way to pin one to its card.

The list a GM opens, filters, and right-clicks. It shows the *whole* surface —
abilities, resistances, the derived readouts, every skill row, every roll each
power calls for — with each row's reading beside it, because "which of these do I
want" is answered by seeing them.

Those readings are the character's **own** numbers, with no condition penalties
folded in (``with_conditions=False``). This is a catalogue, not a combat readout:
browsing a Vulnerable creature's traits to decide what is worth pinning is not the
moment to be shown its halved Dodge. The chip on the card is the other way round,
and :func:`~mm_companion.core.rules.pins.resolve_pin` explains why.

It **unpins** as well as pins — right-click flips to Unpin on a row that is
already on the card, and a double-click toggles — because the card's own ✕ was
otherwise the only way off, and the GM is already here.

**Modeless**, which is the one real design decision. A GM pinning things is
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
from mm_companion.core.rules import (
    PinGroup,
    PinnedValue,
    PinRef,
    available_pins,
    default_pin_choices,
)
from mm_companion.ui.widgets import muted_style

#: Where a row keeps the :class:`PinRef` it would pin.
PIN_ROLE = Qt.ItemDataRole.UserRole
#: Where a row keeps its plain caption, so the "already pinned" marker can be put
#: on and taken off without the caption accumulating pushpins.
LABEL_ROLE = Qt.ItemDataRole.UserRole + 1

HELP_TEXT = "Right-click a row (or double-click it) to pin or unpin it."


class PinPickerDialog(QDialog):
    """Browse a character's parameters and pin them to (or unpin them from) a GM card.

    *pinned* seeds which rows read as already on the card; the dialog keeps that
    set up to date itself as rows are toggled, so a GM sees what they have just
    done without the owner having to push it back — and
    :meth:`set_pinned` is how the card corrects it when the strip changes from the
    other end.

    A ``None`` *character* puts it in **defaults** mode, which is what the GM Mode
    settings page opens: it lists
    :func:`~mm_companion.core.rules.pins.default_pin_choices` — the pins a strip
    can name before there is anyone to read them off — and drops the "Now" column,
    which would otherwise be a blank beside every row.
    """

    #: The :class:`PinRef` for a row the GM chose.
    pinRequested = Signal(object)
    #: The :class:`PinRef` for a row the GM took back off the card.
    unpinRequested = Signal(object)

    def __init__(
        self,
        character: Character | None,
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
        self._tree.itemDoubleClicked.connect(lambda item, _c: self._toggle(item))
        self._tree.setColumnHidden(1, character is None)
        layout.addWidget(self._tree, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        self.reload()

    # -- contents ------------------------------------------------------------

    def reload(self) -> None:
        """Rebuild the list from the character, keeping the filter in place."""
        self._tree.clear()
        for group in self._groups():
            parent = QTreeWidgetItem([group.title, ""])
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled)  # a heading, not a choice
            self._tree.addTopLevelItem(parent)
            for value in group.values:
                parent.addChild(self._row(value))
            parent.setExpanded(True)
        self._tree.resizeColumnToContents(0)
        self._apply_filter(self._filter.text())

    def _groups(self) -> list[PinGroup]:
        """What this picker is browsing — a character, or what a default may name."""
        if self._character is None:
            return default_pin_choices(self._data)
        return available_pins(self._character, self._data, with_conditions=False)

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
        """Tell the dialog which refs are on the card now.

        A no-op when it already agrees, which is the common case: the card echoes
        every change back here, including the ones this dialog just made. Without
        the guard that echo rebuilds the tree *during* :meth:`_toggle`, deleting
        the very row it is about to restate.
        """
        wanted = set(pinned)
        if wanted == self._pinned:
            return
        self._pinned = wanted
        self.reload()

    def _toggle(self, item: QTreeWidgetItem | None) -> None:
        """Put this row on the card, or take it off — whichever it isn't."""
        ref = None if item is None else item.data(0, PIN_ROLE)
        if not isinstance(ref, PinRef):
            return  # a group heading
        pinned = ref not in self._pinned
        # The row is restated *before* the signal goes out: the card echoes the
        # change back through set_pinned, and this row must already be consistent
        # with what that echo will say.
        if pinned:
            self._pinned.add(ref)
        else:
            self._pinned.discard(ref)
        self._mark_pinned(item, pinned)
        (self.pinRequested if pinned else self.unpinRequested).emit(ref)

    def _show_row_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        ref = None if item is None else item.data(0, PIN_ROLE)
        if not isinstance(ref, PinRef):
            return
        menu = QMenu(self._tree)
        menu.addAction(self.action_text(ref), lambda: self._toggle(item))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def action_text(self, ref: PinRef) -> str:
        """What this row's menu offers — the flip side of where the pin already is."""
        return "Unpin" if ref in self._pinned else "Pin"

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
