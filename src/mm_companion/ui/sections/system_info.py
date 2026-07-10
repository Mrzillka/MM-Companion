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

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    effective_size,
    initiative_ability,
    initiative_modifier,
    power_level_for_points,
    reconcile_points_to_level,
    speed_columns,
    speed_lines,
)
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.sections.titled_section import strip_groupbox_caption
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box

HERO_POINT_CIRCLES = 5


class HeroPointsWidget(QWidget):
    """A row of five circles; clicking one spends or gains hero points.

    Clicking a circle sets the count to that circle's position, except clicking the
    last filled circle empties it (so the count can be lowered back to zero). Filled
    circles are ``●``, empty ones ``○``. Emits :attr:`valueChanged` on a user click.
    """

    valueChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = 0
        self._locked = False
        self._buttons: list[QPushButton] = []

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        for i in range(HERO_POINT_CIRCLES):
            button = QPushButton("○")
            button.setFlat(True)
            button.setFixedWidth(22)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, index=i: self._on_click(index))
            self._buttons.append(button)
            row.addWidget(button)
        row.addStretch()

    def value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        """Set the count (clamped to 0…5) without emitting :attr:`valueChanged`."""
        self._value = max(0, min(HERO_POINT_CIRCLES, int(value)))
        self._render()

    def _on_click(self, index: int) -> None:
        if self._locked:
            return
        # Clicking the last filled circle empties it; otherwise fill up to the click.
        new_value = index if self._value == index + 1 else index + 1
        if new_value == self._value:
            return
        self._value = new_value
        self._render()
        self.valueChanged.emit(self._value)

    def _render(self) -> None:
        for i, button in enumerate(self._buttons):
            button.setText("●" if i < self._value else "○")

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        for button in self._buttons:
            button.setCursor(
                Qt.CursorShape.ArrowCursor if locked else Qt.CursorShape.PointingHandCursor
            )


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
        self._lines_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._lines_label)

        self._unit_button = QPushButton()
        self._unit_button.setCheckable(False)
        self._unit_button.clicked.connect(self._toggle_unit)
        layout.addWidget(self._unit_button, alignment=Qt.AlignmentFlag.AlignLeft)

        self._sync_unit_button()

    def render_lines(self, lines: list) -> None:
        """Redraw the speed lines from the given :class:`SpeedLine` list."""
        self._lines = lines
        text_lines = []
        for line in lines:
            walk, dash, run = speed_columns(line.rank, self._data, metric=self._metric)
            text_lines.append(
                f"{line.label}: {_compact(walk)} / {_compact(dash)} / {_compact(run)}"
            )
        self._lines_label.setText("\n".join(text_lines))

    def _toggle_unit(self) -> None:
        self._metric = not self._metric
        self._sync_unit_button()
        self.render_lines(self._lines)

    def _sync_unit_button(self) -> None:
        self._unit_button.setText("Show ft / round" if self._metric else "Show km / h")


def _compact(label: str) -> str:
    """Shorten a distance label for the compact speed row (``"15 feet"`` → ``"15 ft"``)."""
    return label.replace(" feet", " ft").replace(" foot", " ft")


class SystemInfoSection(QGroupBox):
    """Power level, points pool, size, speed, initiative, and hero points.

    Emits :attr:`changed` when an edit affects the point build (power level / points)
    and :attr:`edited` on any user edit for unsaved-change tracking.
    """

    changed = Signal()
    edited = Signal()

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        strip_groupbox_caption(self)

        self._loading = True
        self._data = data
        self._character = character
        self._locked = False
        self._by_key = {c.key: c for c in data.characteristics}
        self._editable: list[QWidget] = []

        form = QFormLayout(self)
        form.addRow("Power Level:", self._build_power_level())
        form.addRow("Power Points:", self._build_power_points())
        form.addRow("Size:", self._build_size())
        form.addRow("Speed:", self._build_speed())
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
        self._power_points = make_spin_box(
            c.minimum if c else 0,
            c.maximum if c else 9999,
            value=int(self._seed("power_points", 150)),
        )
        self._power_points.setToolTip("Total available")
        self._power_points.valueChanged.connect(self._on_power_points_changed)
        row.addWidget(self._pool_current)
        row.addWidget(QLabel("/"))
        row.addWidget(self._power_points)
        row.addStretch()
        self._editable.append(self._power_points)
        return container

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
        self._size_effective.setStyleSheet("color: palette(mid);")
        row.addWidget(self._size_combo)
        row.addWidget(self._size_effective)
        row.addStretch()
        self._editable.append(self._size_combo)
        return container

    def _build_speed(self) -> QWidget:
        self._speed = SpeedWidget(self._data)
        return self._speed

    def _build_initiative(self) -> QWidget:
        self._initiative = QLabel("—")
        self._initiative.setToolTip("Agility (or an Alternate Initiative ability) plus advantages")
        return self._initiative

    def _build_hero_points(self) -> QWidget:
        self._hero_points = HeroPointsWidget()
        self._hero_points.set_value(int(self._seed("hero_points", 1)))
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
        self._character.characteristics["hero_points"] = value
        self._emit_edited()

    def _link_pl_pp(self, *, edited: str) -> None:
        """Reconcile Power Level and the point budget after one of them changed.

        Editing Power Level snaps the budget to that level's band; editing the budget
        re-derives Power Level. Only the *other* field is updated, silently, so the two
        never fight in a signal loop.
        """
        if edited == "power_level":
            new_pp = reconcile_points_to_level(
                self._character.power_level, self._character.power_points_total, self._data
            )
            if new_pp != self._character.power_points_total:
                self._character.power_points_total = self._set_spin_silently(
                    self._power_points, "power_points", new_pp
                )
        else:
            new_pl = power_level_for_points(self._character.power_points_total, self._data)
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
        """Update the power-point pool's calculated *spent* value."""
        if key == "power_points":
            self._pool_current.setText(str(value))

    def refresh_derived(self) -> None:
        """Recompute the derived readouts: speed lines, initiative, effective size.

        Reads the model only, so it never emits ``changed`` — safe to call from any
        cross-block signal.
        """
        self._speed.render_lines(speed_lines(self._character, self._data))

        modifier = initiative_modifier(self._character, self._data)
        ability = initiative_ability(self._character, self._data)
        self._initiative.setText(f"{modifier:+d} ({ability})")

        effective = effective_size(self._character, self._data)
        base = str(self._character.characteristics.get("size", "Medium"))
        self._size_effective.setText(f"→ {effective}" if effective != base else "")

    def set_locked(self, locked: bool) -> None:
        """Turn the editable fields into read-only labels (locked) or back."""
        self._locked = locked
        for widget in self._editable:
            set_widget_locked(widget, locked)
        self._hero_points.set_locked(locked)
