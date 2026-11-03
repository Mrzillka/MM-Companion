"""The power cards' small widgets: the group mode toggle, the split group, the rank dial
and the effect selector.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Qt,
    Signal,
)
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_INDEPENDENT,
    STRUCTURE_LINKED,
)
from mm_companion.ui import theme
from mm_companion.ui.power_constructor.canvas import MODE_ARRAY_DYNAMIC
from mm_companion.ui.rank_pips import RankPips
from mm_companion.ui.sections.power_parts.helpers import _share_index
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import (
    muted_style,
    tinted_style,
)


def _mode_toggle_style(locked: bool) -> str:
    """The mode switch's own stylesheet, in tokens every preset defines.

    Stated **on the widget**, not in the theme's application sheet: Classic emits no
    widget chrome, so an app-level rule would exist under some presets only; and a
    styled preset states ``QPushButton``'s box, so ``QStyleSheetStyle`` stops
    painting the platform's checked panel. With no ``:checked`` rule, the lit segment
    looked like its neighbours and a group card gave no sign of its mode. Only tokens
    *every* preset defines are used (Classic has no ``surface.*`` group).

    The resting border is drawn so a segment does not jump as it lights up. Weight
    only, no ``font-size``.
    """
    accent = theme.color("accent")
    rest = (
        "QPushButton {"
        " background: transparent;"
        f" color: {theme.color('text.muted')};"
        f" border: {int(theme.metric('border.width'))}px solid {theme.color('border.card')};"
        f" border-radius: {int(theme.metric('radius.chip'))}px;"
        f" padding: {int(theme.metric('space.xs'))}px {int(theme.metric('space.sm'))}px; }}"
    )
    lit = (
        "QPushButton:checked {"
        f" background: {accent};"
        f" color: {theme.color('text.on-badge')};"
        f" border-color: {accent};"
        " font-weight: bold; }"
    )
    if locked:
        # A locked switch is a read-out, so it sheds the hover cue along with the
        # rest of its input chrome; set_locked hides the unlit segments entirely.
        return f"{rest}\n{lit}"
    hover = f"QPushButton:hover {{ border-color: {accent}; color: {accent}; }}"
    return f"{rest}\n{hover}\n{lit}"


def _lit_width(button: QPushButton) -> int:
    """How wide *button* has to be to hold its label once that label lights up.

    Only the checked segment is bold, and a size hint measured from the resting
    font is a few pixels short of the bold one — which showed up as a clipped
    "Independen" the moment that segment was the mode in force. Every segment gets
    the same allowance, so lighting one up never re-widths the strip either.

    The bold *delta* is added to the hint rather than the bold advance replacing
    it: the hint already carries the stylesheet's padding, the border and Qt's own
    margins, and none of those are worth re-deriving here.
    """
    font = button.font()
    bold = QFont(font)
    bold.setBold(True)
    grew = QFontMetrics(bold).horizontalAdvance(button.text()) - QFontMetrics(
        font
    ).horizontalAdvance(button.text())
    return button.sizeHint().width() + max(0, grew)


class _ModeToggle(QWidget):
    """A segmented Independent / Array / Dynamic array / Linked switch for a group.

    Mirrors the Power Constructor's mode bar (the same four choices for how parts
    combine), but scoped to whole cards in a group rather than one power's effects.
    Emits :attr:`modeChanged` with a structure id — or
    :data:`~mm_companion.ui.power_constructor.canvas.MODE_ARRAY_DYNAMIC` — when the user
    picks a segment.

    The lit segment *is* how the card reports its group's mode, so it states its own
    look rather than trusting the platform to paint a checked button — see
    :func:`_mode_toggle_style`. Locked, it goes on saying which mode is in force and
    stops being a control: :meth:`set_locked`.
    """

    modeChanged = Signal(str)

    _MODES = (
        (STRUCTURE_INDEPENDENT, "Independent", "Members act on their own; their costs add up."),
        (
            STRUCTURE_ARRAY,
            "Array",
            "One member active at a time; the costliest is paid in "
            "full and each other is a flat-cost alternate.",
        ),
        (
            MODE_ARRAY_DYNAMIC,
            "Dynamic array",
            "The members share the array's points and run at the same time at reduced "
            "effectiveness, instead of switching each other off. Each alternate costs "
            "the dearer Dynamic price, and the split is made on the cards' rank scales.",
        ),
        (STRUCTURE_LINKED, "Linked", "Members always activate together as one; costs add up."),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(theme.metric("space.xs")))
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for mode, label, tip in self._MODES:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setFixedHeight(int(theme.metric("column.mode-toggle")))
            button.setMinimumWidth(_lit_width(button))
            self._group.addButton(button)
            self._buttons[mode] = button
            row.addWidget(button)
        self._group.buttonClicked.connect(self._on_clicked)
        self.set_locked(False)

    def _on_clicked(self, button: QPushButton) -> None:
        for mode, candidate in self._buttons.items():
            if candidate is button:
                self.modeChanged.emit(mode)
                return

    def set_mode(self, mode: str) -> None:
        """Reflect a mode into the buttons without emitting :attr:`modeChanged`."""
        (self._buttons.get(mode) or self._buttons[STRUCTURE_INDEPENDENT]).setChecked(True)

    def set_locked(self, locked: bool) -> None:
        """Read-only view: keep the mode legible, drop the switch.

        Locking is *not* ``setEnabled(False)`` anywhere in this app, and a greyed
        strip of three segments is exactly how a group's mode became unreadable — so
        the two unlit segments go away instead and the lit one stays on as a static
        chip naming the mode. It is left transparent to the mouse rather than
        disabled, so a click on it falls through to the group card the way a click
        anywhere else on the title bar does; the card is the switch. Call this
        *after* :meth:`set_mode`, or there is no lit segment yet to keep.
        """
        self.setStyleSheet(_mode_toggle_style(locked))
        for button in self._buttons.values():
            button.setVisible(not locked or button.isChecked())
            button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, locked)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus if locked else Qt.FocusPolicy.StrongFocus)
            button.setCursor(
                Qt.CursorShape.ArrowCursor if locked else Qt.CursorShape.PointingHandCursor
            )


class _SplitGroup:
    """Keeps one array's share dials honest about each other, live.

    Every member draws from the same pool, so moving one changes what the others may
    take. Each dial reports its notch (:attr:`_RankDial.previewed`) and this restates
    the rest: each other dial's ceiling becomes what the pool has left, and the header
    says what is unspent. Nothing here writes to the model.

    A **phantom** entry is a member seated on a share it does not hold (an unsplit
    array's running alternate, :meth:`PowersSection._fallback_share`). It counts as
    nothing until the player picks another notch; otherwise the first split of an
    untouched array would find the pool already eaten.
    """

    __slots__ = ("_pool", "_entries", "_readout")

    def __init__(self, pool: int) -> None:
        self._pool = max(0, pool)
        # (dial, points-per-notch, phantom seat) per member, in card order. The seat is
        # -1 for a member that really holds its share, which no dial value ever equals.
        self._entries: list[tuple[_RankDial, list[int], int]] = []
        self._readout = None

    def add(self, dial: _RankDial, steps: list[int], phantom: bool = False) -> None:
        self._entries.append((dial, steps, dial.value() if phantom else -1))
        dial.previewed.connect(lambda _v: self.restate())

    def set_readout(self, label) -> None:
        self._readout = label

    def _held(self) -> list[int]:
        """What each member is holding *on screen* — the handle, not the model."""

        return [
            0 if dial.value() == seat else steps[min(dial.value(), len(steps) - 1)]
            for dial, steps, seat in self._entries
        ]

    def restate(self) -> None:
        """Re-bound every dial against the others and restate the header."""

        held = self._held()
        total = sum(held)
        for index, (dial, steps, _seat) in enumerate(self._entries):
            budget = self._pool - (total - held[index])
            ceiling = _share_index(steps, budget)
            # Never below what this member already holds: a rebuild can move the pool
            # under a split already made, and a ceiling that cut into a stored share
            # would silently spend it the moment the dial was touched.
            seated = min(dial.value(), len(steps) - 1)
            dial.set_ceiling(max(ceiling, seated))
        if self._readout is not None:
            self._readout.setText(self.readout_text(total, self._pool))
            over = total > self._pool
            self._readout.setStyleSheet(tinted_style("tint.warning") if over else muted_style())

    @staticmethod
    def readout_text(assigned: int, pool: int) -> str:
        """What the group header says about the pool — before a split and during one.

        Static because the header is built before the dials exist
        (:meth:`PowersSection._pool_readout`) and restated here once they do, so one
        function writes the sentence. An unsplit array says how many points there are
        and that none are spoken for.
        """

        if assigned <= 0:
            return f"Pool: {pool} PP \u2014 not split"
        left = pool - assigned
        text = f"{assigned}/{pool} PP split"
        return text if left <= 0 else f"{text} \u00b7 {left} left"


class _RankDial(QWidget):
    """A scale of pips for the rank an effect is currently *held at*, and what that means.

    A Growth 3 is Large, then Huge, then Gargantuan, and which you stand at is a
    mid-fight decision; a hero pulling their punches fires a Damage 10 at 5. So the
    card carries one pip per rank (:class:`~mm_companion.ui.rank_pips.RankPips`) and a
    label saying what the notch *is*: the **size the character becomes** for a size
    effect (read against the wielder, so a Small character starts at Medium), the
    rank otherwise. Ranks the Size Table clamps together repeat their category, and
    the scale groups them with a wider gap.

    * **Zero is off.** Every pip is hollow while the power is off, and clicking the
      **last lit pip puts it out**, as clicking the card would; any other lit pip
      steps down. Picking a pip on a dormant power wakes it at that notch.
    * **Hover names, click commits.** Nothing reaches the section until a click.
    * **NoFocus**: committing destroys the card, focus would land on the next widget
      in the tab order, and a ``QScrollArea`` scrolls to show it — the page jumped
      away from the card under the cursor.
    * **Live in the locked sheet**: how far a power is turned up is a play action.
      Inside a switched-off Linked group it goes transparent to the mouse instead
      (never ``setEnabled(False)``).
    """

    rankPicked = Signal(int)  #: the effect rank the player settled on (0 = switch off)
    #: The notch the dial has just been set to, emitted ahead of :attr:`rankPicked`.
    #: Nothing is written for it — it is what lets a Dynamic array's other dials restate
    #: their ceilings, and its header its remaining points.
    previewed = Signal(int)

    def __init__(
        self,
        caption: str,
        maximum: int,
        current: int,
        labels: dict[int, str],
        interactive: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._labels = labels
        maximum = max(0, maximum)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(theme.metric("space.sm")))

        self._caption_text = caption
        caption_label = QLabel(caption)
        caption_label.setStyleSheet(muted_style())
        row.addWidget(caption_label)

        self._pips = RankPips(maximum, max(0, min(maximum, current)), labels, interactive)
        self._pips.picked.connect(self.pick)
        self._pips.hovered.connect(self._on_hovered)
        row.addWidget(self._pips)

        self._value = QLabel()
        row.addWidget(self._value)
        row.addStretch(1)
        self._show(self._pips.value())

    def caption(self) -> str:
        """What this dial is *of* — "Size", "Share", or the effect's own name."""

        return self._caption_text

    def value(self) -> int:
        """The notch the dial stands on."""

        return self._pips.value()

    def ceiling(self) -> int:
        """The highest notch currently reachable."""

        return self._pips.ceiling()

    def set_ceiling(self, ceiling: int) -> None:
        """End the reachable part of the scale at the highest notch currently affordable.

        For a Dynamic member: the last hollow pip is the most it can be set to, and
        the dotted ones beyond are rungs its siblings' shares have spent. The **index
        space does not move**: what is affordable is always a prefix of the ladder.

        Never below the notch the dial is seated on. A rebuild can move the pool under a
        split already made, and an end that cut into a stored share would spend it the
        moment the dial was touched.
        """

        ceiling = max(0, min(ceiling, len(self._labels) - 1))
        self._pips.set_ceiling(max(ceiling, self._pips.value()))

    def pick(self, notch: int) -> None:
        """Set the dial to *notch* and report it — what a click on a pip does."""

        notch = max(0, min(notch, self.ceiling()))
        if notch == self._pips.value():
            return
        self._pips.set_value(notch)
        self._show(notch)
        self.previewed.emit(notch)
        self.rankPicked.emit(notch)

    def _label_for(self, rank: int) -> str:
        return self._labels.get(rank, f"Rank {rank}")

    def _show(self, notch: int) -> None:
        """Say what *notch* is — "Off" at zero, since no pip is lit to say it."""

        self._value.setText(self._label_for(notch))

    def _on_hovered(self, notch: int) -> None:
        self._show(notch if notch >= 0 else self._pips.value())


class _EffectSelector(QWidget):
    """Which effect of an array *power* is currently in use.

    One level down from clicking an array **group's** member: a power whose own
    effects are an array runs one at a time, and there is no card per effect to
    click. A combo, since effect names do not fit across a card as segments. Like the
    rank dial it stays live in the locked sheet and takes no focus, because
    committing rebuilds the card.
    """

    effectPicked = Signal(int)

    def __init__(
        self,
        titles: list[str],
        current: int,
        interactive: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(theme.metric("space.sm")))

        caption = QLabel("Using")
        caption.setStyleSheet(muted_style())
        row.addWidget(caption)

        self._combo = QComboBox()
        self._combo.addItems(titles)
        self._combo.setCurrentIndex(max(0, min(current, len(titles) - 1)))
        self._combo.setToolTip(
            "Only one effect of an array runs at a time. That is why an array costs less than the "
            "same effects bought separately. The others add nothing until picked."
        )
        self._combo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not interactive)
        self._combo.setCursor(
            Qt.CursorShape.PointingHandCursor if interactive else Qt.CursorShape.ArrowCursor
        )
        guard_wheel(self._combo)
        self._combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(self._combo)
        row.addStretch()

        # Connected after the initial index, so seeding never reads as a choice.
        self._combo.currentIndexChanged.connect(self.effectPicked)
