"""Quick rolls: saved rolls in a strip under the die, one click to reuse, named, dragged to
reorder, and kept in the settings.
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.rules import RollSpec
from mm_companion.ui.flow_layout import FlowContainer
from mm_companion.ui.roll_history import (
    MAX_QUICK_ROLLS,
    quick_roll_key,
)

# The quick-roll list is stored under this settings key as a list of plain dicts
# ``{"bonus": int, "penalty": int, "dc": int | None, "name"?: str}`` so no Qt
# types leak into the JSON settings layer.
QUICK_ROLLS_KEY = "quick_rolls"


# The MIME type carrying a chip's index while it is dragged to reorder.
_DRAG_MIME = "application/x-mm-quick-roll"


def _params_label(params: dict) -> str:
    """The numbers a quick roll loads, as text, e.g. ``"+3"``.

    Only the numbers it *has*: a quick roll carries no DC (see
    :func:`~mm_companion.ui.roll_history.roll_parameters`).
    """
    modifier = params["bonus"] - params["penalty"]
    return f"{modifier:+d}"


def _quick_label(params: dict) -> str:
    """A chip's caption: its name if it has one, otherwise its parameters."""
    return params.get("name") or _params_label(params)


class _DragGrip(QLabel):
    """A small drag handle that starts a reorder drag carrying its chip's index.

    Sits at the left of a quick-roll chip so the drag gesture never collides with
    the chip's own roll/remove buttons.
    """

    def __init__(self, index: int) -> None:
        super().__init__("⠿")
        self._index = index
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to reorder")

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_DRAG_MIME, str(self._index).encode("ascii"))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)


class QuickRollStrip(FlowContainer):
    """The quick-roll chip host: a flow container that accepts reorder drops."""

    reordered = Signal(int, int)  # source index, insertion index

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.mimeData().hasFormat(_DRAG_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.mimeData().hasFormat(_DRAG_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not event.mimeData().hasFormat(_DRAG_MIME):
            return
        source = int(bytes(event.mimeData().data(_DRAG_MIME)).decode("ascii"))
        target = self._drop_index(event.position().toPoint())
        event.acceptProposedAction()
        self.reordered.emit(source, target)

    def _drop_index(self, pos) -> int:
        """The list index the dragged chip should be inserted before."""
        layout = self.layout()
        if layout is None:
            return 0
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if widget is None:
                continue
            geo = widget.geometry()
            if pos.y() <= geo.bottom() and pos.x() < geo.center().x():
                return i
        return layout.count()


class RollerQuickRollsMixin:
    """Part of :class:`~mm_companion.ui.dice_roller.DiceRollerPanel`; see the module docstring."""

    # -- quick rolls ---------------------------------------------------------

    def _load_quick_rolls(self) -> list[dict]:
        """The saved strip, normalised to what a quick roll is *now*.

        A workspace written before this carries a ``dc`` on every entry, and two
        chips that differed only by their DC collapse onto one key once it is
        dropped — so the read de-duplicates as well as truncating, or the strip
        would hold two chips that look and behave identically and only the first
        would ever answer a click.
        """
        stored = storage.load_settings().get(QUICK_ROLLS_KEY) or []
        rolls: list[dict] = []
        seen: set[tuple[str, int, int]] = set()
        for entry in stored:
            name = str(entry.get("name", "")).strip()
            clean = {"bonus": int(entry.get("bonus", 0)), "penalty": int(entry.get("penalty", 0))}
            if name:
                clean["name"] = name
            key = quick_roll_key(clean)
            if key in seen:
                continue
            seen.add(key)
            rolls.append(clean)
            # Capped here rather than by slicing the file, so a settings file written
            # before there was a cap (or by hand) cannot hold the strip open past it.
            if len(rolls) == MAX_QUICK_ROLLS:
                break
        return rolls

    def _persist_quick_rolls(self) -> None:
        storage.update_settings(**{QUICK_ROLLS_KEY: self._quick_rolls})

    def quick_roll_keys(self) -> set[tuple[str, int, int]]:
        """What the strip holds, as :func:`quick_roll_key` tuples — for the stars."""
        return {quick_roll_key(entry) for entry in self._quick_rolls}

    def quick_rolls_full(self) -> bool:
        """Whether the strip has reached :data:`MAX_QUICK_ROLLS` and holds no more."""
        return len(self._quick_rolls) >= MAX_QUICK_ROLLS

    def toggle_quick_roll(self, params: dict) -> None:
        """Save *params* as a quick roll, or take it back out if it is already one.

        Public because it is what a history card's star ends up in, and the history —
        local or shared — is a sibling of this panel, not part of it. The star is a
        two-way switch, so this is one slot rather than a save and an unsave: the card
        reports the click and the panel, which owns the strip, decides which it was.

        A saved roll is matched by :func:`quick_roll_key`, so a click takes out the
        chip this card's roll saved and no other.
        """
        saved = self._find_quick_roll(params)
        if saved is None:
            self._add_quick_roll(params)
        else:
            self._remove_quick_roll(saved)

    def _add_quick_roll(self, params: dict, name: str | None = None) -> None:
        """Save a roll's name and numbers as a quick roll.

        The name comes with *params* (a roll travels named); *name* overrides it,
        which is how a caller with a better one says so. Refused when the strip
        already holds this exact roll — by :func:`quick_roll_key` — or when it is
        full. No DC is stored: see
        :func:`~mm_companion.ui.roll_history.roll_parameters` for why.
        """
        entry = {"bonus": int(params["bonus"]), "penalty": int(params["penalty"])}
        chosen = str(name if name is not None else params.get("name", "")).strip()
        if chosen:
            entry["name"] = chosen
        if self._find_quick_roll(entry) is not None or self.quick_rolls_full():
            return
        self._quick_rolls.append(entry)
        self._persist_quick_rolls()
        self._rebuild_quick_strip()

    def _remove_quick_roll(self, entry: dict) -> None:
        if entry in self._quick_rolls:
            self._quick_rolls.remove(entry)
            self._persist_quick_rolls()
            self._rebuild_quick_strip()

    def _rename_quick_roll(self, entry: dict) -> None:
        """Ask for this chip's caption; an empty answer puts its numbers back.

        The one place a quick roll is named. Saving is a single click on a star with no
        dialog in the way, so the name comes afterwards, from the chip itself.

        The stored entry is looked up by :func:`quick_roll_key` rather than written
        through *entry*: only the dict a chip's own lambda closed over is the one in the
        list, and this is reachable from anywhere with a matching set of numbers.
        """
        stored = self._find_quick_roll(entry)
        if stored is None:
            return
        name, ok = QInputDialog.getText(
            self,
            "Rename quick roll",
            f"Name for {_params_label(stored)} (leave empty for none):",
            text=str(stored.get("name", "")),
        )
        if not ok:
            return
        name = name.strip()
        renamed = (
            dict(stored, name=name) if name else {k: v for k, v in stored.items() if k != "name"}
        )
        clash = self._find_quick_roll(renamed)
        if clash is not None and clash is not stored:
            # The name is part of a chip's identity now, so this would make two
            # chips one roll — and only the first would ever answer a click.
            return
        if name:
            stored["name"] = name
        else:
            stored.pop("name", None)
        self._persist_quick_rolls()
        self._rebuild_quick_strip()

    def _find_quick_roll(self, params: dict) -> dict | None:
        """The stored entry loading the same inputs as *params*, if there is one."""
        key = quick_roll_key(params)
        for entry in self._quick_rolls:
            if quick_roll_key(entry) == key:
                return entry
        return None

    def _reorder_quick_roll(self, source: int, insert_index: int) -> None:
        """Move the quick roll at *source* to *insert_index* (a drop position)."""
        if not 0 <= source < len(self._quick_rolls):
            return
        entry = self._quick_rolls.pop(source)
        # The insertion index was measured against the full list; account for the
        # entry we just removed if it sat before the drop point.
        if insert_index > source:
            insert_index -= 1
        insert_index = max(0, min(insert_index, len(self._quick_rolls)))
        self._quick_rolls.insert(insert_index, entry)
        self._persist_quick_rolls()
        self._rebuild_quick_strip()

    def _apply_quick_roll(self, entry: dict) -> None:
        """Load a saved quick roll into the inputs and roll it immediately.

        A *named* chip also loads its name as a spec, so a saved roll reaches the
        table under that name. Its numbers stay in the sliders, so the spec carries
        no modifier of its own: the chip is a caption, not a second bonus, and
        **transient** like :meth:`roll_spec`'s, since the roll spends the sliders it
        names (:meth:`_spend_extras`).

        **The DC box is left as it is.** A quick roll carries none
        (:func:`~mm_companion.ui.roll_history.roll_parameters`): the difficulty in
        front of the player now beats one saved weeks ago.
        """
        self._bonus_spin.setValue(entry["bonus"])
        self._penalty_spin.setValue(entry["penalty"])
        name = str(entry.get("name", "")).strip()
        self.load_spec(RollSpec(label=name) if name else None)
        self._transient = bool(name)
        self._start_roll()

    def _rebuild_quick_strip(self) -> None:
        """Render one chip per quick roll, and report that the strip changed.

        The report matters as much as the render. A chip coming or going changes what
        this panel's minimum is, and two things have to hear about it: the stars in a
        paired history (which roll is saved), and whatever is dividing space with this
        panel — :meth:`DiceRollerView._redivide`. ``updateGeometry`` is what makes the
        enclosing :class:`~mm_companion.ui.block_frame.BlockFrame` ask again, since a
        minimum that has just *shrunk* provokes no resize of its own.
        """
        while self._quick_flow.count():
            item = self._quick_flow.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        for index, entry in enumerate(self._quick_rolls):
            self._quick_flow.addWidget(self._make_quick_chip(entry, index))
        self.updateGeometry()
        self.quickRollsChanged.emit()
        self.contentChanged.emit()

    def _make_quick_chip(self, entry: dict, index: int) -> QWidget:
        chip = QFrame()
        chip.setFrameShape(QFrame.Shape.StyledPanel)
        # Right-click is where a chip is named. Set on the frame alone: a child button
        # leaves a context-menu event unhandled, so it propagates up to here.
        chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        chip.customContextMenuRequested.connect(
            lambda pos, c=chip, e=entry: self._show_chip_menu(c, pos, e)
        )
        layout = QHBoxLayout(chip)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        layout.addWidget(_DragGrip(index))

        roll_button = QPushButton(_quick_label(entry))
        roll_button.setFlat(True)
        roll_button.setCursor(Qt.CursorShape.PointingHandCursor)
        roll_button.setToolTip(f"Load and roll — {_params_label(entry)}")
        roll_button.clicked.connect(lambda _=False, e=entry: self._apply_quick_roll(e))
        layout.addWidget(roll_button)

        remove_button = QPushButton("×")
        remove_button.setFixedWidth(20)
        remove_button.setToolTip("Remove this quick roll")
        remove_button.clicked.connect(lambda _=False, e=entry: self._remove_quick_roll(e))
        layout.addWidget(remove_button)
        return chip

    def _show_chip_menu(self, chip: QWidget, pos: QPoint, entry: dict) -> None:
        """The chip's own menu: name it, or throw it away."""
        menu = QMenu(chip)
        menu.addAction("Rename…").triggered.connect(lambda: self._rename_quick_roll(entry))
        menu.addAction("Remove").triggered.connect(lambda: self._remove_quick_roll(entry))
        menu.exec(chip.mapToGlobal(pos))
