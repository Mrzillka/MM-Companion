"""The System / Power Level block: the non-purchasable game characteristics.

Split out of the former base-info block: power level, the power-point pool, size,
speed, initiative, and hero points. Power level and the point pool feed the build
(``changed``); the rest are derived readouts or descriptive edits (``edited``).

Speed, initiative, and effective size are *derived* — computed in
:mod:`mm_companion.core.rules` from abilities, advantages, and active powers — so
this block exposes :meth:`refresh_derived` for the sheet to call whenever those
inputs change. The widgets never compute rules themselves.
"""

from __future__ import annotations

from html import escape

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
    power_level_for_points,
    reconcile_points_to_level,
    speed_columns,
)
from mm_companion.ui import theme
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
        for i in range(HERO_POINT_PIPS):
            button = QPushButton()
            button.setFlat(True)
            button.setFixedSize(self._pip_size, self._pip_size)
            button.setIconSize(QSize(self._pip_size, self._pip_size))
            button.setStyleSheet(style)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, index=i: self._on_click(index))
            self._buttons.append(button)
            row.addWidget(button)
        row.addStretch()
        self._render()

    def value(self) -> int:
        return len(self._lit)

    def set_value(self, value: int) -> None:
        """Set the count (clamped to 0…5) without emitting :attr:`valueChanged`.

        The caller has a number, not an arrangement, so this *reconciles* to it:
        spending puts out the right-most lit pips, gaining lights the left-most
        dark ones. A player who lit only the fourth pip and then spends a point
        still ends up where they expect, and a load with nothing to preserve
        settles on the left-most ``value`` pips.
        """
        target = max(0, min(HERO_POINT_PIPS, int(value)))
        while len(self._lit) > target:
            self._lit.discard(max(self._lit))
        while len(self._lit) < target:
            self._lit.add(min(set(range(HERO_POINT_PIPS)) - self._lit))
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
    an active movement power adding its own line. The unit button flips every line
    between the imperial per-round distance and the km/h equivalent.
    """

    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = data
        self._metric = False
        self._lines: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self._lines_label = QLabel()
        self._lines_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._lines_label)

        self._unit_button = QPushButton()
        self._unit_button.setCheckable(False)
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
        # Rich text can't use a Qt palette() role, so a condition-affected line takes
        # the literal tint colour. Resolved once per redraw rather than per line.
        worse = theme.color("tint.worse")
        html_lines = []
        for line in self._lines:
            if line.immobilised:
                label = escape(line.label)
                html_lines.append(f'<span style="color: {worse};">{label}: immobilised</span>')
                continue
            walk, dash, run = speed_columns(line.rank, self._data, metric=self._metric)
            text = f"{line.label}: {_compact(walk)} / {_compact(dash)} / {_compact(run)}"
            if line.rank_mod:
                text += f" ({line.rank_mod:+d} rank)"
                html_lines.append(f'<span style="color: {worse};">{escape(text)}</span>')
            else:
                html_lines.append(escape(text))
        self._lines_label.setText("<br>".join(html_lines))

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
        layout.setSpacing(1)
        self.setVisible(False)

    def render_lines(self, lines: list) -> None:
        layout = self.layout()
        while layout.count():  # rebuilt wholesale — a mode list is a handful of rows
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for line in lines:
            text = f"{line.label}: {_compact(speed_columns(line.rank, self._data)[0])}/round"
            if line.note:
                text += f" ({line.note})"
            label = QLabel(text)
            label.setWordWrap(True)
            if line.description:
                label.setToolTip(line.description)
            layout.addWidget(label)
        self.setVisible(bool(lines))


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


def _speed_condition_tooltip(speed_mod: int | None) -> str:
    """Explain a condition's ground-speed overlay in the speed widget's tooltip."""
    if speed_mod is None:
        return "Immobilised by an active condition — no ground movement."
    if speed_mod:
        return f"Ground speed slowed by an active condition ({speed_mod:+d} rank)."
    return ""


class SystemInfoSection(QGroupBox):
    """Power level, points pool, size, speed, initiative, and hero points.

    Emits :attr:`changed` when an edit affects the point build (power level / points)
    and :attr:`edited` on any user edit for unsaved-change tracking.
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
    #: A sentence for the roll history — a hero point spent or gained. Carries the
    #: text, since the block that writes it down cannot see what changed here.
    noteRequested = Signal(str)
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

        form = self._form = QFormLayout(self)
        form.addRow("Power Level:", self._build_power_level())
        form.addRow("Power Points:", self._build_power_points())
        form.addRow(self._build_cost_notice())
        form.addRow("Size:", self._build_size())
        form.addRow("Speed:", self._build_speed())
        self._movement_row_label = QLabel("Movement:")
        form.addRow(self._movement_row_label, self._build_movement_modes())
        form.addRow("Initiative:", self._build_initiative())
        form.addRow("Hero Points:", self._build_hero_points())

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
        self._size_combo.currentTextChanged.connect(self._on_size_changed)
        guard_wheel(self._size_combo)
        # Effective size when an active Growth/Shrinking shifts it away from the base.
        self._size_effective = QLabel()
        self._size_effective.setStyleSheet(muted_style())
        row.addWidget(self._size_combo)
        row.addWidget(self._size_effective)
        row.addStretch()
        self._editable.append(self._size_combo)
        return container

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
        self._refresh_cost_notice()
        if npc:
            self.refresh_estimated_pl()

    def _set_row_visible(self, field: QWidget, visible: bool) -> None:
        """Show or hide a whole form row — the field and its caption label."""
        field.setVisible(visible)
        label = self._form.labelForField(field)
        if label is not None:
            label.setVisible(visible)

    def refresh_derived(self) -> None:
        """Recompute the derived readouts: speed lines, initiative, effective size.

        Reads the model only, so it never emits ``changed`` — safe to call from any
        cross-block signal. Active conditions overlay the speed and initiative readouts
        (a slowing/immobilising condition on ground speed, a check penalty on initiative)
        the same display-only way the stat grids show them — the build math is untouched.
        """
        self._speed.render_lines(condition_speed_lines(self._character, self._data))
        self._speed.setToolTip(
            _speed_condition_tooltip(condition_speed_rank_mod(self._character, self._data))
        )

        modes = movement_mode_lines(self._character, self._data)
        self._movement_modes.render_lines(modes)
        self._movement_row_label.setVisible(bool(modes))  # no modes, no row at all

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
        for widget in self._editable:
            set_widget_locked(widget, locked)
