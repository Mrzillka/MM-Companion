"""The GM Mode page: the Scene's one choice, and what a card's strip starts with.

Two lists, one per kind of card, editing the ``gm_default_pins`` setting. They are
separate because the two cards answer different questions: a GM reads a *player's*
card to know what to roll **against** them (their Defence DC, their Toughness),
and an *NPC's* to know what to roll **with** it.

A default is written down long before the card it will seed exists, which is the
whole shape of this page. It is why the picker it opens is the character-free one
(:func:`~mm_companion.core.rules.pins.default_pin_choices`), why the rows carry no
readings, and why the one power a default can name is the late-bound "first Damage
power" rather than any particular power.

It is also why **Apply to cards on the board** is a button and not what Save does.
Once a card exists its strip is the GM's own — :meth:`GMWindow._pins_for` writes it
into ``gm_pins`` on first sight precisely so a later change here cannot rearrange
what is already on the board. Overruling that is a deliberate act, so it is asked
for outright and confirmed.

Above the lists sit the table's standing decisions — the same kind of thing, made
once rather than per session: whether joining puts a player on the Scene, and whether
players set up their own Impress and area attacks or leave that to the GM. The second
is published to the table when it is saved, so a player's next click already reads it.
Below them, which kinds of failed save put their conditions on an NPC by themselves when
the GM rolls one for it off the history.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.core.rules import (
    CATEGORY_AREA,
    CATEGORY_DAMAGE,
    CATEGORY_EFFECT,
    CATEGORY_IMPRESS,
    PinRef,
    default_pins,
    pin_label,
)
from mm_companion.ui.pin_picker import PinPickerDialog
from mm_companion.ui.settings.page import SettingsPage
from mm_companion.ui.widgets import (
    BOLD_STYLE,
    help_tooltip,
    muted_style,
    resurface,
    tinted_style,
    with_help,
)

#: Where a row keeps the :class:`PinRef` it stands for.
PIN_ROLE = Qt.ItemDataRole.UserRole

#: The two card kinds, in the order the page shows them, with the heading each
#: gets. The keys are the ones ``gm_default_pins`` is stored under.
KINDS: tuple[tuple[str, str], ...] = (("player", "Player cards"), ("npc", "NPC cards"))

AUTO_PLAYERS_LABEL = "Put every player in the Scene automatically"
AUTO_PLAYERS_HINT = (
    "On, joining the session is enough to be on the board. Off, players are added "
    "with the 👁 on their card or by dragging it in, exactly as NPCs are — for a "
    "GM who runs the Scene as a spotlight rather than a roster. Turning it off "
    "leaves whoever is already on the Scene there; it only stops the next player "
    "joining it by themselves."
)

PLAYERS_CHOOSE_LABEL = "Let players set up their own Impress and area attacks"
PLAYERS_CHOOSE_HINT = (
    "On, a player who wants to (their own setting) picks the impression, the skill, the "
    "shape and who is targeted, and you check it and roll. Off, every Impress and area "
    "attack reaches you bare and you set it up. Either way you resolve it."
)

AUTO_APPLY_HINT = (
    "When you roll a save for an NPC off the history (a request card, or a save chip "
    "under an attack) and it fails, what failing does goes on the NPC by itself, and "
    "the roll's card gets a ↶ button that takes it back off. Off, the card only says "
    "what it did and you apply it by hand."
)

#: The automatic-application switches, in the order the page lists them.
AUTO_APPLY_LABELS: tuple[tuple[str, str], ...] = (
    (CATEGORY_DAMAGE, "Damage (Toughness saves)"),
    (CATEGORY_EFFECT, "Afflictions and other effects"),
    (CATEGORY_IMPRESS, "Impressions"),
    (CATEGORY_AREA, "Area attacks"),
)

NOTE_TEXT = (
    "A card's strip starts from these and is then its own: editing a list here "
    "does not rearrange a card already on the board, only the ones a GM has yet "
    "to see. Drag a row to reorder it."
)


class GMPage(SettingsPage):
    """The default pinned-parameter strips, one editable list per card kind."""

    title = "GM Mode"

    def __init__(self, parent: QWidget | None = None, *, data: GameData | None = None) -> None:
        super().__init__(parent)
        self._data = data or load_game_data()
        self._lists: dict[str, QListWidget] = {}
        self._pickers: dict[str, PinPickerDialog] = {}
        self._saved: dict[str, list[PinRef]] = {}
        self._saved_auto_players = True
        self._saved_players_choose = True
        self._auto_apply: dict[str, QCheckBox] = {}
        self._saved_auto_apply: dict[str, bool] = {}

        column = QVBoxLayout(self)
        column.addWidget(self._build_scene())
        column.addWidget(self._build_actions_box())
        column.addWidget(self._build_auto_apply_box())
        column.addWidget(self._build_heading())
        column.addLayout(self._build_lists(), stretch=1)
        column.addLayout(self._build_actions())
        column.addWidget(self._build_status())

        self._load()

    # -- construction ------------------------------------------------------------

    def _build_scene(self) -> QWidget:
        """The Scene's one preference: does joining the table put you on the board."""
        box = QGroupBox("Scene")
        column = QVBoxLayout(box)
        self._auto_players = QCheckBox(AUTO_PLAYERS_LABEL)
        self._auto_players.setToolTip(help_tooltip(AUTO_PLAYERS_HINT))
        self._auto_players.toggled.connect(self._on_edited)
        column.addWidget(with_help(self._auto_players, AUTO_PLAYERS_HINT))
        return box

    def _build_actions_box(self) -> QWidget:
        """The table's rule for actions against other characters."""
        box = QGroupBox("Impress and area attacks")
        column = QVBoxLayout(box)
        self._players_choose = QCheckBox(PLAYERS_CHOOSE_LABEL)
        self._players_choose.setToolTip(help_tooltip(PLAYERS_CHOOSE_HINT))
        self._players_choose.toggled.connect(self._on_edited)
        column.addWidget(with_help(self._players_choose, PLAYERS_CHOOSE_HINT))
        return box

    def _build_auto_apply_box(self) -> QWidget:
        """Which failed saves an NPC rolled off the history takes on by itself."""
        box = QGroupBox("Saves you roll for NPCs")
        column = QVBoxLayout(box)
        heading = QLabel("Apply what a failed save does, for:")
        column.addWidget(with_help(heading, AUTO_APPLY_HINT))
        for key, label in AUTO_APPLY_LABELS:
            check = QCheckBox(label)
            check.toggled.connect(self._on_edited)
            self._auto_apply[key] = check
            column.addWidget(check)
        return box

    def _build_heading(self) -> QWidget:
        panel = QWidget()
        stack = QVBoxLayout(panel)
        stack.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Default pinned parameters")
        title.setStyleSheet(BOLD_STYLE)
        stack.addWidget(with_help(title, NOTE_TEXT))
        return panel

    def _build_lists(self) -> QHBoxLayout:
        row = QHBoxLayout()
        for kind, heading in KINDS:
            row.addWidget(self._build_list(kind, heading), stretch=1)
        return row

    def _build_list(self, kind: str, heading: str) -> QWidget:
        box = QGroupBox(heading)
        column = QVBoxLayout(box)

        widget = QListWidget()
        widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(lambda pos, k=kind: self._show_row_menu(k, pos))
        # A drag that lands is a reorder, and the model is the only thing that
        # reports it — QListWidget has no "the user rearranged me" signal.
        widget.model().rowsMoved.connect(self._on_edited)
        self._lists[kind] = widget
        column.addWidget(widget, stretch=1)

        buttons = QHBoxLayout()
        add = QPushButton("Add…")
        add.clicked.connect(lambda _checked=False, k=kind: self._open_picker(k))
        buttons.addWidget(add)
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda _checked=False, k=kind: self._remove_selected(k))
        buttons.addWidget(remove)
        restore = QPushButton("Restore shipped")
        restore.setToolTip("Put this list back to what MM-Companion ships with.")
        restore.clicked.connect(lambda _checked=False, k=kind: self._restore_shipped(k))
        buttons.addWidget(restore)
        buttons.addStretch()
        column.addLayout(buttons)
        return box

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._save_button = QPushButton("Save")
        self._save_button.clicked.connect(self.save)
        row.addWidget(self._save_button)
        self._apply_button = QPushButton("Apply to cards on the board…")
        self._apply_button.setToolTip(
            "Throw away every card's own strip and seed them all from these lists again."
        )
        self._apply_button.clicked.connect(self._apply_to_board)
        row.addWidget(self._apply_button)
        row.addStretch()
        return row

    def _build_status(self) -> QWidget:
        self._status = QLabel()
        self._status.setWordWrap(True)
        return self._status

    # -- the page contract ---------------------------------------------------------

    def is_dirty(self) -> bool:
        if self._auto_players.isChecked() != self._saved_auto_players:
            return True
        if self._players_choose.isChecked() != self._saved_players_choose:
            return True
        if self.auto_apply() != self._saved_auto_apply:
            return True
        return any(self.pins(kind) != self._saved.get(kind, []) for kind, _heading in KINDS)

    def save(self) -> None:
        """Write both lists, whether or not both changed.

        Both, always: :func:`~mm_companion.core.storage.gm_default_pins` merges per
        kind, so a kind left out of the write is not "unchanged" but "take the
        shipped one" the next time this workspace is read.
        """
        storage.set_gm_default_pins(
            {kind: [ref.to_dict() for ref in self.pins(kind)] for kind, _heading in KINDS}
        )
        storage.set_gm_scene_auto_players(self._auto_players.isChecked())
        storage.set_gm_players_choose_actions(self._players_choose.isChecked())
        storage.set_gm_auto_apply(self.auto_apply())
        self._saved_auto_apply = self.auto_apply()
        self._saved = {kind: self.pins(kind) for kind, _heading in KINDS}
        self._saved_auto_players = self._auto_players.isChecked()
        self._saved_players_choose = self._players_choose.isChecked()
        # The table reads it off the session, so a running one is told now.
        for window in _open_gm_windows():
            window.publish_table_rules()
        self._set_status("Saved. New cards will start with these.")

    def discard(self) -> None:
        """Close anything this page opened. Called whether or not it was saved."""
        for picker in list(self._pickers.values()):
            picker.close()
        self._pickers.clear()

    def auto_apply(self) -> dict[str, bool]:
        """Which kinds of failed save the switches say apply by themselves."""
        return {key: check.isChecked() for key, check in self._auto_apply.items()}

    # -- the lists -----------------------------------------------------------------

    def pins(self, kind: str) -> list[PinRef]:
        """What the *kind* list currently shows, in its current order."""
        widget = self._lists[kind]
        refs = [widget.item(row).data(PIN_ROLE) for row in range(widget.count())]
        return [ref for ref in refs if isinstance(ref, PinRef)]

    def set_pins(self, kind: str, refs: list[PinRef]) -> None:
        """Restate one list. Silent — the caller says whether that was an edit."""
        widget = self._lists[kind]
        widget.clear()
        for ref in refs:
            item = QListWidgetItem(pin_label(ref, self._data))
            item.setData(PIN_ROLE, ref)
            widget.addItem(item)

    def _load(self) -> None:
        """Seed both lists from settings and take that as the saved state."""
        stored = storage.gm_default_pins()
        for kind, _heading in KINDS:
            self.set_pins(kind, default_pins(kind, stored))
        self._saved = {kind: self.pins(kind) for kind, _heading in KINDS}
        # Set without raising the edited signal, which would mark a freshly opened
        # page dirty before anyone had touched it.
        self._auto_players.blockSignals(True)
        self._auto_players.setChecked(storage.gm_scene_auto_players())
        self._auto_players.blockSignals(False)
        self._saved_auto_players = self._auto_players.isChecked()
        self._players_choose.blockSignals(True)
        self._players_choose.setChecked(storage.gm_players_choose_actions())
        self._players_choose.blockSignals(False)
        self._saved_players_choose = self._players_choose.isChecked()
        stored = storage.gm_auto_apply()
        for key, check in self._auto_apply.items():
            check.blockSignals(True)
            check.setChecked(stored.get(key, True))
            check.blockSignals(False)
        self._saved_auto_apply = self.auto_apply()

    def _on_edited(self, *_args: object) -> None:
        self._set_status("")

    def _add_pin(self, kind: str, ref: object) -> None:
        """Append a pin the picker offered, refusing one already in the list.

        A duplicate is refused rather than stacked for the same reason
        :meth:`~mm_companion.ui.pin_panel.PinPanel.add_pin` refuses one: two chips
        reading the same number is never what was meant.
        """
        if not isinstance(ref, PinRef) or ref in self.pins(kind):
            return
        self.set_pins(kind, [*self.pins(kind), ref])
        self._on_edited()

    def _remove_pin(self, kind: str, ref: object) -> None:
        if not isinstance(ref, PinRef):
            return
        self.set_pins(kind, [r for r in self.pins(kind) if r != ref])
        self._on_edited()

    def _remove_selected(self, kind: str) -> None:
        widget = self._lists[kind]
        chosen = {item.data(PIN_ROLE) for item in widget.selectedItems()}
        chosen.discard(None)
        if not chosen:
            return
        self.set_pins(kind, [ref for ref in self.pins(kind) if ref not in chosen])
        self._on_edited()
        self._push_to_picker(kind)

    def _restore_shipped(self, kind: str) -> None:
        """This list as MM-Companion ships it, leaving the other one alone."""
        self.set_pins(kind, default_pins(kind, storage.DEFAULT_SETTINGS["gm_default_pins"]))
        self._on_edited()
        self._push_to_picker(kind)

    def _show_row_menu(self, kind: str, pos) -> None:
        widget = self._lists[kind]
        item = widget.itemAt(pos)
        if item is None:
            return
        menu = QMenu(widget)
        menu.addAction("Remove", lambda: self._remove_pin(kind, item.data(PIN_ROLE)))
        menu.exec(widget.viewport().mapToGlobal(pos))
        self._push_to_picker(kind)

    # -- the picker ------------------------------------------------------------------

    def _open_picker(self, kind: str) -> None:
        """Open (or raise) the defaults picker for one list.

        Modeless and kept referenced, the way the GM window opens a card's picker:
        a GM adding pins is usually adding several, and the list fills in behind it.
        """
        existing = self._pickers.get(kind)
        if existing is not None:
            resurface(existing)
            return
        heading = next(title for k, title in KINDS if k == kind)
        picker = PinPickerDialog(None, self._data, self.pins(kind), self, title=heading)
        picker.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        picker.pinRequested.connect(lambda ref, k=kind: self._add_pin(k, ref))
        picker.unpinRequested.connect(lambda ref, k=kind: self._remove_pin(k, ref))
        picker.destroyed.connect(lambda *_: self._pickers.pop(kind, None))
        self._pickers[kind] = picker
        picker.show()

    def _push_to_picker(self, kind: str) -> None:
        """Tell an open picker what the list holds now.

        Only for changes made *here* — a change the picker itself made it already
        knows about, and its :meth:`~PinPickerDialog.set_pinned` guard is what stops
        the echo rebuilding the tree mid-toggle.
        """
        picker = self._pickers.get(kind)
        if picker is not None:
            picker.set_pinned(self.pins(kind))

    # -- the board -------------------------------------------------------------------

    def _apply_to_board(self) -> None:
        """Seed every existing card from these lists again, on the GM's say-so.

        Saves first, so what lands on the cards is what is on the screen. Then the
        stored per-card strips go, and any open GM window is told to re-seed —
        both, in that order: a window still holding its old strips in memory would
        write them back out on its next edit and undo the clear.
        """
        if not _confirm(
            self,
            "Apply to cards on the board?",
            "Every player and NPC card starts again from these lists.\n\n"
            "Any strip you tailored on a card is lost.",
        ):
            return
        self.save()
        storage.clear_gm_card_pins()
        for window in _open_gm_windows():
            window.reseed_pins_from_defaults()
        self._set_status("Applied. Every card is back to the defaults.")

    def _set_status(self, text: str, *, bad: bool = False) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(tinted_style("tint.worse") if bad else muted_style(italic=True))


def _open_gm_windows() -> list:
    """Every GM window currently open, so a change here can reach the board.

    Found among the top-level widgets rather than through a registry: a GM window
    is a window like any other, and this is the one thing in the app that needs to
    speak to one it did not open. Imported here so the Settings window does not
    drag the whole GM/session layer in just to be shown.
    """
    from mm_companion.ui.gm_window import GMWindow

    app = QApplication.instance()
    if app is None:
        return []
    return [w for w in app.topLevelWidgets() if isinstance(w, GMWindow)]


def _confirm(parent: QWidget, title: str, question: str) -> bool:
    choice = QMessageBox.question(
        parent,
        title,
        question,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return choice == QMessageBox.StandardButton.Yes
