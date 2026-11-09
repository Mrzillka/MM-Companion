"""The roller's action lane: one line per action picked off the Actions popup.

It sits under the Bonus/Penalty slider. Each :class:`ActionLine` is an action that
rides on the next roll: its name, a ``✕`` to cancel it, then either a slider and spin
box over ``0..X_max`` (a configurable action, All-Out Attack +1..+5) or a fixed caption
(``"roll twice"``). The roll spends the lines it used, as it spends the slider.

**The lane takes no room while it is empty**: it hides, and the roller's Actions
button, under it, moves up into its row. It used to hold one line's height open so the
first action never resized the roller, but the blank row read as a gap (the user's
call, 2026-11-09): the roller now grows a row when the first line comes on.

Rules-free like the rest of the panel: the limit of a configurable line comes from a
callable the host hands in (``limit(spec) -> (maximum, reason)``), and the panel decides
only whether the loaded roll is an attack at all.

**Lines limit each other.** Two maneuvers that both raise the attack check share the
room to double it, so a limit may read the other lines' values (the host's callable
asks the panel). Whenever a line's value moves, or a line comes or goes, the lane works
the others' limits out again (:meth:`ActionLine.relimit`): their ranges follow, and a
value past its new maximum is pulled down to it — never pushed up, which is what keeps
two lines from chasing each other.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.rules import RollSpec, same_attack
from mm_companion.ui import theme
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import (
    ElidingLabel,
    discard_widget,
    make_spin_box,
    muted_style,
    tinted_style,
)

#: ``limit(spec) -> (maximum, reason)``: how far a configurable line may go for the
#: attack *spec* (``None`` when no attack is loaded), and why not when that is 0.
LineLimit = Callable[[RollSpec | None], tuple[int, str]]

#: ``shift(spec, lines) -> spec``: what the lines that apply (``(key, value)`` pairs) do
#: to the spec they ride on — the host's, since the panel reads no game data.
LineShift = Callable[[RollSpec, Sequence[tuple[str, "int | None"]]], RollSpec]

#: ``fits(spec) -> reason``: why a fixed line cannot ride on the attack *spec* (``""``
#: when it can) — Aim on an attack that makes no attack check.
LineFit = Callable[[RollSpec | None], str]

#: ``describe(value) -> caption``: what a configurable line's value trades, in words —
#: "+3 attack, −3 Defense" — shown under its slider (``""`` for nothing).
LineDescribe = Callable[[int], str]

#: Why a line that needs an attack is greyed out while none is loaded.
NEEDS_ATTACK = "Load an attack first: pick one off the Actions popup or a power card."


class ActionLine(QWidget):
    """One action riding on the next roll (see the module docstring)."""

    #: The ``✕`` was pressed: this line's key.
    removed = Signal(str)
    #: The value was moved (by the player, or by a limit pulling it down): this key.
    valueChanged = Signal(str)

    def __init__(
        self,
        key: str,
        name: str,
        *,
        limit: LineLimit | None = None,
        fixed: str = "",
        needs_attack: bool = False,
        tooltip: str = "",
        tone: str = "tint.better",
        fits: LineFit | None = None,
        describe: LineDescribe | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self._describe = describe
        self._fits = fits
        self.name = name
        self.needs_attack = needs_attack
        self._limit = limit
        self._tone = tone
        self._available = True
        self._reason = ""
        #: The attack this line's limit was last worked out for; a different one
        #: resets the value to its new maximum.
        self.bound_spec: RollSpec | None = None
        # Greyed for want of an attack (see :meth:`bind`): nothing to work a limit for.
        self._unloaded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(theme.metric("space.xs")))
        outer.addLayout(row)

        self._name = ElidingLabel(name)
        self._name.setStyleSheet(tinted_style(tone, bold=True))
        self._name.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        row.addWidget(self._name)

        self._close = QPushButton("✕")
        self._close.setFixedWidth(int(theme.metric("column.chip-button")))
        self._close.setToolTip(f"Cancel {name}" if name else "")
        self._close.clicked.connect(lambda: self.removed.emit(self.key))
        row.addWidget(self._close, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._slider: QSlider | None = None
        self._spin: QSpinBox | None = None
        self._caption: QLabel | None = None
        #: What a configurable line's value trades, under its slider — see *describe*.
        self._trade: ElidingLabel | None = None
        if limit is not None:
            self._slider = QSlider(Qt.Orientation.Horizontal)
            self._slider.setRange(0, 0)
            guard_wheel(self._slider)
            self._spin = make_spin_box(0, 0, buttons=False, snug=True)
            self._slider.valueChanged.connect(self._spin.setValue)
            self._spin.valueChanged.connect(self._slider.setValue)
            self._spin.valueChanged.connect(lambda _v: self.valueChanged.emit(self.key))
            row.addWidget(self._slider, stretch=1)
            row.addWidget(self._spin)
            if describe is not None:
                # Under the slider rather than beside it: the strip is narrow, and the
                # trade is a sentence, not a number. Elided, so it never widens the roller.
                self._trade = ElidingLabel("")
                self._trade.setAlignment(Qt.AlignmentFlag.AlignRight)
                self._trade.setStyleSheet(muted_style())
                outer.addWidget(self._trade)
                self._spin.valueChanged.connect(self._sync_trade)
                self._sync_trade()
        else:
            self._caption = QLabel(fixed)
            self._caption.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            # As tall as a configurable line's spin box, so swapping one kind of line
            # for the other never moves the roller.
            # A spin box's height is chrome, the same for every character.
            probe = make_spin_box(0, 0, buttons=False, guarded=False)
            probe.setParent(self)
            self._caption.setMinimumHeight(probe.sizeHint().height())
            discard_widget(probe)
            row.addWidget(self._caption, stretch=1)
        self._base_tooltip = tooltip
        self.setToolTip(tooltip)

    # -- state ----------------------------------------------------------------

    def _sync_trade(self) -> None:
        if self._trade is None or self._describe is None or self._spin is None:
            return
        text = self._describe(self._spin.value())
        self._trade.setText(text)
        self._trade.setToolTip(text)

    def trade(self) -> str:
        """The caption under the slider — what the value trades — or ``""``."""
        return self._trade.text() if self._trade is not None else ""

    @property
    def configurable(self) -> bool:
        return self._spin is not None

    @property
    def available(self) -> bool:
        """Whether the line applies to the roll as things stand (not greyed out)."""
        return self._available

    @property
    def reason(self) -> str:
        """Why the line is greyed out, or ``""``."""
        return self._reason

    def value(self) -> int | None:
        """The amount set (a configurable line), or ``None`` (a fixed one)."""
        return None if self._spin is None else self._spin.value()

    def maximum(self) -> int | None:
        return None if self._spin is None else self._spin.maximum()

    def set_value(self, value: int) -> None:
        if self._spin is not None:
            self._spin.setValue(value)

    def bind(self, spec: RollSpec | None) -> None:
        """Work the line out again for the attack *spec* (``None``: no attack loaded).

        A line that needs an attack greys out without one, and keeps its place: loading
        an attack lights it again. A **different** attack recomputes the limit and
        resets the value to the new maximum (the slider starts at the largest value
        allowed); the same one — the aimed copy of it included
        (:func:`~mm_companion.core.rules.same_attack`) — keeps the value the player set,
        pulled down only if it no longer fits.
        """
        # The attack it was set for is remembered through an unload, so loading the
        # same one again keeps the value the player set.
        self._unloaded = self.needs_attack and spec is None
        if self._unloaded:
            self._set_available(False, NEEDS_ATTACK)
            return
        if self._limit is None:
            self.bound_spec = spec
            reason = self._fits(spec) if self._fits is not None else ""
            self._set_available(not reason, reason)
            return
        fresh = not same_attack(spec, self.bound_spec)
        self.bound_spec = spec
        self._relimit(reset=fresh)

    def relimit(self) -> None:
        """Work the limit out again for the attack the line is bound to — another line
        moved. The value is pulled down to a lower maximum, never pushed up."""
        if self._limit is None or self._unloaded:
            return
        self._relimit(reset=False)

    def _relimit(self, *, reset: bool) -> None:
        maximum, reason = self._limit(self.bound_spec)
        maximum = max(0, int(maximum))
        if reset or self._spin.maximum() != maximum:
            value = maximum if reset else min(self._spin.value(), maximum)
            self._slider.setRange(0, maximum)
            self._spin.setRange(0, maximum)
            self._spin.setValue(value)
        self._set_available(maximum > 0, "" if maximum > 0 else reason)

    def _set_available(self, available: bool, reason: str) -> None:
        self._available = available
        self._reason = reason
        for widget in (self._name, self._slider, self._spin, self._caption, self._trade):
            if widget is not None:
                widget.setEnabled(available)
        self._name.setStyleSheet(tinted_style(self._tone, bold=True) if available else "")
        tip = self._base_tooltip
        if reason:
            tip = f"{tip}\n\n{reason}" if tip else reason
        self.setToolTip(tip)


class ActionLane(QWidget):
    """The column of :class:`ActionLine`\\ s under the slider, hidden while empty."""

    #: A line came or went, so the panel's height may have moved.
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lines: list[ActionLine] = []
        # Set while the lane re-limits or re-binds its lines, so the values those
        # passes move do not each set off another pass.
        self._settling = False
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(int(theme.metric("space.xs")))
        # Empty, so hidden (only ever hide() here: the lane may not have a parent yet).
        self.hide()

    def _show_lines(self) -> None:
        """Shown while it holds a line, hidden (no room at all) while it holds none.
        Only reached once the lane is in the roller, so it is never shown parentless."""
        if self.parentWidget() is not None:
            self.setVisible(bool(self._lines))

    def lines(self) -> list[ActionLine]:
        return list(self._lines)

    def line(self, key: str) -> ActionLine | None:
        return next((line for line in self._lines if line.key == key), None)

    def add(self, line: ActionLine) -> ActionLine:
        """Put *line* in the lane, replacing one of the same key (re-picking an action
        sets it afresh rather than stacking a second)."""
        old = self.line(line.key)
        if old is not None:
            index = self._lines.index(old)
            self._lines[index] = line
            self._column.replaceWidget(old, line)
            discard_widget(old)
        else:
            self._lines.append(line)
            self._column.addWidget(line)
        line.removed.connect(self.remove)
        line.valueChanged.connect(self._relimit_others)
        self._show_lines()
        self._relimit_others(line.key)
        self.changed.emit()
        return line

    def remove(self, key: str) -> None:
        line = self.line(key)
        if line is None:
            return
        self._lines.remove(line)
        self._column.removeWidget(line)
        discard_widget(line)
        self._show_lines()
        self._relimit_others(key)
        self.changed.emit()

    def clear(self) -> None:
        for line in list(self._lines):
            self.remove(line.key)

    def bind(self, spec: RollSpec | None) -> None:
        """Bind every line to *spec* (the loaded attack, or ``None``), then let them
        settle against each other once."""
        self._settling = True
        try:
            for line in self._lines:
                line.bind(spec)
        finally:
            self._settling = False
        self._relimit_others("")

    def _relimit_others(self, key: str) -> None:
        """The line *key* moved (or came, or went): work the others' limits out again."""
        if self._settling:
            return
        self._settling = True
        try:
            for line in self._lines:
                if line.key != key:
                    line.relimit()
            # A pass only ever pulls values down, which only loosens the others'
            # limits; a second one widens the ranges the first left narrower than
            # they need be, and moves no value.
            for line in self._lines:
                line.relimit()
        finally:
            self._settling = False
