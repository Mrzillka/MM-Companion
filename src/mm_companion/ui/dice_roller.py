"""A standalone dice-roller window for Mutants & Masterminds checks.

Set a bonus and a penalty (each a labeled slider linked to a spin box) and,
optionally, a Difficulty Class, then click the big D20 to play a short roll
animation and read the result. When a DC is set the result also shows the degree
of success/failure. Past rolls stack up in a history panel (each card can be
removed or saved); a saved roll can be named, dragged to reorder, and lives in a
persistent "quick rolls" strip pinned to the bottom for one-click reuse.

The roll column is a reusable :class:`DiceRollerPanel`;
:class:`DiceRollerWindow` is a thin window around one, and GM Mode embeds the
same panel with its "Hidden roll" option turned on.

**In a session the panel does not roll.** It asks the session — the server
resolves every roll and broadcasts the result, so nobody reports their own
number — and the answer arrives back on
:attr:`~mm_companion.ui.session_bridge.SessionBridge.rollAdded`. The tumble
animation covers the round trip, which is why it is a fixed 1.4 s rather than
instant. Outside a session the panel rolls locally, exactly as before.

The window owns no character state — it drives :mod:`mm_companion.core.dice`
directly (no game rules live here) and persists quick rolls through
:mod:`mm_companion.core.storage`.
"""

from __future__ import annotations

import random
from functools import lru_cache
from importlib.resources import as_file, files

from PySide6.QtCore import QElapsedTimer, QMimeData, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDrag, QFont, QIcon, QPixmap, QShowEvent
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
from mm_companion.ui import theme
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.roll_history import HIDDEN_MARK, RollHistoryPanel, degree_label
from mm_companion.ui.session_bridge import SessionBridge, live_session
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

# How long the die tumbles before revealing the result. The shown face flickers
# fast at first and visibly slows as the roll approaches its end (an ease-out
# deceleration between these two intervals), so the die reads as tumbling and
# then coming to rest rather than flashing at a constant rate.
ROLL_DURATION_MS = 1400
FLICKER_MIN_MS = 40
FLICKER_MAX_MS = 220

# How long a session roll may take before the die gives up. The tumble keeps
# running until the server's answer arrives, so this is the outer bound on a
# session that has gone quiet — long enough to ride out a slow relay, short
# enough that the inputs are not locked forever.
SESSION_ROLL_TIMEOUT_MS = 8000

NO_ANSWER = "The session did not answer, so nothing was rolled."
NOT_SENT = "The roll could not be sent to the session."


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
    return degree_label(result.degree, result.critical, result.die_roll)


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

        # Rich text can't resolve a Qt palette() role, so this takes the literal token.
        muted = theme.color("text.muted.rich")
        headline = f"<b>{total}</b> <span style='color:{muted}'>(d20 {die} {modifier:+d})</span>"
        if dc is not None:
            headline += f" vs DC {dc}"
        title = QLabel(headline)
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setWordWrap(True)
        info.addWidget(title)

        if result is not None:
            color = theme.color("tint.better" if result.success else "tint.worse")
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


class DiceRollerPanel(QWidget):
    """The roll column: the settings, the die, the readout, and the quick rolls.

    Reusable on its own — :class:`DiceRollerWindow` puts one beside a history
    panel, and GM Mode embeds one with ``hidden_option=True`` so the GM can roll
    without it reaching anybody's history.

    Where the roll *comes from* depends on the session: with one live the panel
    asks the server and waits for the broadcast (see the module docstring);
    without one it rolls locally and reports it on :attr:`localRoll`, which is
    what the standalone window's own history renders.
    """

    #: A roll resolved locally, with no session to route it through:
    #: ``{"die", "bonus", "penalty", "dc", "result"}`` — ``result`` being a
    #: :class:`~mm_companion.core.dice.CheckResult`, or ``None`` when no DC was set.
    localRoll = Signal(object)

    #: This panel's own session roll, emitted once the die finishes tumbling — the
    #: cue a paired :class:`~mm_companion.ui.roll_history.RollHistoryPanel` waits on
    #: so one's own card lands as the die settles, not the instant the server answers.
    sessionRollRevealed = Signal(object)

    def __init__(self, parent: QWidget | None = None, *, hidden_option: bool = False) -> None:
        super().__init__(parent)
        self._hidden_option = hidden_option
        self._rolling = False
        # A roll is out with the session and its answer has not come back yet.
        self._awaiting = False
        self._pending: dict | None = None
        # The session this roll went to, and our seat in it — held for as long as
        # the roll is in flight so a broadcast can be told apart from someone
        # else's, and the signal can be disconnected again when it lands.
        self._bridge: SessionBridge | None = None
        self._own_id = ""
        # Wall-clock for the tumble; drives the flicker's ease-out deceleration.
        self._roll_clock = QElapsedTimer()
        self._quick_rolls: list[dict] = self._load_quick_rolls()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
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

        self._rebuild_quick_strip()

    # -- construction --------------------------------------------------------

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

        self._hidden_check = QCheckBox("Hidden roll")
        self._hidden_check.setToolTip(
            "Roll without it reaching anyone else — a hidden roll is never sent "
            "to a player, so there is nothing for them to see."
        )
        self._hidden_check.setVisible(self._hidden_option)
        if self._hidden_option:
            grid.addWidget(self._hidden_check, 3, 0, 1, 3)

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
        font.setPointSizeF(theme.font_size("size.dice-face"))
        font.setBold(True)
        self._face.setFont(font)
        self._face.setStyleSheet(f"color: {theme.color('dice.face')};")

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

    # -- rolling -------------------------------------------------------------

    def _input_widgets(self) -> list[QWidget]:
        return [
            self._bonus_slider,
            self._bonus_spin,
            self._penalty_slider,
            self._penalty_spin,
            self._dc_check,
            self._dc_spin,
            self._hidden_check,
            self._quick_container,
        ]

    def _roll_parameters(self) -> tuple[int, int, int | None, bool]:
        """What the inputs currently ask for: bonus, penalty, DC, hidden."""
        return (
            self._bonus_spin.value(),
            self._penalty_spin.value(),
            self._dc_spin.value() if self._dc_check.isChecked() else None,
            self._hidden_option and self._hidden_check.isChecked(),
        )

    def _start_roll(self) -> None:
        """Begin a roll: lock the inputs, tumble the die, then reveal the result.

        In a session the request goes out *now* and the tumble runs until the
        server's answer arrives; on its own the die is thrown when the tumble
        ends. Either way the inputs stay locked until there is a number.
        """
        if self._rolling:
            return
        self._rolling = True
        self._die_button.setEnabled(False)
        for widget in self._input_widgets():
            widget.setEnabled(False)
        self._roll_clock.restart()
        self._request_session_roll()
        self._tick_roll()

    def _request_session_roll(self) -> None:
        """Put the roll to the session, if there is one to put it to.

        The listener is connected *before* the request goes out: a hosted session
        resolves in-process and emits the answer during the call, so connecting
        afterwards would miss the GM's own roll every time.
        """
        bridge = live_session()
        if bridge is None:
            return
        bonus, penalty, dc, hidden = self._roll_parameters()
        self._bridge = bridge
        self._own_id = bridge.own_player_id()
        self._awaiting = True
        self._pending = None
        bridge.rollAdded.connect(self._on_session_roll)
        if not bridge.request_roll(bonus=bonus, penalty=penalty, dc=dc, hidden=hidden):
            self._abandon_roll(NOT_SENT)

    def _on_session_roll(self, roll: object) -> None:
        """A roll came back from the session — ours, or somebody else's.

        Only our own answers this roll. The die keeps tumbling for the rest of
        its animation even once the number is known, so a fast answer does not
        cut the roll short.
        """
        if not self._awaiting or not isinstance(roll, dict):
            return
        if str(roll.get("player_id", "")) != self._own_id:
            return
        self._pending = roll
        if self._roll_clock.elapsed() >= ROLL_DURATION_MS:
            self._reveal_session_roll(roll)

    def _tick_roll(self) -> None:
        """One flicker frame: show a random face, then reschedule at a widening
        interval so the tumble decelerates, or finish once there is a result."""
        if not self._rolling:  # a direct _finish_roll() (e.g. in tests) pre-empted us
            return
        elapsed = self._roll_clock.elapsed()
        if elapsed >= ROLL_DURATION_MS:
            if not self._awaiting:
                self._finish_roll()
                return
            if self._pending is not None:
                self._reveal_session_roll(self._pending)
                return
            if elapsed >= SESSION_ROLL_TIMEOUT_MS:
                self._abandon_roll(NO_ANSWER)
                return
        self._face.setText(str(random.randint(1, 20)))
        # Ease-out: interval grows with the square of progress, so frames start
        # rapid and stretch out as the die settles.
        progress = min(1.0, elapsed / ROLL_DURATION_MS) if ROLL_DURATION_MS else 1.0
        interval = FLICKER_MIN_MS + (FLICKER_MAX_MS - FLICKER_MIN_MS) * progress**2
        QTimer.singleShot(round(interval), self._tick_roll)

    def _finish_roll(self) -> None:
        """Resolve the roll locally and report it, ending any tumble in progress."""
        self._rolling = False  # stops the _tick_roll chain from rescheduling

        bonus, penalty, dc, _hidden = self._roll_parameters()
        die = roll_d20()
        self._face.setText(str(die))

        modifier = bonus - penalty
        result = resolve_check(modifier, dc, roll=die) if dc is not None else None
        self._update_readout(
            die,
            modifier,
            dc,
            None if result is None else result.degree,
            bool(result is not None and result.critical),
        )
        self.localRoll.emit(
            {"die": die, "bonus": bonus, "penalty": penalty, "dc": dc, "result": result}
        )
        self._unlock_inputs()

    def _reveal_session_roll(self, roll: dict) -> None:
        """Show the number the session rolled for us and let go of the inputs."""
        self._rolling = False
        self._awaiting = False
        self._pending = None
        self._disconnect_session()

        die = int(roll.get("die", 0))
        bonus = int(roll.get("bonus", 0))
        penalty = int(roll.get("penalty", 0))
        dc = roll.get("dc")
        degree = roll.get("degree")
        self._face.setText(str(die))
        self._update_readout(
            die,
            bonus - penalty,
            None if dc is None else int(dc),
            None if degree is None else int(degree),
            bool(roll.get("critical")),
            hidden=bool(roll.get("hidden")),
        )
        self._unlock_inputs()
        # The die has settled — a paired history panel can now show this own roll.
        self.sessionRollRevealed.emit(roll)

    def _abandon_roll(self, message: str) -> None:
        """Give up on a session roll that never came back."""
        if not self._rolling and not self._awaiting:
            return
        self._rolling = False
        self._awaiting = False
        self._pending = None
        self._disconnect_session()
        self._face.setText("?")
        self._readout.setText(f"<span style='color:{theme.color('tint.worse')}'>{message}</span>")
        self._unlock_inputs()

    def _disconnect_session(self) -> None:
        bridge, self._bridge = self._bridge, None
        if bridge is None:
            return
        try:
            bridge.rollAdded.disconnect(self._on_session_roll)
        except (RuntimeError, TypeError):
            # Already disconnected, or the bridge went away underneath us; either
            # way there is nothing left to unhook.
            pass

    def _unlock_inputs(self) -> None:
        self._die_button.setEnabled(True)
        for widget in self._input_widgets():
            widget.setEnabled(True)
        # The DC spin follows its checkbox, not the blanket re-enable above.
        self._dc_spin.setEnabled(self._dc_check.isChecked())

    def _update_readout(
        self,
        die: int,
        modifier: int,
        dc: int | None,
        degree: int | None,
        critical: bool = False,
        *,
        hidden: bool = False,
    ) -> None:
        total = die + modifier
        muted = theme.color("text.muted.rich")
        html = (
            f"<span style='font-size:{theme.font_size('size.roll-readout')}pt'>"
            f"<b>{total}</b></span> "
            f"<span style='color:{muted}'>(d20 {die} {modifier:+d})</span>"
        )
        if dc is not None:
            html += f" vs DC {dc}"
        if degree is not None:
            color = theme.color("tint.better" if degree > 0 else "tint.worse")
            html += f"<br><span style='color:{color}'>{degree_label(degree, critical, die)}</span>"
        if hidden:
            note = f"{HIDDEN_MARK} only you can see this roll"
            html += f"<br><span style='color:{muted}'>{note}</span>"
        self._readout.setText(html)

    # -- quick rolls ---------------------------------------------------------

    def _load_quick_rolls(self) -> list[dict]:
        stored = storage.load_settings().get(QUICK_ROLLS_KEY) or []
        return [dict(entry) for entry in stored]

    def _persist_quick_rolls(self) -> None:
        storage.update_settings(**{QUICK_ROLLS_KEY: self._quick_rolls})

    def save_quick_roll(self, params: dict) -> None:
        """Prompt for an optional name, then save the roll as a quick roll.

        Public because it is what a history card's "★ Save" ends up in, and the
        history — local or shared — is a sibling of this panel, not part of it.
        """
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


class DiceRollerWindow(QMainWindow):
    """A standalone d20 roller: the roll panel on the left, the history on the right.

    Which history depends on the session. On its own the window keeps its own
    list of what *this* app rolled, each card removable and saveable. In a session
    that list is replaced by the table's shared one — every roll here is resolved
    and broadcast by the server anyway, so a private copy beside it would be the
    same rolls twice, minus everyone else's.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Dice Roller")
        self.resize(880, 520)

        self.panel = DiceRollerPanel()
        self.panel.localRoll.connect(self._add_local_card)
        # Held so the same object can be disconnected again; a fresh lambda per
        # sync would leave the old one attached and stack up.
        self._session_bridge: SessionBridge | None = None
        self._on_session_end = lambda *_: self._sync_session()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.panel)
        splitter.addWidget(self._build_history_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([520, 340])
        self.setCentralWidget(splitter)

        self._sync_session()

    # -- construction --------------------------------------------------------

    def _build_history_panel(self) -> QWidget:
        holder = QWidget()
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(0, 0, 0, 0)

        self._local_box = QGroupBox("History")
        local_layout = QVBoxLayout(self._local_box)
        self._history_container = QWidget()
        self._history_layout = QVBoxLayout(self._history_container)
        self._history_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._history_container)
        local_layout.addWidget(scroll)
        outer.addWidget(self._local_box)

        self._session_box = QGroupBox("Session rolls")
        session_layout = QVBoxLayout(self._session_box)
        self._session_history = RollHistoryPanel()
        self._session_history.saveRequested.connect(self.panel.save_quick_roll)
        # Hold this app's own roll until its die stops tumbling; the roller cues it.
        self._session_history.set_defer_own(True)
        self.panel.sessionRollRevealed.connect(self._session_history.release_roll)
        session_layout.addWidget(self._session_history)
        self._session_box.hide()
        outer.addWidget(self._session_box)
        return holder

    # -- the session ---------------------------------------------------------

    def _sync_session(self) -> None:
        """Show whichever history is the right one right now.

        Re-run whenever the answer could have changed — the window may well have
        been opened before the session was joined, and it outlives one ending.
        """
        bridge = live_session()
        if bridge is not self._session_bridge:
            self._follow(bridge)
        self._session_history.attach(bridge)
        self._session_box.setVisible(bridge is not None)
        self._local_box.setVisible(bridge is None)

    def _follow(self, bridge: SessionBridge | None) -> None:
        """Watch a new session end (and stop watching the old one's)."""
        previous, self._session_bridge = self._session_bridge, bridge
        if previous is not None:
            for signal in (previous.disconnected, previous.stopped, previous.kicked):
                try:
                    signal.disconnect(self._on_session_end)
                except (RuntimeError, TypeError):
                    pass
        if bridge is not None:
            for signal in (bridge.disconnected, bridge.stopped, bridge.kicked):
                signal.connect(self._on_session_end)

    # -- the local history ---------------------------------------------------

    def _add_local_card(self, roll: object) -> None:
        """Record a roll this app made on its own — the session was not involved."""
        if not isinstance(roll, dict):
            return
        card = RollCard(
            die=int(roll["die"]),
            bonus=int(roll["bonus"]),
            penalty=int(roll["penalty"]),
            dc=roll["dc"],
            result=roll["result"],
        )
        card.saveRequested.connect(self.panel.save_quick_roll)
        card.removeRequested.connect(lambda c=card: self._remove_history_card(c))
        # Newest on top: insert above every existing card (the stretch is last).
        self._history_layout.insertWidget(0, card)

    def _remove_history_card(self, card: RollCard) -> None:
        self._history_layout.removeWidget(card)
        card.setParent(None)
        card.deleteLater()

    # -- lifecycle -----------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt override)
        self._sync_session()
        super().showEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._session_history.detach()
        self._follow(None)
        super().closeEvent(event)
