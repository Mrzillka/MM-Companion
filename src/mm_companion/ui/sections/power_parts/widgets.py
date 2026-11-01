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

    It is stated **here, on the widget**, and not as a rule in the theme's
    application sheet — the same bargain :mod:`~mm_companion.ui.lock` and the
    compact roller's overlay button strike, and here it is forced twice over.
    Classic emits no widget chrome at all, so an app-level rule would exist under
    some presets and not others; and a *styled* preset states ``QPushButton``'s box
    (border, radius, padding), which makes ``QStyleSheetStyle`` take the whole box
    over and stop painting the platform's sunken/checked panel. That is the bug this
    fixes: with no ``:checked`` rule anywhere, the lit segment painted exactly like
    its two neighbours, and a group card gave no sign of being Independent, Array or
    Linked. Only tokens *every* preset defines are used — Classic has no
    ``surface.*`` group.

    The resting border is drawn rather than dropped so a segment does not jump
    sideways as it lights up, which is the lesson the theme's tool-button rules and
    ``QuickRollStar`` already carry. No ``font-size`` here: weight only.

    Used by the group card's mode switch alone now that the size ladder has become a
    dial; it stays a shared helper because a segmented strip is the shape any future
    one-of-N card control wants, and two of them agreeing about nothing is the bug this
    docstring exists to prevent.
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

    **Dynamic** was a checkbox beside this strip, which asked the same question twice:
    an array and a Dynamic array are two answers to "how do these members combine", not
    one answer and a modifier on it.

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

    A split is one decision spread over several controls: every member draws from the
    same pool, so moving one changes what the others may take. Without something joining
    them each dial knew only its own bounds at the moment it was built, and the truth
    arrived a rebuild later — which is how a handle came to move because a *sibling* had,
    back when the dials were sliders.

    So each dial reports the notch it is set to (:attr:`_RankDial.previewed`) and this
    restates the rest of the array from that: each other dial's ceiling becomes what the
    pool has left once that one is paid, and the header says what is unspent. Nothing
    here writes to the model; the dial's own commit does.

    A **phantom** entry is a member seated on a share it does not hold: an unsplit array
    runs its selected alternate anyway, and its dial says so rather than reading "Off"
    (:meth:`PowersSection._fallback_share`). Those points are not spoken for until the
    player picks another notch, so they are counted as nothing while it sits where it was
    drawn — otherwise the first split of an untouched array would find the pool already
    eaten by a share nobody had assigned.
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

        A static method because the header's label is built before any of the dials
        exist (:meth:`PowersSection._pool_readout`) and restated by this coordinator once
        they do; two places writing the sentence two ways is how a readout comes to
        disagree with itself.

        **Unsplit is a state, not an absence.** An array with nothing spread says how
        many points there are and that none of them are spoken for — which is also the
        line that tells the player the array is still picking one alternate at a time.
        """

        if assigned <= 0:
            return f"Pool: {pool} PP \u2014 not split"
        left = pool - assigned
        text = f"{assigned}/{pool} PP split"
        return text if left <= 0 else f"{text} \u00b7 {left} left"


class _RankDial(QWidget):
    """A scale of pips for the rank an effect is currently *held at*, and what that means.

    Two kinds of power want one and they want the same control. A Growth 3 is not one
    leap to Gargantuan — it is Large, then Huge, then Gargantuan, and which of the three
    you are standing at is a mid-fight decision. A Damage 10 is not all-or-nothing
    either: a hero pulling their punches fires it at 5. So the card carries one pip per
    rank (:class:`~mm_companion.ui.rank_pips.RankPips`), with a label beside them saying
    what the current notch *is*: the **size the character
    becomes** for a size effect (read against the wielder, so a Small character's scale
    starts at Medium) and the plain rank otherwise. A rank is an accounting fact the card
    already prints; "Huge" is the thing being chosen.

    It was a ``QSlider``, which looked like a stray form control on a card and had to
    work around a drag: a slider that wrote on every tick would have deleted itself under
    the player's thumb, so it committed on release, deferred a commit made from a groove
    click until the mouse grab was over, and so on. A pip is picked by a click, which is
    the whole gesture, so all of that went with it.

    Ranks the Size Table clamps together simply repeat their category, which is honest —
    a Growth 8 really does spend several of its ranks at Gargantuan — and the scale
    groups them with a wider gap so the stretch at one size is visible.

    What carried over:

    * **Zero is off.** Nothing is held while the power is switched off — the dial
      reports where the power *is*, and off is nowhere, every pip hollow — and reaching
      zero switches it off, exactly as clicking the card would, so the dial is a whole
      control rather than one that can only turn a power on. There is no Off button:
      clicking the **last lit pip puts it out**, so a power held at rank 1 goes off on
      that click, and any other one steps down a rank. Picking a pip on a dormant power
      wakes it at the notch asked for, so going from dormant to Huge is one click.
    * **Hover names, click commits.** Hovering a pip names the notch a click there would
      land on and previews it on the scale; nothing reaches the section until a click.
    * **NoFocus**, because committing destroys the whole card: focus would land on
      whatever the tab order offers next — a table in some other block — and a
      ``QScrollArea`` scrolls to show a child that has just taken focus. That was the
      page jumping away from the card under the cursor.
    * **Live in the locked sheet**, like every other runtime control on a card: how far
      a power is turned up is a play action, not a build edit. Inside a switched-off
      Linked group it goes transparent to the mouse instead, so a click falls through to
      the card exactly as it does off the group's own chrome — never
      ``setEnabled(False)``, which nothing in this app does.
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

        What a Dynamic member's dial needs: the reachable pips have to *end* where the
        pool ends, so the last hollow pip is the most this member can be set to and the
        dotted ones beyond it are rungs its siblings' shares have spent. Free a point
        elsewhere and a dotted pip becomes pickable; spend one and one goes dotted. A
        member whose siblings hold everything is left with only the pip it is sitting
        on, which a click puts out.

        The **index space does not move**: the notch list is the member's whole ladder
        and what is affordable is always a prefix of it, so notch *n* buys the same rank
        for the same points however far the end has travelled.

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

    The whole-card twin of the click that selects an array **group's** live member, one
    level down: a power whose own effects are an array runs exactly one of them at a
    time, and that is what makes it cheaper than the same effects bought independently.
    A card is one widget, though, so there is no card to click — hence a control.

    A combo rather than the group header's segmented toggle: an effect reads as its name
    and rank ("Enhanced Trait 4"), several of those do not fit across a card, and an
    array may hold more than three. Like the rank dial it stays live in the locked
    sheet — choosing which alternate you are using is a play action, not a build edit —
    and takes no focus, because committing rebuilds the card out from under it.
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
