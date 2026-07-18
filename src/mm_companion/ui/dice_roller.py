"""A standalone dice-roller window for Mutants & Masterminds checks.

Set a bonus and a penalty (each a labeled slider linked to a spin box) and,
optionally, a Difficulty Class, then click the big D20 to play a short roll
animation and read the result. When a DC is set the result also shows the degree
of success/failure. Past rolls stack up in a history panel (each card can be
removed or saved); a saved roll can be named, dragged to reorder, and lives in a
persistent "quick rolls" strip pinned to the bottom for one-click reuse.

The window owns no character state — it drives :mod:`mm_companion.core.dice`
directly (no game rules live here) and persists quick rolls through
:mod:`mm_companion.core.storage`.
"""

from __future__ import annotations

import random
from functools import lru_cache
from importlib.resources import as_file, files

from PySide6.QtCore import QMimeData, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDrag, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.dice import CheckResult, resolve_check, roll_d20
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box

RESOURCE_PACKAGE = "mm_companion.ui"
D20_RESOURCE = "assets/D20_icon.png"

# The quick-roll list is stored under this settings key as a list of plain dicts
# ``{"bonus": int, "penalty": int, "dc": int | None, "name"?: str}`` so no Qt
# types leak into the JSON settings layer.
QUICK_ROLLS_KEY = "quick_rolls"

# The MIME type carrying a chip's index while it is dragged to reorder.
_DRAG_MIME = "application/x-mm-quick-roll"

# How long the die tumbles before revealing the result, and how often the shown
# face flickers while it does.
ROLL_DURATION_MS = 2000
FLICKER_INTERVAL_MS = 50


@lru_cache(maxsize=1)
def d20_pixmap() -> QPixmap:
    """Load the bundled D20 image (cached — one load per process).

    Mirrors :func:`mm_companion.ui.app_icon.app_icon`: the PNG is a UI asset under
    ``ui/assets/`` and is read via :mod:`importlib.resources` so it resolves when
    the app is installed as a package.
    """
    resource = files(RESOURCE_PACKAGE).joinpath(D20_RESOURCE)
    with as_file(resource) as path:
        return QPixmap(str(path))


def degree_text(result: CheckResult | None) -> str:
    """Human-readable degree of success for a resolved check.

    Maps the signed :attr:`CheckResult.degree` and the natural-1/20
    :attr:`~CheckResult.critical` flag to text like ``"Success (2 degrees)"`` or
    ``"Failure — Nat 1!"``. Returns ``""`` when there is no result (no DC was
    set), since a degree of success is only defined against a DC.
    """
    if result is None:
        return ""
    count = abs(result.degree)
    base = "Success" if result.success else "Failure"
    text = base if count == 1 else f"{base} ({count} degrees)"
    if result.critical:
        text += " — Nat 20!" if result.die_roll == 20 else " — Nat 1!"
    return text


def _params_label(params: dict) -> str:
    """The parameters of a quick roll as text, e.g. ``"+3 vs DC 15"``."""
    modifier = params["bonus"] - params["penalty"]
    label = f"{modifier:+d}"
    if params.get("dc") is not None:
        label += f" vs DC {params['dc']}"
    return label


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


class RollCard(QFrame):
    """One history entry: the die, the modifier breakdown, and (with a DC) the
    degree of success — plus buttons to save its parameters or drop it."""

    saveRequested = Signal(dict)
    removeRequested = Signal()

    def __init__(
        self,
        *,
        die: int,
        bonus: int,
        penalty: int,
        dc: int | None,
        result: CheckResult | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._params = {"bonus": bonus, "penalty": penalty, "dc": dc}

        modifier = bonus - penalty
        total = die + modifier

        layout = QHBoxLayout(self)
        info = QVBoxLayout()

        headline = f"<b>{total}</b> <span style='color:gray'>(d20 {die} {modifier:+d})</span>"
        if dc is not None:
            headline += f" vs DC {dc}"
        title = QLabel(headline)
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setWordWrap(True)
        info.addWidget(title)

        if result is not None:
            color = "green" if result.success else "red"
            degree = QLabel(degree_text(result))
            degree.setStyleSheet(f"color: {color};")
            info.addWidget(degree)

        layout.addLayout(info, stretch=1)

        save_button = QPushButton("★ Save")
        save_button.setToolTip("Save these parameters to the quick rolls strip")
        save_button.clicked.connect(lambda: self.saveRequested.emit(dict(self._params)))
        layout.addWidget(save_button, alignment=Qt.AlignmentFlag.AlignVCenter)

        remove_button = QPushButton("−")
        remove_button.setFixedWidth(24)
        remove_button.setToolTip("Remove this roll from history")
        remove_button.clicked.connect(self.removeRequested.emit)
        layout.addWidget(remove_button, alignment=Qt.AlignmentFlag.AlignVCenter)


class DiceRollerWindow(QMainWindow):
    """A standalone d20 roller: roll settings and the die on the left, a scrollable
    history of past rolls on the right."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Dice Roller")
        self.resize(880, 520)

        self._rolling = False
        self._flicker_timer = QTimer(self)
        self._flicker_timer.setInterval(FLICKER_INTERVAL_MS)
        self._flicker_timer.timeout.connect(self._flicker_face)
        self._quick_rolls: list[dict] = self._load_quick_rolls()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_column())
        splitter.addWidget(self._build_history_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([520, 340])
        self.setCentralWidget(splitter)

        self._rebuild_quick_strip()

    # -- construction --------------------------------------------------------

    def _build_left_column(self) -> QWidget:
        column = QWidget()
        layout = QVBoxLayout(column)

        layout.addWidget(self._build_roll_settings())
        layout.addWidget(self._build_die(), alignment=Qt.AlignmentFlag.AlignHCenter)

        self._readout = QLabel("Click the die to roll.")
        self._readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._readout.setWordWrap(True)
        self._readout.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._readout)

        # The stretch sits above the quick-roll strip so the strip stays pinned to
        # the bottom — the readout growing a degree line no longer nudges it around.
        layout.addStretch()
        layout.addWidget(self._build_quick_rolls())
        return column

    def _build_roll_settings(self) -> QGroupBox:
        group = QGroupBox("Roll")
        grid = QGridLayout(group)

        self._bonus_slider, self._bonus_spin = self._make_slider_spin(0, 20)
        self._penalty_slider, self._penalty_spin = self._make_slider_spin(0, 20)

        grid.addWidget(QLabel("Bonus"), 0, 0)
        grid.addWidget(self._bonus_slider, 0, 1)
        grid.addWidget(self._bonus_spin, 0, 2)

        grid.addWidget(QLabel("Penalty"), 1, 0)
        grid.addWidget(self._penalty_slider, 1, 1)
        grid.addWidget(self._penalty_spin, 1, 2)

        self._dc_check = QCheckBox("Difficulty Class")
        self._dc_spin = make_spin_box(0, 60, value=15)
        self._dc_spin.setEnabled(False)
        self._dc_check.toggled.connect(self._dc_spin.setEnabled)
        grid.addWidget(self._dc_check, 2, 0)
        grid.addWidget(self._dc_spin, 2, 2)

        return group

    def _make_slider_spin(self, minimum: int, maximum: int) -> tuple[QSlider, QWidget]:
        """A horizontal slider linked two-way to a spin box over the same range."""
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        guard_wheel(slider)
        spin = make_spin_box(minimum, maximum)
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        return slider, spin

    def _build_die(self) -> QWidget:
        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)

        self._die_button = QPushButton()
        self._die_button.setFlat(True)
        self._die_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._die_button.setToolTip("Click to roll")
        pixmap = d20_pixmap().scaled(
            160,
            160,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._die_button.setIcon(QIcon(pixmap))
        self._die_button.setIconSize(QSize(160, 160))
        self._die_button.setFixedSize(180, 180)
        self._die_button.clicked.connect(self._start_roll)

        self._face = QLabel("?")
        self._face.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._face.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        font = QFont()
        font.setPointSize(40)
        font.setBold(True)
        self._face.setFont(font)
        self._face.setStyleSheet("color: white;")

        grid.addWidget(self._die_button, 0, 0)
        grid.addWidget(self._face, 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        return container

    def _build_quick_rolls(self) -> QGroupBox:
        group = QGroupBox("Quick rolls")
        layout = QVBoxLayout(group)
        self._quick_container = QuickRollStrip()
        self._quick_flow = FlowLayout(self._quick_container)
        self._quick_container.reordered.connect(self._reorder_quick_roll)
        layout.addWidget(self._quick_container)
        return group

    def _build_history_panel(self) -> QWidget:
        panel = QGroupBox("History")
        outer = QVBoxLayout(panel)

        self._history_container = QWidget()
        self._history_layout = QVBoxLayout(self._history_container)
        self._history_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._history_container)
        outer.addWidget(scroll)
        return panel

    # -- rolling -------------------------------------------------------------

    def _input_widgets(self) -> list[QWidget]:
        return [
            self._bonus_slider,
            self._bonus_spin,
            self._penalty_slider,
            self._penalty_spin,
            self._dc_check,
            self._dc_spin,
            self._quick_container,
        ]

    def _start_roll(self) -> None:
        """Begin a roll: lock the inputs, tumble the die, reveal after ~2 s."""
        if self._rolling:
            return
        self._rolling = True
        self._die_button.setEnabled(False)
        for widget in self._input_widgets():
            widget.setEnabled(False)
        self._flicker_timer.start()
        QTimer.singleShot(ROLL_DURATION_MS, self._finish_roll)

    def _flicker_face(self) -> None:
        self._face.setText(str(random.randint(1, 20)))

    def _finish_roll(self) -> None:
        """Stop the animation, resolve the real roll, and record it."""
        self._flicker_timer.stop()

        bonus = self._bonus_spin.value()
        penalty = self._penalty_spin.value()
        dc = self._dc_spin.value() if self._dc_check.isChecked() else None

        die = roll_d20()
        self._face.setText(str(die))

        modifier = bonus - penalty
        result = resolve_check(modifier, dc, roll=die) if dc is not None else None
        self._update_readout(die, modifier, dc, result)

        card = RollCard(die=die, bonus=bonus, penalty=penalty, dc=dc, result=result)
        card.saveRequested.connect(self._on_save_requested)
        card.removeRequested.connect(lambda c=card: self._remove_history_card(c))
        # Newest on top: insert above every existing card (the stretch is last).
        self._history_layout.insertWidget(0, card)

        self._rolling = False
        self._die_button.setEnabled(True)
        for widget in self._input_widgets():
            widget.setEnabled(True)
        # The DC spin follows its checkbox, not the blanket re-enable above.
        self._dc_spin.setEnabled(self._dc_check.isChecked())

    def _remove_history_card(self, card: RollCard) -> None:
        self._history_layout.removeWidget(card)
        card.setParent(None)
        card.deleteLater()

    def _update_readout(
        self, die: int, modifier: int, dc: int | None, result: CheckResult | None
    ) -> None:
        total = die + modifier
        html = (
            f"<span style='font-size:16pt'><b>{total}</b></span> "
            f"<span style='color:gray'>(d20 {die} {modifier:+d})</span>"
        )
        if dc is not None:
            html += f" vs DC {dc}"
        if result is not None:
            color = "green" if result.success else "red"
            html += f"<br><span style='color:{color}'>{degree_text(result)}</span>"
        self._readout.setText(html)

    # -- quick rolls ---------------------------------------------------------

    def _load_quick_rolls(self) -> list[dict]:
        stored = storage.load_settings().get(QUICK_ROLLS_KEY) or []
        return [dict(entry) for entry in stored]

    def _persist_quick_rolls(self) -> None:
        storage.update_settings(**{QUICK_ROLLS_KEY: self._quick_rolls})

    def _on_save_requested(self, params: dict) -> None:
        """Prompt for an optional name, then save the roll as a quick roll."""
        name, ok = QInputDialog.getText(
            self,
            "Save quick roll",
            f"Name for {_params_label(params)} (optional):",
        )
        if not ok:
            return
        self._add_quick_roll(params, name=name.strip() or None)

    def _add_quick_roll(self, params: dict, name: str | None = None) -> None:
        """Save a roll's parameters (optionally named) as a quick roll (de-duplicated)."""
        entry = {"bonus": params["bonus"], "penalty": params["penalty"], "dc": params.get("dc")}
        if name:
            entry["name"] = name
        if entry in self._quick_rolls:
            return
        self._quick_rolls.append(entry)
        self._persist_quick_rolls()
        self._rebuild_quick_strip()

    def _remove_quick_roll(self, entry: dict) -> None:
        if entry in self._quick_rolls:
            self._quick_rolls.remove(entry)
            self._persist_quick_rolls()
            self._rebuild_quick_strip()

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
        """Load a saved quick roll into the inputs and roll it immediately."""
        self._bonus_spin.setValue(entry["bonus"])
        self._penalty_spin.setValue(entry["penalty"])
        has_dc = entry.get("dc") is not None
        self._dc_check.setChecked(has_dc)
        if has_dc:
            self._dc_spin.setValue(entry["dc"])
        self._start_roll()

    def _rebuild_quick_strip(self) -> None:
        while self._quick_flow.count():
            item = self._quick_flow.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        for index, entry in enumerate(self._quick_rolls):
            self._quick_flow.addWidget(self._make_quick_chip(entry, index))

    def _make_quick_chip(self, entry: dict, index: int) -> QWidget:
        chip = QFrame()
        chip.setFrameShape(QFrame.Shape.StyledPanel)
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
