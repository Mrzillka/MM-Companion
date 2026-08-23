"""The System / Power Level block: the non-purchasable game characteristics.

Split out of the former base-info block. Ten rows, in the order they are built: power
level, the power-point pool, the homebrew-cost notice, the **Power Level limits**, size,
reach, speed, movement modes, initiative, hero points and Extra Effort. Power level and
the point pool feed the build (``changed``); the rest are derived readouts or
descriptive edits (``edited``).

**Four of those rows are not always there.** The cost notice, Reach and Movement appear
only when they have something to say, and an NPC sheet drops Power Level, Hero Points
and the limits (see :meth:`SystemInfoSection.set_npc_mode`). All of them go through
:meth:`SystemInfoSection._set_row_visible`, which takes the *row* out of the form rather
than only the widgets in it — hiding a widget leaves its row's spacing behind.

Everything below the pool is *derived* — computed in :mod:`mm_companion.core.rules` from
abilities, advantages, conditions and active powers — so this block exposes
:meth:`SystemInfoSection.refresh_derived` for the sheet to call whenever those inputs
change. The widgets never compute rules themselves.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    PIN_INITIATIVE,
    PinRef,
    character_reach,
    clear_extra_effort,
    clear_stunts,
    condition_check_penalty,
    condition_speed_lines,
    condition_speed_rank_mod,
    effective_size,
    estimated_power_level,
    has_cost_overrides,
    initiative_ability,
    initiative_modifier,
    initiative_roll,
    movement_mode_lines,
    power_level_cap_summary,
    power_level_for_points,
    pushed_effects,
    pushed_trait_labels,
    reach_is_altered,
    reach_text,
    reconcile_points_to_level,
    speed_columns,
    spend_extra_effort,
)
from mm_companion.ui import theme
from mm_companion.ui.extra_effort import ExtraEffortDialog, character_effort_menu
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.roll_click import ROLL_TOOLTIP, attach_roll_click
from mm_companion.ui.sections.cost_config_dialog import CostConfigDialog
from mm_companion.ui.sections.stat_table import PinMenuState
from mm_companion.ui.sections.titled_section import strip_groupbox_caption
from mm_companion.ui.svg_assets import hero_point_pixmap
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box, muted_style, tinted_style

HERO_POINT_PIPS = 5
INITIATIVE_TIP = f"Agility (or an Alternate Initiative ability) plus advantages\n{ROLL_TOOLTIP}"
REACH_TIP = (
    "How far this character can make close attacks — their size's own reach, plus "
    "whatever an active effect stretches on top.\nShown only while something has moved "
    "it off the reach their bought size gives."
)


class HeroPointsWidget(QWidget):
    """Five hero-point pips, each its own switch; the count is how many are lit.

    A pip toggles on its own — light the fourth and leave the rest dark if that is
    how you like to read your row. A held point shows the lit drawing, a spent one
    the dimmed twin; which pair of the bundled ones (:mod:`~mm_companion.ui.svg_assets`)
    is the theme's ``assets.hero-point`` choice. Emits :attr:`valueChanged` on a
    user click.

    **Which** pips are lit is cosmetic: the character carries a count, so
    :meth:`set_value` — a load, or a GM's command — can only say *how many*. It
    reconciles rather than redraws (see there), which keeps an arrangement the
    player made wherever the new count still allows it.

    **Five is the resting count, not a cap.** ``characteristics.json`` puts the ceiling
    at 99 and the rules put none on a GM handing out a sixth, so a row that stopped at
    five did not merely fail to draw the point — :meth:`set_value` clamped, and
    ``_on_hero_points_changed`` wrote the clamped number straight back to the model, so
    the sixth point was *destroyed* rather than hidden. The row grows a pip instead, and
    shrinks back to five when the count drops.

    Hero points stay clickable even when the sheet is locked — they are spent
    during play, not a build value — so this widget has no locked state.
    """

    valueChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lit: set[int] = set()
        self._buttons: list[QPushButton] = []
        self._pip_size = int(theme.metric("column.hero-point"))
        self.setToolTip("Click a pip to spend or gain a hero point")

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(theme.metric("space.xs")))
        # The pip is the artwork edge to edge: a button frame or padding would
        # scale it down inside its own cell. The hover wash stands in for the
        # button chrome that goes with them.
        style = (
            "QPushButton { border: none; padding: 0; background: transparent; }"
            f"QPushButton:hover {{ background: {theme.wash('accent', 0.18)};"
            f" border-radius: {self._pip_size // 2}px; }}"
        )
        self._style = style
        self._row = row
        self._grow_to(HERO_POINT_PIPS)
        row.addStretch()
        self._render()

    def _grow_to(self, count: int) -> None:
        """Make sure there are at least *count* pips, adding any that are missing.

        Pips are only ever added here and only ever removed by :meth:`_shrink_to`, so a
        player's arrangement survives a count that goes up and comes back down.
        """

        while len(self._buttons) < count:
            index = len(self._buttons)
            button = QPushButton()
            button.setFlat(True)
            button.setFixedSize(self._pip_size, self._pip_size)
            button.setIconSize(QSize(self._pip_size, self._pip_size))
            button.setStyleSheet(self._style)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, i=index: self._on_click(i))
            self._buttons.append(button)
            # Before the trailing stretch, so the row stays left-aligned as it grows.
            self._row.insertWidget(index, button)

    def _shrink_to(self, count: int) -> None:
        """Drop pips past *count*, never below :data:`HERO_POINT_PIPS`."""

        while len(self._buttons) > max(HERO_POINT_PIPS, count):
            button = self._buttons.pop()
            self._lit.discard(len(self._buttons))
            self._row.removeWidget(button)
            button.setParent(None)
            button.deleteLater()

    def value(self) -> int:
        return len(self._lit)

    def set_value(self, value: int) -> None:
        """Set the count without emitting :attr:`valueChanged`, growing the row to fit it.

        The caller has a number, not an arrangement, so this *reconciles* to it:
        spending puts out the right-most lit pips, gaining lights the left-most
        dark ones. A player who lit only the fourth pip and then spends a point
        still ends up where they expect, and a load with nothing to preserve
        settles on the left-most ``value`` pips.

        Only the floor is clamped. A count above five grows the row rather than being
        cut down to it — see the class docstring for what the cut used to cost.
        """
        target = max(0, int(value))
        self._grow_to(target)
        while len(self._lit) > target:
            self._lit.discard(max(self._lit))
        while len(self._lit) < target:
            self._lit.add(min(set(range(len(self._buttons))) - self._lit))
        self._shrink_to(target)
        self._render()

    def _on_click(self, index: int) -> None:
        """Flip one pip. Each is independent — no filling up to the click."""
        if index in self._lit:
            self._lit.discard(index)
        else:
            self._lit.add(index)
        self._render()
        self.valueChanged.emit(self.value())

    def changeEvent(self, event: QEvent) -> None:
        # Dragged to a screen of a different scaling, the pips are re-rasterised at
        # that screen's ratio rather than left as a stretched copy of the old one.
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            self._render()
        super().changeEvent(event)

    def _render(self) -> None:
        ratio = self.devicePixelRatioF()
        # The variant is read here rather than kept from __init__, so a widget
        # rebuilt after a preset switch draws the new artwork.
        variant = theme.asset("hero-point")
        for i, button in enumerate(self._buttons):
            pixmap = hero_point_pixmap(i in self._lit, self._pip_size, ratio, variant)
            button.setIcon(QIcon(pixmap))


class SpeedWidget(QWidget):
    """Movement speeds as one line per mode, with a ft/round ↔ km/h toggle.

    Each :class:`~mm_companion.core.rules.SpeedLine` renders as
    ``Label: walk / dash / run`` (see :func:`~mm_companion.core.rules.speed_columns`),
    every mode anything active grants adding its own line. Only the first — the ground
    line everybody has — carries the run column; every other mode moves or dashes. The
    unit button flips every line between the imperial per-round distance and the km/h
    equivalent.

    One **label per row** rather than a single rich-text block, for the reason
    :class:`MovementModesWidget` already builds rows: a line is the *mode* now, so what
    granted it goes on the row's hover, and a tooltip cannot be applied to part of a
    label.
    """

    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = data
        self._metric = False
        self._lines: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(int(theme.metric("space.xxs")))

        self._rows = QWidget()
        rows = QVBoxLayout(self._rows)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(int(theme.metric("space.xxs")))
        layout.addWidget(self._rows)

        self._unit_button = QPushButton()
        self._unit_button.setCheckable(False)
        self._unit_button.setToolTip(
            "Switch every speed line between the imperial distance covered in one round "
            "and the equivalent sustained km/h."
        )
        self._unit_button.clicked.connect(self._toggle_unit)
        layout.addWidget(self._unit_button, alignment=Qt.AlignmentFlag.AlignLeft)

        self._sync_unit_button()

    def render_lines(self, lines: list) -> None:
        """Redraw from the given :class:`SpeedLine` list.

        Any condition overlay is already resolved into the lines by
        :func:`~mm_companion.core.rules.condition_speed_lines` — a slowed line arrives
        at its reduced rank carrying the penalty in ``rank_mod``, an immobilised one is
        flagged. This widget only expands ranks into distance columns and tints.
        """
        self._lines = lines
        self._redraw()

    def _redraw(self) -> None:
        layout = self._rows.layout()
        while layout.count():  # rebuilt wholesale — a speed list is a handful of rows
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for index, line in enumerate(self._lines):
            # The first line is the ground one, and the only mode with a run column —
            # see :func:`~mm_companion.core.rules.speed_columns`.
            layout.addWidget(self._row_label(line, ground=index == 0))

    def _row_label(self, line, *, ground: bool) -> QLabel:
        """One movement line, tinted by its condition overlay and explained on hover."""

        if line.immobilised:
            label = QLabel(f"{line.label}: immobilised")
            label.setStyleSheet(tinted_style("tint.worse"))
        else:
            columns = speed_columns(line.rank, self._data, metric=self._metric, ground=ground)
            text = f"{line.label}: " + " / ".join(_compact(column) for column in columns)
            if line.rank_mod:
                text += f" ({line.rank_mod:+d} rank)"
            label = QLabel(text)
            if line.rank_mod:
                label.setStyleSheet(tinted_style("tint.worse"))
        if line.sources:
            # A line is a mode, so its own caption cannot say what granted it — two
            # Flight powers and a worn glider all land on one "Flight" row.
            label.setToolTip("From " + ", ".join(line.sources))
        return label

    def rendered_text(self) -> str:
        """Every speed row's caption, newline-joined — what the block actually reads.

        A read-back seam rather than a private probe: the rows are built and thrown away
        on each redraw, so a caller reaching into the layout would be reaching into an
        implementation detail that has already changed once.
        """

        layout = self._rows.layout()
        return "\n".join(
            widget.text()
            for i in range(layout.count())
            if (widget := layout.itemAt(i).widget()) is not None
        )

    def rendered_tooltips(self) -> str:
        """Every speed row's hover text, newline-joined — what *granted* each line."""

        layout = self._rows.layout()
        return "\n".join(
            widget.toolTip()
            for i in range(layout.count())
            if (widget := layout.itemAt(i).widget()) is not None
        )

    def rendered_styles(self) -> str:
        """Every speed row's inline style, newline-joined — which lines are tinted.

        The tint moved off the text and onto the row when the readout stopped being one
        rich-text block, so "is the slowed line red" is asked here rather than by looking
        for a colour inside the caption.
        """

        layout = self._rows.layout()
        return "\n".join(
            widget.styleSheet()
            for i in range(layout.count())
            if (widget := layout.itemAt(i).widget()) is not None
        )

    def _toggle_unit(self) -> None:
        self._metric = not self._metric
        self._sync_unit_button()
        self._redraw()

    def _sync_unit_button(self) -> None:
        self._unit_button.setText("Show ft / round" if self._metric else "Show km / h")


class MovementModesWidget(QWidget):
    """The specialised speeds the character's active powers grant.

    One row per :class:`~mm_companion.core.rules.MovementModeLine` — the mode's name,
    the speed it moves at, and any per-tier caveat. Unlike :class:`SpeedWidget`'s single
    rich-text label this builds a label per row, because each mode carries its own hover
    description and a tooltip cannot be applied to part of a label.

    Hidden entirely while nothing grants a mode, so the block gains no empty row for
    the many characters that have none.
    """

    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = data
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(int(theme.metric("space.xxs")))
        self.setVisible(False)

    def render_lines(self, lines: list) -> None:
        layout = self.layout()
        while layout.count():  # rebuilt wholesale — a mode list is a handful of rows
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for line in lines:
            # Read like a SpeedLine — move / dash, no run column — since a specialised
            # mode is a way of moving too, and one row saying "30 ft/round" beside
            # another saying "30 ft / 60 ft" reads as two different quantities.
            columns = speed_columns(line.rank, self._data, ground=False)
            text = f"{line.label}: " + " / ".join(_compact(column) for column in columns)
            if line.note:
                text += f" ({line.note})"
            label = QLabel(text)
            label.setWordWrap(True)
            if line.description:
                label.setToolTip(line.description)
            layout.addWidget(label)
        self.setVisible(bool(lines))


class PowerLevelCapsWidget(QWidget):
    """How close the character is to each of their character-wide Power Level limits.

    The block owns Power Level, and until now it stated the number and nothing the number
    *does*. The caps were computed all along — :func:`~mm_companion.core.rules.
    power_level_violations` has evaluated the paired-resistance caps and the per-skill
    modifier cap since long before this — but the only caller was a minion's build, so a
    player whose own Dodge + Toughness was over Power Level got no mark anywhere on the
    sheet while a single power got a warning glyph. This is that answer, shown.

    **Headroom, not just the alarm.** Each line reads ``Dodge + Toughness 18/20``,
    because the interesting moment is the one *before* the breach: a cap is a budget a
    player spends deliberately, and one that only speaks up when it is already broken is
    a budget you cannot plan against. A line goes ``tint.warning`` only when the build is
    actually past it — sitting exactly on a cap is the intended place to sit.

    One label per line rather than one rich-text label, for :class:`MovementModesWidget`'s
    reason: each cap carries its own tooltip and a tooltip cannot cover part of a label.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(int(theme.metric("space.xxs")))
        self._lines: list[str] = []

    def rendered_text(self) -> list[str]:
        """What each line currently reads — the test seam, like :class:`SpeedWidget`'s."""
        return list(self._lines)

    def render_caps(self, caps: list) -> None:
        """Redraw from a list of :class:`~mm_companion.core.rules.CapStatus`."""

        layout = self.layout()
        while layout.count():  # a handful of rows; rebuilt wholesale like the mode list
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._lines = []
        for cap in caps:
            name = f"{cap.short} ({cap.detail})" if cap.detail else cap.short
            text = f"{name} {cap.value}/{cap.limit}"
            label = QLabel(text)
            label.setStyleSheet(tinted_style("tint.warning") if cap.over else muted_style())
            label.setToolTip(
                cap.message
                if cap.over
                else f"{cap.label}: {cap.value} of the {cap.limit} this Power Level allows."
            )
            layout.addWidget(label)
            self._lines.append(text)
        self.setVisible(bool(caps))


def hero_point_note(previous: int, current: int) -> str:
    """The sentence for a hero-point change, or ``""`` when nothing moved.

    Deliberately says the same thing whoever caused it: a point the GM granted and
    a point the player clicked are the same event at the table, and the card names
    the character either way. The new total rides along because "spent a hero
    point" without it sends everyone back to the sheet to count pips.
    """
    delta = current - previous
    if not delta:
        return ""
    verb = "gained" if delta > 0 else "spent"
    count = abs(delta)
    points = "a hero point" if count == 1 else f"{count} hero points"
    return f"{verb} {points} — {current} left"


def _compact(label: str) -> str:
    """Shorten a distance label for the compact speed row (``"15 feet"`` → ``"15 ft"``)."""
    return label.replace(" feet", " ft").replace(" foot", " ft")


#: What the ground line's three columns are, which nothing on the sheet said. Shown
#: whenever no condition has something more urgent to say in the same place.
SPEED_TIP = (
    "Distance covered in one round: a normal move / a move action spent dashing / "
    "a full round spent running.\n"
    "Every other line is a movement mode something active grants, which moves and "
    "dashes but does not run."
)


def _speed_condition_tooltip(speed_mod: int | None) -> str:
    """What the speed widget says on hover — a condition's overlay, or what it is showing.

    The condition wins the tooltip when there is one, because it is the thing that has
    *changed*. With none, the row explained nothing at all: three numbers separated by
    slashes, and no statement anywhere on the sheet of which was which."""
    if speed_mod is None:
        return "Immobilised by an active condition — no ground movement."
    if speed_mod:
        return f"Ground speed slowed by an active condition ({speed_mod:+d} rank)."
    return SPEED_TIP


class SystemInfoSection(QGroupBox):
    """Power level, the points pool and the limits it sets, plus the derived readouts.

    The module docstring lists the rows. Emits :attr:`changed` when an edit affects the
    point build (power level / points) and :attr:`edited` on any user edit for
    unsaved-change tracking.

    The block owns Power Level, so it owns what Power Level *does*: the **Limits** row
    (:class:`PowerLevelCapsWidget`) states how close the character is to each
    character-wide cap, and the spent half of the pool tints when the build has outrun
    its budget (:meth:`_restate_pool_balance`). Both are readouts over
    :mod:`mm_companion.core.rules` and neither computes a rule of its own.
    """

    changed = Signal()
    edited = Signal()
    #: Raised when a homebrew PP-cost rate changes, so every priced block re-titles
    #: its subtotal (the pool total already recomputes off ``changed``).
    costRatesChanged = Signal()
    #: The Initiative readout was double-clicked — roll it. Carries a
    #: :class:`~mm_companion.core.rules.RollSpec`; rolling is not a build edit.
    rollRequested = Signal(object)
    #: It was clicked once — show it in the roller's chip, ready to roll.
    loadRequested = Signal(object)
    #: A sentence for the roll history — a hero point spent or gained, or a use of
    #: Extra Effort. Carries the text, since the block that writes it down cannot see
    #: what changed here.
    noteRequested = Signal(str)
    #: A condition was put on the character from here — the fatigue Extra Effort costs.
    #: The same fan-out the Conditions block's own signal drives, because it is the same
    #: event: the model changed, and every view over a condition has to restate itself.
    conditionsChanged = Signal()
    #: The +2 Extra Effort buys on a check (p21) — the one benefit that is a number on
    #: the *next roll* rather than on the build. Nothing tracks which roll that will be,
    #: so it is handed to the block that owns the sliders and the player rolls with it.
    bonusRequested = Signal(int)
    #: The Initiative readout was right-clicked and pinned — carries a
    #: :class:`~mm_companion.core.rules.pins.PinRef`. Only ever raised on a sheet a
    #: GM opened from a card (see :meth:`set_pin_target`).
    pinRequested = Signal(object)
    #: The same, for a readout that was already on the card.
    unpinRequested = Signal(object)

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        strip_groupbox_caption(self)

        self._loading = True
        self._data = data
        self._character = character
        self._locked = False
        # Whether this sheet was opened from a GM card, and what is already on
        # that card. Both are set by the sheet after construction.
        self._pins = PinMenuState()
        self._by_key = {c.key: c for c in data.characteristics}
        self._editable: list[QWidget] = []
        # A GM's NPC has no point budget (see :meth:`set_npc_mode`).
        self._npc = False
        # A sheet whose budget is handed to it rather than chosen (see
        # :meth:`set_budget_fixed`).
        self._budget_fixed = False

        form = self._form = QFormLayout(self)
        form.addRow("Power Level:", self._build_power_level())
        form.addRow("Power Points:", self._build_power_points())
        form.addRow(self._build_cost_notice())
        self._limits_row_label = QLabel("Limits:")
        form.addRow(self._limits_row_label, self._build_limits())
        form.addRow("Size:", self._build_size())
        # The two self-hiding rows keep a named caption purely as a test seam: nothing
        # here reads them, since _set_row_visible finds a row's label for itself.
        self._reach_row_label = QLabel("Reach:")
        form.addRow(self._reach_row_label, self._build_reach())
        form.addRow("Speed:", self._build_speed())
        self._movement_row_label = QLabel("Movement:")
        form.addRow(self._movement_row_label, self._build_movement_modes())
        form.addRow("Initiative:", self._build_initiative())
        form.addRow("Hero Points:", self._build_hero_points())
        form.addRow("Extra Effort:", self._build_extra_effort())

        self.refresh_derived()
        self._loading = False

    # -- widget construction -------------------------------------------------

    def _seed(self, key: str, fallback: object) -> object:
        value = self._character.characteristics.get(key)
        if value is not None:
            return value
        c = self._by_key.get(key)
        return c.default if c and c.default is not None else fallback

    def _build_power_level(self) -> QWidget:
        c = self._by_key.get("power_level")
        self._power_level = make_spin_box(
            c.minimum if c else 0, c.maximum if c else 30, value=int(self._seed("power_level", 10))
        )
        self._power_level.setToolTip(
            "The series Power Level this character is built to. It sets the point budget "
            "beside it and every cap in the Limits row below."
        )
        self._power_level.valueChanged.connect(self._on_power_level_changed)
        self._editable.append(self._power_level)
        return self._power_level

    def _build_power_points(self) -> QWidget:
        c = self._by_key.get("power_points")
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        self._pool_current = QLabel("—")
        self._pool_current.setToolTip("Spent — calculated from the build")
        self._pool_separator = QLabel("/")
        self._power_points = make_spin_box(
            c.minimum if c else 0,
            c.maximum if c else 9999,
            value=int(self._seed("power_points", 150)),
        )
        self._power_points.setToolTip("Total available")
        self._power_points.valueChanged.connect(self._on_power_points_changed)
        # The NPC sheet's stand-in for the whole pool: the Power Level the build's
        # traits estimate to. Hidden until :meth:`set_npc_mode` asks.
        self._estimated_pl = QLabel("—")
        self._estimated_pl.setToolTip(
            "Estimated from this build's Resistances and best attack — an NPC is not "
            "budgeted, so its Power Level is read from what it can do, not what it costs."
        )
        self._estimated_pl.hide()
        row.addWidget(self._pool_current)
        row.addWidget(self._pool_separator)
        row.addWidget(self._power_points)
        row.addWidget(self._estimated_pl)
        row.addStretch()
        self._editable.append(self._power_points)
        self._points_row = container
        return container

    def _build_cost_notice(self) -> QWidget:
        self._cost_notice = QLabel("⌂ Homebrew PP costs")
        self._cost_notice.setStyleSheet(tinted_style("tint.homerule"))
        self._cost_notice.setToolTip(
            "Homebrew PP cost changed — this character uses non-default point costs "
            "(Settings ▸ Cost config)."
        )
        self._cost_notice.setVisible(False)
        return self._cost_notice

    def _build_size(self) -> QWidget:
        c = self._by_key.get("size")
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        self._size_combo = QComboBox()
        if c:
            self._size_combo.addItems(c.options)
        seed = str(self._seed("size", "Medium"))
        if seed in [self._size_combo.itemText(i) for i in range(self._size_combo.count())]:
            self._size_combo.setCurrentText(seed)
        self._size_combo.setToolTip(
            "The size this character is built at. It shifts their Defence, Toughness, "
            "Damage and Speed limits, their Intimidation and Stealth, and their reach."
        )
        self._size_combo.currentTextChanged.connect(self._on_size_changed)
        guard_wheel(self._size_combo)
        # Effective size when an active Growth/Shrinking shifts it away from the base.
        self._size_effective = QLabel()
        self._size_effective.setStyleSheet(muted_style())
        self._size_effective.setToolTip(
            "The size the character is at right now — an active Growth or Shrinking has "
            "moved them off the size they were bought at."
        )
        row.addWidget(self._size_combo)
        row.addWidget(self._size_effective)
        row.addStretch()
        self._editable.append(self._size_combo)
        return container

    def _build_limits(self) -> QWidget:
        """The Power Level limits readout — see :class:`PowerLevelCapsWidget`.

        It sits under the Power Level and the point pool because those are its two
        inputs: the caps are read off the level, and the traits they measure are what the
        points bought. Hidden for an NPC, whose Power Level is *estimated* from its
        traits rather than bought — measuring those traits back against a number derived
        from them would be a tautology, not a limit.
        """

        self._limits = PowerLevelCapsWidget()
        return self._limits

    def _build_reach(self) -> QWidget:
        """The close-attack reach readout — a row that is not there most of the time.

        Every character has a reach and almost none of them has an interesting one: the
        baseline is your own Space, which is what a close attack already means, so
        stating it on every sheet would be noise. The row appears only once something has
        moved it (:func:`~mm_companion.core.rules.reach_is_altered`) — a Growth, a
        Shrinking, an Elongation — which is also the only time it needs reading.
        """

        self._reach = QLabel("—")
        self._reach.setToolTip(REACH_TIP)
        self._reach.setVisible(False)
        return self._reach

    def _build_speed(self) -> QWidget:
        self._speed = SpeedWidget(self._data)
        return self._speed

    def _build_movement_modes(self) -> QWidget:
        self._movement_modes = MovementModesWidget(self._data)
        return self._movement_modes

    def _build_initiative(self) -> QWidget:
        self._initiative = QLabel("—")
        self._initiative.setToolTip(INITIATIVE_TIP)
        # Initiative is the one readout on this block a GM pins, and the only
        # pinnable line on the sheet that is not a table row — so the menu is hung
        # on the label itself rather than through stat_table.
        self._initiative.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._initiative.customContextMenuRequested.connect(self._show_initiative_pin_menu)
        # The one readout on this block that is a die roll rather than a fact. Its
        # tooltip is rewritten on every refresh, so the click hint is folded into
        # that text rather than left to attach_roll_click.
        attach_roll_click(
            self._initiative,
            lambda: initiative_roll(self._character, self._data),
            self.rollRequested.emit,
            load_sink=self.loadRequested.emit,
            tooltip=False,
        )
        return self._initiative

    def _show_initiative_pin_menu(self, pos) -> None:
        if not self._pins.enabled:
            return
        ref = PinRef(PIN_INITIATIVE)
        sink = self.unpinRequested if self._pins.is_pinned(ref) else self.pinRequested
        menu = QMenu(self._initiative)
        menu.addAction(self._pins.action_text(ref), lambda: sink.emit(ref))
        menu.exec(self._initiative.mapToGlobal(pos))

    def set_pin_target(self, enabled: bool) -> None:
        """Whether the Initiative readout offers to pin at all."""
        self._pins.enabled = enabled

    def set_pinned(self, refs) -> None:
        """Which parameters are already on the card, so the readout can offer Unpin."""
        self._pins.set_pinned(refs)

    def _build_hero_points(self) -> QWidget:
        self._hero_points = HeroPointsWidget()
        self._hero_points.set_value(int(self._seed("hero_points", 1)))
        # What the total was before the change being handled, so a note can say
        # whether a point was spent or gained. Seeding is silent — the widget's own
        # set_value emits nothing — so this starts out agreeing with the model.
        self._last_hero_points = self._hero_points.value()
        self._hero_points.valueChanged.connect(self._on_hero_points_changed)
        return self._hero_points

    # -- edits ----------------------------------------------------------------

    def _on_power_level_changed(self, value: int) -> None:
        self._character.characteristics["power_level"] = value
        self._character.power_level = value
        self._link_pl_pp(edited="power_level")
        self.changed.emit()
        self._emit_edited()

    def _on_power_points_changed(self, value: int) -> None:
        self._character.characteristics["power_points"] = value
        self._character.power_points_total = value
        self._link_pl_pp(edited="power_points")
        self._restate_pool_balance()  # the budget moved; what is left of it moved with it
        self.changed.emit()
        self._emit_edited()

    def _on_size_changed(self, text: str) -> None:
        self._character.characteristics["size"] = text
        self.refresh_derived()  # size shifts ground speed and the effective-size readout
        self._emit_edited()

    def _on_hero_points_changed(self, value: int) -> None:
        """The single funnel for a hero-point change, wherever it came from.

        A click on a pip and a GM's command both arrive here (see
        :meth:`set_hero_points`), which is why the note is raised here rather than
        in either of them — one code path, so a point can never move silently.
        """
        self._character.characteristics["hero_points"] = value
        note = hero_point_note(self._last_hero_points, value)
        self._last_hero_points = value
        if note:
            self.noteRequested.emit(note)
        self._emit_edited()

    def _build_extra_effort(self) -> QWidget:
        """The button that offers what Extra Effort can buy, and charges what it costs.

        It lives here because both of Extra Effort's currencies do: the fatigue it costs
        is a condition on the shared model, and the Hero Point that shrugs the fatigue off
        is these very pips. The two uses that name one of the character's own effects are
        taken on the power's card instead — the menu says so rather than hiding them.

        Deliberately **not** in ``_editable``: spending Extra Effort is a mid-play action
        like clicking a pip, so it survives the lock.
        """

        self._extra_effort = QPushButton("Use…")
        self._extra_effort.setToolTip(
            "Push past your limits (p20). The benefit is immediate; at the start of your "
            "next turn you gain the next rung of the fatigue ladder."
        )
        self._extra_effort.clicked.connect(self._show_extra_effort_menu)
        # What is currently pushed. A power's card carries an "Extra Effort" row saying
        # why a rank moved; a pushed *trait* has no card, and the enhancement column that
        # shows it looks like any other bonus — so without this the only sign the
        # character is straining is a number that has quietly changed.
        self._effort_note = QLabel()
        self._effort_note.setStyleSheet(tinted_style("tint.warning", bold=False))
        self._effort_note.setVisible(False)
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._extra_effort)
        layout.addWidget(self._effort_note)
        layout.addStretch()
        return row

    def _refresh_effort_note(self) -> None:
        """Restate what Extra Effort is currently holding up, or hide the note.

        Names the traits, because those are the ones with nowhere else to say it, and
        counts the effects, because each of those already says so on its own card.
        """

        parts = [
            f"{label} +{ranks}"
            for label, ranks in pushed_trait_labels(self._character, self._data).items()
        ]
        effects = len(pushed_effects(self._character))
        if effects:
            parts.append(f"{effects} effect{'' if effects == 1 else 's'}")
        self._effort_note.setText(("⚡ " + ", ".join(parts)) if parts else "")
        self._effort_note.setToolTip(
            "Held up by Extra Effort until the end of your turn — clear it from this menu."
        )
        self._effort_note.setVisible(bool(parts))

    def extra_effort_menu(self) -> QMenu:
        """The menu of uses, built but not shown — the seam the tests take.

        Split from :meth:`_show_extra_effort_menu` for the reason the Powers block splits
        its counter menu: ``exec`` on a modal menu headless is a test that hangs.
        """

        return character_effort_menu(
            self,
            self._character,
            self._data,
            self.use_extra_effort,
            self.clear_extra_effort,
            self.drop_stunts,
            self.push_trait,
        )

    def _show_extra_effort_menu(self) -> None:
        menu = self.extra_effort_menu()
        menu.exec(self._extra_effort.mapToGlobal(self._extra_effort.rect().bottomLeft()))

    def use_extra_effort(self, use) -> bool:
        """Confirm one use of Extra Effort and charge it; ``False`` when it was cancelled.

        The fatigue is applied to the shared model by
        :func:`~mm_companion.core.rules.spend_extra_effort` — the condition resolver is
        core's, so a rung gained this way bundles and supersedes exactly like one the
        Conditions block applied — and the blocks that show conditions restate themselves
        off the topics raised here.

        The **Bonus** use is the one whose benefit is not on the build at all: "+2 on a
        single check", which nothing here can apply because nothing tracks which check it
        will be. It is raised on ``bonus-requested`` instead, and the Dice block drops it
        into the bonus slider — so the player rolls with it rather than being charged a
        rung of fatigue for a number they then have to remember to type in.
        """

        dialog = ExtraEffortDialog(self._character, self._data, use, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        outcome = spend_extra_effort(
            self._character,
            self._data,
            use,
            doubled=dialog.doubled,
            determination=dialog.determination,
        )
        if dialog.spend_hero_point:
            self.adjust_hero_points(-1)
        if outcome.check_bonus:
            self.bonusRequested.emit(outcome.check_bonus)
        self.noteRequested.emit(outcome.note)
        self.conditionsChanged.emit()
        self._emit_edited()
        return True

    def push_trait(self, use, target) -> bool:
        """Push ranks into one of the character's own traits; ``False`` when cancelled.

        The rank increase's third target, after an effect's rank: "your Strength rank for
        either Damage or Lifting, or your movement Speed rank in one mode of movement you
        have" (p21). Neither is an effect, so neither has a card to be taken on, and this
        block is the one that owns the character rather than any one power.

        A *build* change rather than a runtime one, for the reason
        :meth:`clear_extra_effort` publishes one: the ranks are saved with the sheet, and
        every block that shows an ability or a speed restates itself off ``facts-changed``.
        """

        dialog = ExtraEffortDialog(
            self._character, self._data, use, effect_name=target.label, parent=self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        outcome = spend_extra_effort(
            self._character,
            self._data,
            use,
            trait=target,
            doubled=dialog.doubled,
            determination=dialog.determination,
        )
        if dialog.spend_hero_point:
            self.adjust_hero_points(-1)
        self.noteRequested.emit(outcome.note)
        self.conditionsChanged.emit()
        self.changed.emit()
        self._emit_edited()
        return True

    def clear_extra_effort(self) -> bool:
        """Take back every rank Extra Effort pushed into this character.

        Publishes an ordinary *build* change rather than reaching into the Powers block:
        the ranks live on the effects, the block that draws them subscribes to
        ``facts-changed``, and one blanket republish is how every other cross-block edit
        on this sheet restates itself.
        """

        if not clear_extra_effort(self._character):
            return False
        self.changed.emit()
        self._emit_edited()
        return True

    def drop_stunts(self) -> bool:
        """Drop every power stunt on the character — the scene ended.

        Publishes a build change for the same reason :meth:`clear_extra_effort` does: the
        cards belong to the Powers block, and the sheet restates itself off the model
        rather than one block reaching into another.
        """

        if not clear_stunts(self._character):
            return False
        self.changed.emit()
        self._emit_edited()
        return True

    def adjust_hero_points(self, delta: object) -> None:
        """Move the hero-point total by ``delta`` — the seam the bus spends through.

        A block that costs the character a hero point (Extra Effort shrugged off with a
        Determination heroic feat, p22) has no business writing the model itself: the
        pips, the clamp and the sentence for the roll history are all one funnel here
        (:meth:`_on_hero_points_changed`), and a second writer would move a total the
        pips then disagreed with.
        """

        try:
            step = int(delta)
        except (TypeError, ValueError):
            return
        self.set_hero_points(max(0, self._hero_points.value() + step))

    def set_hero_points(self, value: int) -> None:
        """Set the hero-point total from outside the sheet — a GM's session command.

        Moves the pips, writes the model, and marks the sheet edited so the
        snapshot pusher relays the new total back to the GM's card. (The widget's
        own :meth:`~HeroPointsWidget.set_value` is deliberately silent, so this
        does the model write and edit signal the click handler would.)
        """
        self._hero_points.set_value(value)
        self._on_hero_points_changed(self._hero_points.value())

    def open_cost_config(self) -> None:
        """Open the homebrew cost-rate editor; a saved change refreshes the whole build.

        Called from the window's ``Settings ▸ Cost config…`` action (the block itself no
        longer carries the button, only the homebrew notice).
        """
        dialog = CostConfigDialog(self._character, self._data, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._refresh_cost_notice()
        self._link_pl_pp(edited="power_level")  # a new pp/level rate may shift the budget band
        self.costRatesChanged.emit()  # re-title every priced block's PP subtotal
        self.changed.emit()  # fan out so the pool total re-derives
        self._emit_edited()

    def _refresh_limits(self) -> None:
        """Restate the Power Level limits row, or drop it.

        Every input moves it — the level itself, an ability, a skill rank, worn armour,
        an active Growth — which is why it hangs off :meth:`refresh_derived` rather than
        off the two fields it sits under. Gone entirely for an NPC (see
        :meth:`_build_limits`) and for a ruleset that declares no character-wide caps.
        """

        caps = [] if self._npc else power_level_cap_summary(self._character, self._data)
        self._limits.render_caps(caps)
        self._set_row_visible(self._limits, bool(caps))

    def _refresh_cost_notice(self) -> None:
        """Show the homebrew-cost notice when any non-power rate differs from default.

        Never shown for an NPC — it has no point budget, so PP-cost homebrew is moot.
        """
        self._cost_notice.setVisible(
            not self._npc and has_cost_overrides(self._character, self._data)
        )

    def _link_pl_pp(self, *, edited: str) -> None:
        """Reconcile Power Level and the point budget after one of them changed.

        Editing Power Level snaps the budget to that level's band; editing the budget
        re-derives Power Level. Only the *other* field is updated, silently, so the two
        never fight in a signal loop. An NPC has no budget to reconcile against, so
        this does nothing there — its Power Level is whatever the GM says it is.
        """
        if self._npc:
            return
        if edited == "power_level":
            new_pp = reconcile_points_to_level(
                self._character.power_level,
                self._character.power_points_total,
                self._data,
                self._character,
            )
            if new_pp != self._character.power_points_total:
                self._character.power_points_total = self._set_spin_silently(
                    self._power_points, "power_points", new_pp
                )
        else:
            new_pl = power_level_for_points(
                self._character.power_points_total, self._data, self._character
            )
            if new_pl != self._character.power_level:
                self._character.power_level = self._set_spin_silently(
                    self._power_level, "power_level", new_pl
                )

    def _set_spin_silently(self, spin: QSpinBox, key: str, value: int) -> int:
        """Set a spin box without re-triggering its handler; keep the model in step."""
        with QSignalBlocker(spin):
            spin.setValue(value)
            actual = spin.value()
        self._character.characteristics[key] = actual
        return actual

    def reseed(self) -> None:
        """Restate the bought characteristics from the model — an earlier state is back.

        Deliberately does **not** run :meth:`_link_pl_pp`: both Power Level and the
        point budget in a restored state are authoritative, and reconciling one
        against the other would rewrite it and turn the restore into a fresh edit.

        The derived readouts and the homebrew notice arrive on ``derived-changed``,
        so this only touches the four widgets that hold model values directly.
        """
        self._loading = True
        try:
            for spin, key, fallback in (
                (self._power_level, "power_level", 10),
                (self._power_points, "power_points", 150),
            ):
                blocker = QSignalBlocker(spin)
                spin.setValue(int(self._seed(key, fallback)))
                del blocker
            blocker = QSignalBlocker(self._size_combo)
            size = str(self._seed("size", "Medium"))
            if self._size_combo.findText(size) >= 0:
                self._size_combo.setCurrentText(size)
            del blocker
            # set_value is silent by design, so the "spent/gained" baseline has to be
            # moved by hand — otherwise the next pip click writes a note about a jump
            # the player never made.
            self._hero_points.set_value(int(self._seed("hero_points", 1)))
            self._last_hero_points = self._hero_points.value()
        finally:
            self._loading = False

    def _emit_edited(self) -> None:
        if not self._loading:
            self.edited.emit()

    # -- derived readouts -----------------------------------------------------

    def set_pool_current(self, key: str, value: object) -> None:
        """Update the power-point pool's calculated *spent* value (player sheets only).

        NPCs carry no budget, so the sheet skips this for them and shows an estimated
        Power Level instead (:meth:`refresh_estimated_pl`).
        """
        if key != "power_points":
            return
        self._pool_current.setText(str(value))
        self._restate_pool_balance()

    def _restate_pool_balance(self) -> None:
        """Tint the spent half of the pool when the build has outrun its budget.

        ``170 / 150`` used to read in the same ink as ``140 / 150``, so the one number on
        the sheet that says the character cannot legally be played said it in a whisper.
        Over budget is a *build* error rather than a rules one — no cap has been broken,
        the points simply are not there — so it is tinted rather than badged, and the
        exact arithmetic goes on the tooltip either way, which is the question a player
        actually has of this row ("how many have I got left?").
        """

        try:
            spent = int(self._pool_current.text())
        except ValueError:  # the "—" placeholder, before the first fan-out
            self._pool_current.setStyleSheet("")
            self._pool_current.setToolTip("Spent — calculated from the build")
            return
        total = self._power_points.value()
        over = spent - total
        self._pool_current.setStyleSheet(tinted_style("tint.warning") if over > 0 else "")
        left = f"{over} over budget" if over > 0 else f"{-over} left"
        self._pool_current.setToolTip(f"Spent — calculated from the build ({left})")

    def refresh_estimated_pl(self) -> None:
        """Recompute the NPC sheet's estimated Power Level from the build's traits.

        An NPC is not budgeted, so its Power Level is read from what it can do —
        the smallest level its Resistances and best attack stay legal under
        (:func:`~mm_companion.core.rules.estimated_power_level`) — rather than from
        spent points. Cheap and model-only, so the sheet calls it on any build change.
        """
        level = estimated_power_level(self._character, self._data)
        # No "PL" prefix: the row's own caption already says Estimated PL.
        self._estimated_pl.setText(str(level))

    def set_npc_mode(self, npc: bool) -> None:
        """Swap the point budget for an estimated Power Level, or back.

        A GM's NPC is an ordinary character without the accounting: nobody spends
        points on a thug, so the pool row would only ever be a number to ignore.
        In its place the row reads back an *estimated* Power Level derived from the
        NPC's traits — its Resistances and best attack (:meth:`refresh_estimated_pl`).
        The editable Power Level field above it is dropped too: an NPC is not built
        to a target level, so the single Estimated PL readout — what the traits
        actually add up to — is the only Power Level the sheet shows. Hero points
        are a player's currency, so that row goes as well.
        """
        self._npc = npc
        for widget in (self._pool_current, self._pool_separator, self._power_points):
            widget.setVisible(not npc)
        self._estimated_pl.setVisible(npc)
        label = self._form.labelForField(self._points_row)
        if isinstance(label, QLabel):
            label.setText("Estimated PL:" if npc else "Power Points:")
        # Drop the rows that only make sense for a budgeted player character: the
        # bought Power Level (replaced by the estimate) and Hero Points.
        self._set_row_visible(self._power_level, not npc)
        self._set_row_visible(self._hero_points, not npc)
        self._refresh_limits()  # ...and with it the limits read off that level
        self._refresh_cost_notice()
        if npc:
            self.refresh_estimated_pl()

    def _set_row_visible(self, field: QWidget, visible: bool) -> None:
        """Show or hide a whole form row — the field, its caption, and the row itself.

        ``QFormLayout.setRowVisible`` rather than hiding the two widgets by hand, which
        is what this did. Hiding a widget takes it off the screen but leaves its **row**
        in the layout, spacing and all, so every absent row left a blank band behind it —
        and this block hides four of them (Reach and Movement on almost every sheet, plus
        Power Level and Hero Points on an NPC), which is why the gaps read as a
        mis-spaced block rather than as a missing row.

        The widgets are still hidden alongside, because ``visibleWidgets`` is what the
        tests and the rest of the block ask about, and a hidden row does not otherwise
        change what ``isVisible`` reports for its members.
        """

        field.setVisible(visible)
        label = self._form.labelForField(field)
        if label is not None:
            label.setVisible(visible)
        row, _role = self._form.getWidgetPosition(field)
        if row >= 0:
            self._form.setRowVisible(row, visible)

    def refresh_derived(self) -> None:
        """Recompute the derived readouts: speed lines, initiative, effective size.

        Reads the model only, so it never emits ``changed`` — safe to call from any
        cross-block signal. Active conditions overlay the speed and initiative readouts
        (a slowing/immobilising condition on ground speed, a check penalty on initiative)
        the same display-only way the stat grids show them — the build math is untouched.
        """
        self._refresh_effort_note()
        self._refresh_limits()
        self._speed.render_lines(condition_speed_lines(self._character, self._data))
        self._speed.setToolTip(
            _speed_condition_tooltip(condition_speed_rank_mod(self._character, self._data))
        )

        altered = reach_is_altered(self._character, self._data)
        if altered:
            self._reach.setText(reach_text(character_reach(self._character, self._data)))
        self._set_row_visible(self._reach, altered)  # unchanged reach, no row at all

        modes = movement_mode_lines(self._character, self._data)
        self._movement_modes.render_lines(modes)
        self._set_row_visible(self._movement_modes, bool(modes))  # no modes, no row at all

        modifier = initiative_modifier(self._character, self._data)
        ability = initiative_ability(self._character, self._data)
        penalty = condition_check_penalty(self._character, self._data)
        net = modifier + penalty
        if penalty:
            worse = theme.color("tint.worse")
            self._initiative.setText(f'<span style="color: {worse};">{net:+d}</span> ({ability})')
            self._initiative.setToolTip(
                f"{modifier:+d} base {penalty:+d} from an active condition on all checks"
                f"\n{ROLL_TOOLTIP}"
            )
        else:
            self._initiative.setText(f"{net:+d} ({ability})")
            self._initiative.setToolTip(INITIATIVE_TIP)

        effective = effective_size(self._character, self._data)
        base = str(self._character.characteristics.get("size", "Medium"))
        self._size_effective.setText(f"→ {effective}" if effective != base else "")

        if self._npc:
            self.refresh_estimated_pl()  # traits may have shifted the estimate
        self._refresh_cost_notice()

    def set_locked(self, locked: bool) -> None:
        """Turn the editable fields into read-only labels (locked) or back."""
        self._locked = locked
        self._apply_lock()

    def set_budget_fixed(self, fixed: bool) -> None:
        """Show the Power Level and point budget read-only, for a *derived* budget.

        A power's sub-build — a Summon's minion, a Metamorph form — is built on a
        number the power hands it: ``rank x 15``, or its wielder's own total
        (:mod:`mm_companion.core.rules.subbuilds`). Both are restamped every time the
        build is opened, so leaving the spin boxes live would offer an edit that
        silently does not stick. The rows stay *visible* — the budget is the whole
        point of the window — they simply cannot be typed into.

        Sticky, unlike :meth:`set_locked`: unlocking the sheet must not hand back a
        field that was never the player's.
        """
        self._budget_fixed = fixed
        self._apply_lock()

    def _apply_lock(self) -> None:
        """Re-apply the lock state to every editable field."""
        derived = (self._power_level, self._power_points)
        for widget in self._editable:
            fixed = self._budget_fixed and widget in derived
            set_widget_locked(widget, self._locked or fixed)
