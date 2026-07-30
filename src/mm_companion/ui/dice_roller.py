"""The dice roller for Mutants & Masterminds checks.

Set a bonus and a penalty (each a labeled slider linked to a spin box) and,
optionally, a Difficulty Class, then click the big D20 to play a short roll
animation and read the result. When a DC is set the result also shows the degree
of success/failure. Past rolls stack up in a history panel (each card can be
removed or saved); a saved roll can be named, dragged to reorder, and lives in a
persistent "quick rolls" strip pinned to the bottom for one-click reuse.

Three widgets, smallest first:

* :class:`DiceRollerPanel` — the roll controls alone (settings, die + readout,
  quick rolls). GM Mode embeds one directly, with its "Hidden roll" option on.
* :class:`LocalRollHistory` — the private list of what *this* app rolled, each
  card removable and saveable.
* :class:`DiceRollerView` — a panel plus **whichever history is the right one**
  (see below). This is what the sheet's Dice block
  (:mod:`mm_companion.ui.sections.dice`) puts on screen; there is no standalone
  roller window any more.

Both composites **reflow to the space they are given** (see
:mod:`mm_companion.ui.reflow`), each deciding from its own width, which is what
lets the one Dice block suit the tall narrow right-hand pinned strip *and* a short
wide bottom one: a column of four parts, ``[panel][history]``, or one row of four
as the room grows.

**In a session the panel does not roll.** It asks the session — the server
resolves every roll and broadcasts the result, so nobody reports their own
number — and the answer arrives back on
:attr:`~mm_companion.ui.session_bridge.SessionBridge.rollAdded`. The tumble
animation covers the round trip, which is why it is a fixed 1.4 s rather than
instant. Outside a session the panel rolls locally, exactly as before.

Nothing here owns character state — it drives :mod:`mm_companion.core.dice`
directly (no game rules live here) and persists quick rolls through
:mod:`mm_companion.core.storage`.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from functools import lru_cache
from importlib.resources import as_file, files

from PySide6.QtCore import QElapsedTimer, QMimeData, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDrag, QFont, QIcon, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.dice import CheckResult, resolve_check, roll_d20
from mm_companion.core.rules import RollSpec
from mm_companion.ui import theme
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.reflow import ReflowBox
from mm_companion.ui.roll_history import (
    HIDDEN_MARK,
    MAX_QUICK_ROLLS,
    MIN_HISTORY_HEIGHT,
    MIN_HISTORY_WIDTH,
    QuickRollStar,
    RollHistoryPanel,
    chain_widgets,
    degree_label,
    escape_rich_text,
    quick_roll_key,
)
from mm_companion.ui.session_bridge import SessionBridge, live_session
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box, tinted_style

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


def spec_caption(spec: RollSpec) -> str:
    """A loaded spec's chip caption: its name, and its modifier when it has one.

    A spec carrying no modifier of its own — a save (the target's resistance goes in
    the Bonus slider) or a named quick roll — reads as just its name rather than a
    bare ``+0``. Its DC, if it set one, is already visible in the DC box.
    """

    return f"{spec.label} {spec.modifier:+d}" if spec.modifier else spec.label


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
    degree of success — plus a star to save its parameters and a ``−`` to drop it."""

    #: This card's star was clicked — see
    #: :attr:`~mm_companion.ui.roll_history.SessionRollCard.saveToggled`, which is
    #: the same contract from the shared history's card.
    saveToggled = Signal(dict)
    removeRequested = Signal()

    #: A chain button on this card was pressed — see
    #: :func:`~mm_companion.ui.roll_history.chain_widgets`.
    rollFollowUp = Signal(object)

    def __init__(
        self,
        *,
        die: int,
        bonus: int,
        penalty: int,
        dc: int | None,
        result: CheckResult | None,
        label: str = "",
        spec: object = None,
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
        # What was rolled leads, the way the shared history's card names the player —
        # a private history of bare numbers is unreadable a few rolls later.
        if label:
            headline = (
                f"<span style='color:{theme.color('accent.dice')}'>"
                f"{escape_rich_text(label)}</span><br>"
                f"{headline}"
            )
        title = QLabel(headline)
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setWordWrap(True)
        info.addWidget(title)

        if result is not None:
            color = theme.color("tint.better" if result.success else "tint.worse")
            degree = QLabel(degree_text(result))
            degree.setStyleSheet(f"color: {color};")
            info.addWidget(degree)

        for widget in chain_widgets(
            spec,
            die=die,
            degree=None if result is None else result.degree,
            on_follow_up=self.rollFollowUp.emit,
            # Every card in the private history is one's own, and off the air the
            # roller is usually running both sides — so never assume the sheet in
            # front of us is the target's.
            localize=False,
        ):
            info.addWidget(widget)

        layout.addLayout(info, stretch=1)

        self.star = QuickRollStar()
        self.star.clicked.connect(lambda: self.saveToggled.emit(dict(self._params)))
        layout.addWidget(self.star, alignment=Qt.AlignmentFlag.AlignVCenter)

        remove_button = QPushButton("−")
        remove_button.setFixedWidth(24)
        remove_button.setToolTip("Remove this roll from history")
        remove_button.clicked.connect(self.removeRequested.emit)
        layout.addWidget(remove_button, alignment=Qt.AlignmentFlag.AlignVCenter)

    def parameters(self) -> dict:
        """The quick-roll parameters this card would save."""
        return dict(self._params)

    def set_save_state(self, saved_keys: set, room: bool) -> None:
        """Light this card's star if its roll is among *saved_keys*."""
        self.star.set_state(quick_roll_key(self._params) in saved_keys, room)


class DiceRollerPanel(ReflowBox, QWidget):
    """The roll controls: the settings, the die with its readout, and the quick rolls.

    Reusable on its own — :class:`DiceRollerView` puts one beside a history panel,
    and GM Mode embeds one with ``hidden_option=True`` so the GM can roll without
    it reaching anybody's history.

    Its three parts **reflow to the space it is given** (see
    :mod:`mm_companion.ui.reflow`): a column when it is narrow, a row once there is
    width for all three side by side. That is what lets the Dice block work in a
    short, wide bottom strip as well as in the tall, narrow right-hand one.

    Where the roll *comes from* depends on the session: with one live the panel
    asks the server and waits for the broadcast (see the module docstring);
    without one it rolls locally and reports it on :attr:`localRoll`, which is
    what a paired :class:`LocalRollHistory` renders.
    """

    #: A roll resolved locally, with no session to route it through:
    #: ``{"die", "bonus", "penalty", "dc", "result"}`` — ``result`` being a
    #: :class:`~mm_companion.core.dice.CheckResult`, or ``None`` when no DC was set.
    localRoll = Signal(object)

    #: This panel's own session roll, emitted once the die finishes tumbling — the
    #: cue a paired :class:`~mm_companion.ui.roll_history.RollHistoryPanel` waits on
    #: so one's own card lands as the die settles, not the instant the server answers.
    sessionRollRevealed = Signal(object)

    #: The quick-roll strip gained, lost or renamed a chip. Two consequences, both
    #: outside this panel: a paired history's stars have to be re-lit, and the space
    #: this panel was given no longer matches what it needs (see
    #: :meth:`DiceRollerView._redivide`).
    quickRollsChanged = Signal()

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
        # What the sheet asked to roll, if anything — see :meth:`load_spec`. Sticky:
        # it survives the roll so the sliders can be nudged and the die thrown again.
        self._spec: RollSpec | None = None
        # Last chance to adjust a spec before it is loaded — see :meth:`set_localizer`.
        self._localizer: Callable[[RollSpec], RollSpec] | None = None
        self._quick_rolls: list[dict] = self._load_quick_rolls()

        # The three reflowing parts, in order. The layout's *direction* is what the
        # reflow changes, so the parts are added once and never re-parented.
        self._settings_part = self._build_roll_settings()
        self._die_part = self._build_die()
        self._quick_part = self._build_quick_rolls()

        self._box = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(self.REFLOW_SPACING)
        for part in self.reflow_parts():
            self._box.addWidget(part)

        self._rebuild_quick_strip()
        self.init_reflow()

    # -- construction --------------------------------------------------------

    def reflow_parts(self) -> tuple[QWidget, QWidget, QWidget]:
        """The parts the reflow arranges: roll settings, the die, the quick rolls."""
        return (self._settings_part, self._die_part, self._quick_part)

    def apply_reflow(self, row: bool) -> None:
        """Put the three parts in a row or a column.

        Only the layout's direction and its spacers change — the parts themselves
        stay where they are, so nothing is rebuilt or re-parented.

        The trailing stretch belongs to the **column** alone: there it holds the
        quick-roll strip down at the bottom so a readout growing a degree line
        doesn't nudge it about. In a row that same stretch would shove the quick
        rolls off to the right-hand edge, so it is taken out again.
        """
        self._box.setDirection(
            QBoxLayout.Direction.LeftToRight if row else QBoxLayout.Direction.TopToBottom
        )
        # Drop any stretch from the previous arrangement (the parts are widgets, so
        # every non-widget item in the layout is a spacer we put there).
        for index in reversed(range(self._box.count())):
            if self._box.itemAt(index).widget() is None:
                self._box.takeAt(index)
        if row:
            # Across a row the die keeps its fixed 180px and the other two share the
            # slack. Each part is also top-aligned, so a short one (the Roll grid is
            # 108px tall) sits at the top of a tall strip instead of having its rows
            # spread down the whole height. Note this is per *item*: the one-argument
            # setAlignment places the layout inside its widget, which is not the same
            # thing and would leave the parts stretched.
            for part, stretch in zip(self.reflow_parts(), (1, 0, 1), strict=True):
                self._box.setStretchFactor(part, stretch)
                self._box.setAlignment(part, Qt.AlignmentFlag.AlignTop)
        else:
            for part in self.reflow_parts():
                self._box.setStretchFactor(part, 0)
                self._box.setAlignment(part, Qt.AlignmentFlag(0))
            self._box.insertStretch(self._box.indexOf(self._quick_part))

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        self.sync_reflow()

    def _build_roll_settings(self) -> QGroupBox:
        group = QGroupBox("Roll")
        grid = QGridLayout(group)
        # The sliders are the only thing here worth widening, so give their column
        # all the slack. Without this the grid shares spare width equally between the
        # labels, the sliders and the spin boxes, which leaves each slider a few
        # pixels above its 15px minimum — a groove too short to aim at, let alone drag.
        grid.setColumnStretch(1, 1)

        self._spec_chip = self._build_spec_chip()
        grid.addWidget(self._spec_chip, 0, 0, 1, 3)

        self._bonus_slider, self._bonus_spin = self._make_slider_spin(0, 20)
        self._penalty_slider, self._penalty_spin = self._make_slider_spin(0, 20)

        grid.addWidget(QLabel("Bonus"), 1, 0)
        grid.addWidget(self._bonus_slider, 1, 1)
        grid.addWidget(self._bonus_spin, 1, 2)

        grid.addWidget(QLabel("Penalty"), 2, 0)
        grid.addWidget(self._penalty_slider, 2, 1)
        grid.addWidget(self._penalty_spin, 2, 2)

        self._dc_check = QCheckBox("Difficulty Class")
        self._dc_spin = make_spin_box(0, 60, value=15)
        self._dc_spin.setEnabled(False)
        self._dc_check.toggled.connect(self._dc_spin.setEnabled)
        # Spanning the label *and* slider columns: on its own in column 0 this
        # checkbox's caption is by far the widest thing there and would hold that
        # column open to its width, stealing the room from the sliders beside it.
        grid.addWidget(self._dc_check, 3, 0, 1, 2)
        grid.addWidget(self._dc_spin, 3, 2)

        self._hidden_check = QCheckBox("Hidden roll")
        self._hidden_check.setToolTip(
            "Roll without it reaching anyone else — a hidden roll is never sent "
            "to a player, so there is nothing for them to see."
        )
        self._hidden_check.setVisible(self._hidden_option)
        if self._hidden_option:
            grid.addWidget(self._hidden_check, 4, 0, 1, 3)

        return group

    def _build_spec_chip(self) -> QWidget:
        """The strip naming what the sheet asked to roll — hidden until it does.

        It sits *above* the sliders because it is what they now modify: the loaded
        stat supplies the base number and the sliders are the situational extras on
        top of it. The ``✕`` puts the panel back to a plain manual roll.
        """

        chip = QFrame()
        chip.setFrameShape(QFrame.Shape.StyledPanel)
        chip.setVisible(False)
        layout = QHBoxLayout(chip)
        margin = int(theme.metric("space.sm"))
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(int(theme.metric("space.xs")))

        self._spec_label = QLabel()
        self._spec_label.setWordWrap(True)
        self._spec_label.setStyleSheet(tinted_style("accent.dice", bold=True))
        layout.addWidget(self._spec_label, stretch=1)

        clear = QPushButton("✕")
        clear.setFixedWidth(int(theme.metric("column.chip-button")))
        clear.setToolTip("Stop rolling this trait")
        clear.clicked.connect(lambda: self.load_spec(None))
        layout.addWidget(clear, alignment=Qt.AlignmentFlag.AlignVCenter)
        return chip

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
        """The die and the number it rolled — one part, since the two belong together.

        The readout used to be a sibling of the die in the panel's column; it lives
        inside now so the reflow moves the two as a unit.
        """
        part = QWidget()
        column = QVBoxLayout(part)
        column.setContentsMargins(0, 0, 0, 0)

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
        column.addWidget(container, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._readout = QLabel("Click the die to roll.")
        self._readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._readout.setWordWrap(True)
        self._readout.setTextFormat(Qt.TextFormat.RichText)
        column.addWidget(self._readout)
        return part

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
            self._spec_chip,
            self._bonus_slider,
            self._bonus_spin,
            self._penalty_slider,
            self._penalty_spin,
            self._dc_check,
            self._dc_spin,
            self._hidden_check,
            self._quick_container,
        ]

    # -- rolling a trait off the sheet ---------------------------------------

    def current_spec(self) -> RollSpec | None:
        """The trait currently loaded, or ``None`` for a plain manual roll."""
        return self._spec

    def set_localizer(self, localizer: Callable[[RollSpec], RollSpec] | None) -> None:
        """Install a last pass over every spec on its way in.

        A spec can arrive from three directions — a double-clicked stat, a power's
        🎲, or a follow-up chip on a history card, which may be *another player's*
        card. Only one of those is worth adjusting (a save that knows which
        resistance it is but not this character's number for it), but all three end
        up here, so the adjustment is installed once on the panel rather than
        threaded down every path. See
        :meth:`~mm_companion.ui.sections.dice.DiceSection.perform_roll`, which
        supplies it; a panel with no sheet behind it (GM Mode's) installs none.
        """
        self._localizer = localizer

    def load_spec(self, spec: RollSpec | None) -> None:
        """Load *spec* as the thing this panel rolls, or ``None`` to go back to manual.

        Sticky on purpose: the chip stays after the roll so the sliders can be
        adjusted and the die thrown again for the same trait. A spec that names its
        own DC (a power's save) ticks the DC box and fills it in, so the number it
        will roll against is visible rather than implied; a spec without one leaves
        the box exactly as the player left it.
        """
        if spec is not None and self._localizer is not None:
            spec = self._localizer(spec)
        self._spec = spec
        if spec is not None and spec.dc is not None:
            self._dc_check.setChecked(True)
            self._dc_spin.setValue(spec.dc)
        self._spec_chip.setVisible(spec is not None)
        if spec is not None:
            self._spec_label.setText(spec_caption(spec))
            self._spec_chip.setToolTip(spec.hint)
        self.updateGeometry()

    def roll_spec(self, spec: RollSpec | None) -> bool:
        """Load *spec* and roll it at once. ``False`` if a die is already tumbling.

        The one public way in for the sheet: a double-clicked stat line, a power's
        🎲, or a follow-up chip on a history card all land here.
        """
        if self._rolling:
            return False
        self.load_spec(spec)
        self._start_roll()
        return True

    def _roll_parameters(self) -> tuple[int, int, int | None, bool]:
        """What the inputs currently ask for: bonus, penalty, DC, hidden.

        The loaded spec's modifier is folded in **on top of** the sliders rather than
        written into them — the sliders are the situational extras ("+2 for cover"),
        and a trait bonus can in any case exceed their 0-20 range. The net is split
        back into a non-negative bonus and penalty, which is the shape the wire and
        the history cards speak.
        """
        bonus = self._bonus_spin.value()
        penalty = self._penalty_spin.value()
        dc = self._dc_spin.value() if self._dc_check.isChecked() else None
        if self._spec is not None:
            net = self._spec.modifier + bonus - penalty
            bonus, penalty = max(net, 0), max(-net, 0)
            if self._spec.dc is not None:
                dc = self._spec.dc
        return (bonus, penalty, dc, self._hidden_option and self._hidden_check.isChecked())

    def _roll_label(self) -> str:
        """The name this roll travels under — the loaded spec's, or nothing."""
        return self._spec.label if self._spec is not None else ""

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
        sent = bridge.request_roll(
            label=self._roll_label(),
            bonus=bonus,
            penalty=penalty,
            dc=dc,
            hidden=hidden,
            # What is being rolled travels with it, so the *other* seats can act on
            # it: the target's player sees the attack land and clicks the save off
            # its card. The server echoes it back on the record.
            spec=None if self._spec is None else self._spec.to_dict(),
        )
        if not sent:
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
            {
                "die": die,
                "bonus": bonus,
                "penalty": penalty,
                "dc": dc,
                "result": result,
                "label": self._roll_label(),
                "spec": self._spec,
            }
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
        # The spec is already on the record (the server recorded what we sent and
        # broadcast it), so this is the same dict every other seat is reading.
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
        html = ""
        label = self._roll_label()
        if label:
            html += (
                f"<span style='color:{theme.color('accent.dice')}'>"
                f"{escape_rich_text(label)}</span><br>"
            )
        html += (
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
        # Truncated to the cap, so a settings file written before there was one (or
        # by hand) cannot hold the strip open past it.
        return [dict(entry) for entry in stored[:MAX_QUICK_ROLLS]]

    def _persist_quick_rolls(self) -> None:
        storage.update_settings(**{QUICK_ROLLS_KEY: self._quick_rolls})

    def quick_roll_keys(self) -> set[tuple[int, int, int | None]]:
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
        chip with these numbers whatever it has since been renamed to.
        """
        saved = self._find_quick_roll(params)
        if saved is None:
            self._add_quick_roll(params)
        else:
            self._remove_quick_roll(saved)

    def _add_quick_roll(self, params: dict, name: str | None = None) -> None:
        """Save a roll's parameters (optionally named) as a quick roll.

        Refused when the strip already holds the same numbers — by
        :func:`quick_roll_key`, so a name does not make a second chip of one roll —
        or when it is full.
        """
        entry = {"bonus": params["bonus"], "penalty": params["penalty"], "dc": params.get("dc")}
        if name:
            entry["name"] = name
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
        table under that name instead of anonymously. Its numbers stay in the
        sliders where they have always been, so the spec carries no modifier of its
        own — the chip is a caption, not a second bonus.
        """
        self._bonus_spin.setValue(entry["bonus"])
        self._penalty_spin.setValue(entry["penalty"])
        has_dc = entry.get("dc") is not None
        self._dc_check.setChecked(has_dc)
        if has_dc:
            self._dc_spin.setValue(entry["dc"])
        name = str(entry.get("name", "")).strip()
        self.load_spec(RollSpec(label=name) if name else None)
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


class LocalRollHistory(QWidget):
    """The private list of what *this* app rolled, newest first.

    The counterpart of :class:`~mm_companion.ui.roll_history.RollHistoryPanel`:
    that one is a view of a log the whole table shares, this one is a scratch list
    of one's own rolls that only exists while there is no session. Each card can
    be thrown away or saved to the quick-roll strip.
    """

    #: A card's star — the roll's parameters, for the roller to save or unsave.
    saveToggled = Signal(dict)

    #: A card's follow-up button — the next roll in the chain, for the roller to make.
    rollFollowUp = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # What the roller's quick-roll strip holds, mirrored here so every card's
        # star agrees; see :meth:`set_quick_roll_state`.
        self._saved_keys: set = set()
        self._quick_room = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.addStretch()

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._container)
        # The same two floors the shared history pins, and for the same reason: these
        # cards carry the same headline and star, and a scroll area asks for nothing on
        # its own — so without them the history is squeezed to a sliver, or to the one
        # card that fits in whatever height is left over.
        self._scroll.setMinimumWidth(MIN_HISTORY_WIDTH)
        self._scroll.setMinimumHeight(MIN_HISTORY_HEIGHT)
        layout.addWidget(self._scroll)

    def set_quick_roll_state(self, saved_keys: set, room: bool) -> None:
        """Tell every card what the quick-roll strip holds, so its star can show it.

        The counterpart of
        :meth:`RollHistoryPanel.set_quick_roll_state
        <mm_companion.ui.roll_history.RollHistoryPanel.set_quick_roll_state>` — the
        two histories take the same instruction, so whichever one is showing can be
        driven by the same wiring.
        """
        self._saved_keys = set(saved_keys)
        self._quick_room = bool(room)
        for card in self.cards():
            card.set_save_state(self._saved_keys, self._quick_room)

    def add_roll(self, roll: object) -> None:
        """Record a roll this app made on its own — the session was not involved."""
        if not isinstance(roll, dict):
            return
        card = RollCard(
            die=int(roll["die"]),
            bonus=int(roll["bonus"]),
            penalty=int(roll["penalty"]),
            dc=roll["dc"],
            result=roll["result"],
            label=str(roll.get("label", "")),
            spec=roll.get("spec"),
        )
        card.set_save_state(self._saved_keys, self._quick_room)
        card.saveToggled.connect(self.saveToggled)
        card.rollFollowUp.connect(self.rollFollowUp)
        card.removeRequested.connect(lambda c=card: self._remove_card(c))
        # Newest on top: insert above every existing card (the stretch is last).
        self._layout.insertWidget(0, card)

    def cards(self) -> list[RollCard]:
        """The cards on screen, newest first."""
        found = []
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, RollCard):
                found.append(widget)
        return found

    def _remove_card(self, card: RollCard) -> None:
        self._layout.removeWidget(card)
        card.setParent(None)
        card.deleteLater()


class DiceRollerView(ReflowBox, QWidget):
    """A roll panel plus whichever history is the right one, on whichever axis fits.

    Which history depends on the session. On its own the view keeps a private
    :class:`LocalRollHistory` of what *this* app rolled. In a session that list is
    replaced by the table's shared one — every roll here is resolved and broadcast
    by the server anyway, so a private copy beside it would be the same rolls
    twice, minus everyone else's.

    The two sit in a splitter whose orientation **follows the space available**
    (:mod:`mm_companion.ui.reflow`), and the panel reflows its own three parts
    inside that. The two levels decide independently from their own widths, which
    gives three natural shapes as the room grows:

    1. one column of four — the narrow right-hand pinned strip;
    2. ``[settings / die / quick rolls] [history]`` — a medium block;
    3. one row of four — a short, wide bottom strip.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        hidden_option: bool = False,
    ) -> None:
        super().__init__(parent)
        self.panel = DiceRollerPanel(hidden_option=hidden_option)
        self.panel.localRoll.connect(self._add_local_card)
        # Held so the same object can be disconnected again; a fresh lambda per
        # sync would leave the old one attached and stack up.
        self._session_bridge: SessionBridge | None = None
        self._on_session_end = lambda *_: self._sync_session()

        self._history_part = self._build_history_panel()
        # Connected only now that the histories exist for it to reach.
        self.panel.quickRollsChanged.connect(self._on_quick_rolls_changed)
        self._splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._splitter.setChildrenCollapsible(False)
        for part in self.reflow_parts():
            self._splitter.addWidget(part)
        self._splitter.setStretchFactor(0, 0)  # the panel is sized by its content...
        self._splitter.setStretchFactor(1, 1)  # ...and the history takes the slack
        # Whether the handle has been dragged since the last flip. Only a real drag
        # emits splitterMoved (never setSizes), which is the same distinction
        # PinnedBoard._remember_dragged_extent relies on — and it is what lets the
        # row keep re-dividing itself on resize without overriding a chosen split.
        self._user_sized = False
        self._splitter.splitterMoved.connect(self._on_handle_dragged)
        # A resize arrives before the parts' own minimums have settled — the pinned
        # strip converges its thickness over several deferred turns — and the division
        # computed mid-flight is stale, with no further resize coming to correct it.
        # So the row is divided again on the turn *after* the dust settles, the same
        # trick BlockFrame._refresh_layout plays for the same reason.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._redivide)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._splitter)

        self.init_reflow()
        self._sync_session()
        self._sync_quick_roll_state()  # the strip may have been restored with chips in it

    # -- the reflow ----------------------------------------------------------

    def reflow_parts(self) -> tuple[QWidget, QWidget]:
        """The two halves the reflow arranges: the roll panel and the history."""
        return (self.panel, self._history_part)

    def apply_reflow(self, row: bool) -> None:
        """Turn the splitter along the axis that fits, forgetting its old sizes.

        The sizes a handle was dragged to are live pixel values, true only of the
        axis they were measured on — a panel given 200 px of *height* means nothing
        as a *width*. So a flip clears them and lets the splitter lay both children
        out afresh, exactly the rule
        :meth:`~mm_companion.ui.block_canvas.BlockCanvas.set_pin_edge` applies to the
        pinned strip's own remembered sizes.
        """
        self._user_sized = False  # a flip is a fresh axis; nothing has been chosen on it
        self._splitter.setOrientation(Qt.Orientation.Horizontal if row else Qt.Orientation.Vertical)
        self._splitter.setSizes(self._row_sizes() if row else self._column_sizes())

    def _on_handle_dragged(self, _pos: int = 0, _index: int = 0) -> None:
        """The split is the user's from here until the axis flips again."""
        self._user_sized = True

    def _column_sizes(self) -> list[int]:
        """How the panel and the history share the height, stacked.

        Each takes what it asks for — the panel is exactly as tall as its controls,
        the die and however many quick-roll chips there are, and the history's stretch
        gives it the rest. Which is also why this has to be *re-applied* rather than
        set once at the flip: the panel carries no stretch, so a chip going away leaves
        it holding the height it was pushed to and the strip keeps a gap where the chip
        was. See :meth:`_redivide`.
        """
        return [part.sizeHint().height() or 1 for part in self.reflow_parts()]

    def _row_sizes(self) -> list[int]:
        """How the panel and the history share the width, side by side.

        The panel carries no stretch, so whatever it is handed here is what it keeps
        — and that decides whether it can reflow *its own* three parts into a row too.
        That is what produces the one-row-of-four arrangement in a wide bottom strip
        rather than a narrow column of three beside a very wide history.

        So offer the panel its natural row width, and settle for less only as the
        space runs out: down to the width its row needs plus the reflow's dead-band
        (at exactly the threshold it would refuse to flip), and below that back to a
        column, leaving the history its own minimum throughout. The history takes
        whatever is left, which is the lion's share of a wide strip.
        """
        available = self.reflow_available_width()
        floor = self.panel.row_minimum_width() + self.REFLOW_HYSTERESIS
        spare = available - self._history_part.minimumSizeHint().width()
        if spare < floor:
            return [self.panel.column_minimum_width(), max(1, available)]
        natural = self.panel.row_natural_width()
        # A quarter of whatever is left over beyond the panel's natural width, so its
        # controls get a little air (a slider worth dragging, a caption that isn't
        # elided) while the history — which is what actually benefits from length —
        # still keeps the greater part of a wide strip.
        wanted = natural + max(0, spare - natural) // 4
        wanted = max(floor, min(wanted, spare))
        return [wanted, max(1, available - wanted)]

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """Reflow, and re-divide a row as it grows.

        The axis only changes at the threshold, but the panel's *share* of a row has
        to be recomputed as the width changes: the panel carries no stretch, so
        without this it would keep whatever it was given at the moment of the flip —
        which is the threshold width, the narrowest a row ever is — and could never
        widen enough to reflow its own parts. A split the user chose is left alone.
        """
        super().resizeEvent(event)
        self.sync_reflow()
        self._divide_row()
        self._settle.start(0)  # and again once everything has settled

    def _divide_row(self) -> None:
        """Re-divide a row between the panel and the history (a no-op otherwise)."""
        if self.is_row and not self._user_sized:
            self._splitter.setSizes(self._row_sizes())

    def _redivide(self) -> None:
        """Share the space out again on whichever axis is in use.

        A splitter child with no stretch keeps the pixels it was given, so neither axis
        gives space back on its own when the panel needs less of it — the panel's
        minimum drops, and the splitter simply leaves it where it was. So any change to
        what the panel *contains* has to divide the space again explicitly. A split the
        user dragged is still left alone.
        """
        if self._user_sized:
            return
        self._splitter.setSizes(self._row_sizes() if self.is_row else self._column_sizes())

    def _on_quick_rolls_changed(self) -> None:
        """A chip came, went or was renamed: re-light the stars and re-divide.

        The re-division runs twice, and needs to: the chips just dropped are deleted
        later, so the panel's size hint only tells the truth on the following turn —
        the same reason the deferred :attr:`_settle` pass exists for a resize.
        """
        self._sync_quick_roll_state()
        self._redivide()
        self._settle.start(0)

    def _sync_quick_roll_state(self) -> None:
        """Push the strip's contents into whichever history is on screen."""
        keys = self.panel.quick_roll_keys()
        room = not self.panel.quick_rolls_full()
        self._local_history.set_quick_roll_state(keys, room)
        self._session_history.set_quick_roll_state(keys, room)

    # -- construction --------------------------------------------------------

    def _build_history_panel(self) -> QWidget:
        holder = QWidget()
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(0, 0, 0, 0)

        self._local_box = QGroupBox("History")
        local_layout = QVBoxLayout(self._local_box)
        self._local_history = LocalRollHistory()
        self._local_history.saveToggled.connect(self.panel.toggle_quick_roll)
        # A chain button on a card puts the next roll back into the panel it came
        # from — an attack's card offers the save it forced.
        self._local_history.rollFollowUp.connect(self.panel.roll_spec)
        local_layout.addWidget(self._local_history)
        outer.addWidget(self._local_box)

        self._session_box = QGroupBox("Session rolls")
        session_layout = QVBoxLayout(self._session_box)
        self._session_history = RollHistoryPanel()
        self._session_history.saveToggled.connect(self.panel.toggle_quick_roll)
        self._session_history.rollFollowUp.connect(self.panel.roll_spec)
        # Hold this app's own roll until its die stops tumbling; the roller cues it.
        self._session_history.set_defer_own(True)
        self.panel.sessionRollRevealed.connect(self._session_history.release_roll)
        session_layout.addWidget(self._session_history)
        self._session_box.hide()
        outer.addWidget(self._session_box)
        return holder

    # -- the session ---------------------------------------------------------

    def sync_session(self) -> None:
        """Show whichever history is the right one right now.

        Public because a session can be joined (or left) long after this view was
        built: the sheet's Dice block is constructed with the sheet, so nothing
        about it is re-shown when the player joins a table. ``attach_player_session``
        calls through :meth:`~mm_companion.ui.character_sheet.CharacterSheet.sync_session`
        at both ends of a session.
        """
        self._sync_session()

    def _sync_session(self) -> None:
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
        self._local_history.add_roll(roll)

    # -- lifecycle -----------------------------------------------------------

    def detach(self) -> None:
        """Let go of the session — the host block or window is going away."""
        self._session_history.detach()
        self._follow(None)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt override)
        self._sync_session()
        super().showEvent(event)
