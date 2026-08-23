"""Section 4: powers.

The most complex part of a character. An "Add Power" button opens the standalone
:class:`~mm_companion.ui.power_constructor.PowerConstructorWindow` brick-builder in
its own window; saving there hands the finished
:class:`~mm_companion.core.powers.Power` back through
:attr:`~mm_companion.ui.power_constructor.PowerConstructorWindow.powerSaved`, which
this section appends to the shared :class:`~mm_companion.core.character.Character`
and shows as a *card*. Each card reads top-to-bottom like a stat-block entry: a
header (name, assembled point cost, a ⚠ marker when the power breaks a Power Level
cap), the free-text description, and a per-effect summary pairing each effect's extras
and flaws with *its full game-term table* alongside them (Type / Range / Action /
Duration / checks / measures — the same data the Power Constructor shows while
building, rendered small and muted so it informs without shouting). A power that calls
for dice closes with a footer listing one roll per line (the attack check, then the
save it forces); a power that rolls nothing simply ends. Each card carries an edit
button that reopens the constructor pre-loaded with that power — editing a deep copy
that replaces the original in place on save — and a remove button.

**The card is the on/off switch.** There is no small "Active" checkbox: clicking a
card's body toggles a runtime-gated power (or, for a member of an array, makes it the
live alternate), and the card's own appearance carries the state — a switched-off
power stays fully readable but recedes, dimmed and a notch smaller, so a glance at the
block tells you what is running. Flipping one *eases* between those two looks rather
than cutting, and a card you can click says so before you touch it: an accent edge down
its left side, and an accent border under the pointer — on exactly one card at a time,
the innermost one a click would reach. Clicking the grip, ✎ or ✕ never toggles: those
widgets consume the click themselves.

Powers can be **grouped**. A character's ``powers`` is a tree of
:data:`~mm_companion.core.powers.PowerNode` — leaf powers and
:class:`~mm_companion.core.powers.PowerGroup` containers, which can nest arbitrarily.
Dragging a card into a *gap* reorders (or moves it into/out of a group); dropping it
*onto* another card, or onto a group's title bar, combines the two into a group with
a distinct highlight. A group's title bar carries an Independent / Array / Linked
mode toggle (the same three choices the Constructor offers for a single power's own
effects) that decides how its members' costs combine.

The **cards themselves** live in :mod:`mm_companion.ui.cards` — the frame and its
on/off look, the grip, the drop-target lists, the dice footer, the per-effect summary.
This section is the part that knows about *powers*: the tree, what a click on a card
means, what a group's mode does to its members' costs. The Equipment block draws its
items from the same pieces (an item wraps a real ``Power``), so a change to how a card
looks or behaves reaches both.

It follows the standard section contract (``data`` + ``character`` constructor,
``changed`` signal, ``set_locked``) so it slots into the sheet like the others, and —
because saved powers live on the model — a loaded character repopulates its list at
construction.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_INDEPENDENT,
    STRUCTURE_LINKED,
    Power,
    PowerEffectInstance,
    PowerGroup,
    PowerNode,
    power_is_homerule,
    power_is_stunt,
)
from mm_companion.core.rules import (
    PIN_POWER,
    USE_POWER_STUNT,
    PinRef,
    active_array_child,
    active_array_effect_index,
    array_pool_points,
    clear_power_extra_effort,
    counter_rolls,
    debilitated_traits,
    dynamic_held_rank,
    dynamic_member_cost,
    dynamic_rank_share,
    dynamic_share_points,
    dynamic_share_steps,
    effect_current_rank,
    effect_display_name,
    effect_has_rank_dial,
    effect_is_selected,
    effect_stands,
    group_scope_note,
    leaf_powers,
    live_array_children,
    live_array_effects,
    live_powers,
    member_effects,
    node_cost_formula,
    node_display_cost,
    power_display_name,
    power_effects_are_array,
    power_has_custom_modifier,
    power_has_standing_effect,
    power_pool_points,
    power_roll_lines,
    power_rolls,
    power_runtime_gates,
    power_total_cost,
    power_violations,
    powers_points_spent,
    pushable_effects,
    size_steps,
    spend_extra_effort,
    stunt_source,
)
from mm_companion.ui import theme
from mm_companion.ui.cards import (
    DraggableCard,
    DragHandle,
    GroupHeader,
    NodeList,
    RollLine,
    RollsFooter,
    effect_title,
    effects_block,
)
from mm_companion.ui.extra_effort import ExtraEffortDialog, add_power_effort_actions
from mm_companion.ui.power_constructor import PowerConstructorWindow
from mm_companion.ui.power_constructor.canvas import MODE_ARRAY_DYNAMIC
from mm_companion.ui.sections.stat_table import PinMenuState
from mm_companion.ui.sections.titled_section import TitledSection
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import (
    BOLD_STYLE,
    hline_separator,
    muted_style,
    preserved_scroll,
    tinted_style,
)

# The card machinery moved to :mod:`mm_companion.ui.cards` so the Equipment block could
# draw the same cards. These are the spellings anything that reached into this module
# already imports; the classes themselves are the shared ones.
_DraggableCard = DraggableCard
_DragHandle = DragHandle
_GroupHeader = GroupHeader
_NodeList = NodeList
_RollLine = RollLine

# What a click on a card does, by activation role (see _activation_role).
_CLICK_HINTS = {
    "toggle": "Click this card to switch the power on or off — its bonuses apply "
    "only while it is active.",
    "select": "Click this card to make it the array's live alternate; its siblings switch off.",
}

# The same click while the array's points are split across its Dynamic members, when
# "its siblings switch off" has stopped being true: the split decides who is running,
# so selecting one member no longer switches anything off (see live_array_children).
_SPLIT_SELECT_HINT = (
    "This array's points are split across its Dynamic members, so they are all running "
    "at once — selecting one alternate only matters once the pool is handed back, which "
    "is the ↺ button on the group's title bar."
)

# What each group mode is called on its title bar.
_MODE_LABELS = {
    STRUCTURE_INDEPENDENT: "Group of powers",
    STRUCTURE_ARRAY: "Group of alternate effects",
    MODE_ARRAY_DYNAMIC: "Group of dynamic alternate effects",
    STRUCTURE_LINKED: "Group of linked powers",
}


def _group_mode(group: PowerGroup) -> str:
    """Which of the toggle's four segments a group is currently on.

    :data:`~mm_companion.ui.power_constructor.canvas.MODE_ARRAY_DYNAMIC` is a *view*
    over the model rather than a fourth stored structure — an array whose members carry
    the ``dynamic`` flag — so this is the one place the view is derived and the switch,
    the title and the rename placeholder cannot disagree about it. **Any** Dynamic
    member counts, so a mixed array saved while Dynamic was a per-member checkbox reads
    as what it is instead of as a plain array that quietly costs more.
    """

    if group.mode == STRUCTURE_ARRAY and any(child.dynamic for child in group.children):
        return MODE_ARRAY_DYNAMIC
    return group.mode


def _held_effects(node) -> list[PowerEffectInstance]:
    """The effects one share holds down — a whole member's, or a single effect's own.

    A share is held by a member of an array, and an array exists at two levels: its
    members are whole cards at the group level and single effects inside one power. The
    slider is the same either way, so this is where the two shapes meet.
    """

    if isinstance(node, PowerEffectInstance):
        return [node]
    return member_effects(node)


def _pool_is_split(group: PowerGroup) -> bool:
    """Whether any of this array's Dynamic members is currently holding a share.

    The one question that decides whether an array behaves as a set of mutually
    exclusive alternates or as a pool running several at once, so the hint, the dimming
    and the header button all ask it here rather than each spelling it out.
    """

    return group.mode == STRUCTURE_ARRAY and any(
        child.dynamic and (child.dynamic_points or 0) > 0 for child in group.children
    )


def roll_lines(power: Power, character: Character, data: GameData) -> list[str]:
    """One entry per die roll a power calls for; effect-prefixed for a multi-effect power.

    Module-level so both the power card's dice footer and the GM's NPC hover summary
    read the same lines. The lines are the labels of :func:`~mm_companion.core.rules.
    power_rolls`, which is also what the card's 🎲 buttons roll — so what is written
    and what is rolled can never drift apart.
    """

    return power_roll_lines(power, character, data)


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
    slider; it stays a shared helper because a segmented strip is the shape any future
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
            "the dearer Dynamic price, and the split is made on the cards' sliders.",
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
    """Keeps one array's share sliders honest about each other, live.

    A split is one decision spread over several controls: every member draws from the
    same pool, so moving one changes what the others may take. Without something joining
    them each slider knew only its own bounds at the moment it was built, and the truth
    arrived a rebuild later — which is how a handle came to move because a *sibling* had.

    So the dials report every notch they pass (:attr:`_RankDial.previewed`) and this
    restates the rest of the array from that: each other dial's ceiling becomes what the
    pool has left once the previewing one is paid, and the header says what is unspent.
    Nothing here writes to the model — a drag is not a decision until it is released —
    which is what keeps a whole gesture a single undoable step.

    A **phantom** entry is a member seated on a share it does not hold: an unsplit array
    runs its selected alternate anyway, and its slider says so rather than reading "Off"
    (:meth:`PowersSection._fallback_share`). Those points are not spoken for until the
    player moves that handle, so they are counted as nothing while it sits where it was
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
            ceiling = PowersSection._share_index(steps, budget)
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

        A static method because the header's label is built before any of the sliders
        exist (:meth:`PowersSection._pool_readout`) and restated by this coordinator once
        they do; two places writing the sentence two ways is how a readout comes to
        disagree with itself mid-drag.

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
    """A slider for the rank an effect is currently *held at*, and what that means.

    Two kinds of power want one and they want the same control. A Growth 3 is not one
    leap to Gargantuan — it is Large, then Huge, then Gargantuan, and which of the three
    you are standing at is a mid-fight decision. A Damage 10 is not all-or-nothing
    either: a hero pulling their punches fires it at 5. So the card carries one slider
    from ``0`` to the effect's bought rank, with a label beside it saying what the
    current notch *is*: the **size the character becomes** for a size effect (read
    against the wielder, so a Small character's dial starts at Medium) and the plain
    rank otherwise. A rank is an accounting fact the card already prints; "Huge" is the
    thing being chosen.

    Ranks the Size Table clamps together simply repeat their category, which is honest —
    a Growth 8 really does spend several of its ranks at Gargantuan.

    Four behaviours carried over from the strip of buttons this replaces:

    * **Zero is off.** Nothing is held while the power is switched off — the dial
      reports where the power *is*, and off is nowhere — and sliding back to 0 switches
      it off, exactly as clicking the card would, so the dial is a whole control rather
      than one that can only turn a power on. Sliding up from 0 wakes the power at the
      notch asked for, so going from dormant to Huge is one gesture.
    * **It commits on release.** Every runtime setter ends in a rebuild, so a slider
      that wrote on each tick would delete itself under the player's thumb. The label
      tracks the drag; only ``sliderReleased`` (and a keyboard or groove step, which
      leaves the handle up) reaches the section.
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
    #: Every notch the handle passes, drag included. Nothing is written for these — they
    #: are what lets a Dynamic array's other sliders restate their ceilings, and its
    #: header its remaining points, while one of them is still moving.
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
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(theme.metric("space.sm")))

        self._caption_text = caption
        caption_label = QLabel(caption)
        caption_label.setStyleSheet(muted_style())
        row.addWidget(caption_label)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, max(0, maximum))
        self._slider.setSingleStep(1)
        self._slider.setPageStep(1)
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider.setTickInterval(1)
        # The top of the groove. For a Dynamic member this moves as its siblings give
        # points back, so the right-hand end of the track *is* the most it can be set
        # to. See :meth:`set_ceiling`.
        self._ceiling = max(0, maximum)
        self._slider.setValue(max(0, min(maximum, current)))
        self._slider.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not interactive)
        self._slider.setCursor(
            Qt.CursorShape.PointingHandCursor if interactive else Qt.CursorShape.ArrowCursor
        )
        guard_wheel(self._slider)  # don't let a card's slider steal the page wheel
        # *After* the wheel guard, which asks for StrongFocus so a focused widget keeps
        # its own wheel. Focus is the one thing this slider must never take: committing
        # destroys the card, and a QScrollArea chases whatever takes focus next.
        self._slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(self._slider, 1)

        self._value = QLabel(self._label_for(self._slider.value()))
        row.addWidget(self._value)

        # Connected *after* the initial value, so seeding the dial never reads as the
        # player having moved it.
        self._slider.valueChanged.connect(self._on_value_changed)
        self._slider.sliderReleased.connect(self._commit)

    def caption(self) -> str:
        """What this dial is *of* — "Size", "Share", or the effect's own name."""

        return self._caption_text

    def value(self) -> int:
        """The notch the handle is on right now, committed or merely being dragged."""

        return self._slider.value()

    def ceiling(self) -> int:
        """The highest notch currently reachable."""

        return self._ceiling

    def set_ceiling(self, ceiling: int) -> None:
        """Move the end of the groove to the highest notch currently reachable.

        What a Dynamic member's slider needs: the track has to *end* where the pool ends,
        so the right-hand end is the most this member can be set to and the divisions on
        it are the choices it actually has. Free a point elsewhere and this slider gains
        a division; spend one and it loses one. A member whose siblings hold everything
        is left with the single notch it is sitting on rather than a long track most of
        which refuses the handle — a slider that can be dragged into a region it then
        rejects is the thing this exists to avoid.

        The **index space does not move**: the notch list is the member's whole ladder
        and what is affordable is always a prefix of it, so notch *n* buys the same rank
        for the same points however far the end has travelled, and the handle keeps its
        meaning while a sibling is still being dragged.

        Never below the notch the handle is seated on. A rebuild can move the pool under
        a split already made, and an end that cut into a stored share would spend it the
        moment the dial was touched.
        """

        self._ceiling = max(0, min(ceiling, len(self._labels) - 1))
        self._ceiling = max(self._ceiling, self._slider.value())
        self._slider.setMaximum(self._ceiling)

    def _label_for(self, rank: int) -> str:
        return self._labels.get(rank, f"Rank {rank}")

    def _on_value_changed(self, value: int) -> None:
        self._value.setText(self._label_for(value))
        self.previewed.emit(value)
        # A drag reports every notch it passes; only the one it stops on is a decision.
        # A keyboard or groove step leaves the handle up, and *is* one.
        if not self._slider.isSliderDown():
            self._commit()

    def _commit(self) -> None:
        """Report the notch settled on, once it is safe to be deleted for it.

        Every commit ends in a rebuild that deletes this very widget. A **groove click**
        reaches :meth:`_on_value_changed` from inside ``mousePressEvent``, so committing
        straight through tore the slider down while it still held the mouse grab: the
        rest of that gesture went nowhere, and a queued auto-repeat could re-fire against
        a stale reading of the pool. So a commit made while a button is still held is
        deferred a turn of the event loop, letting the press finish first.

        Only that case. A release has already cleared the button, and a keyboard step or
        a programmatic ``setValue`` never had one — those commit straight through, which
        keeps the dial synchronous everywhere it was before.
        """

        value = self._slider.value()
        if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            QTimer.singleShot(0, lambda: self.rankPicked.emit(value))
            return
        self.rankPicked.emit(value)


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
            "Only one effect of an array runs at a time — that is what makes an array "
            "cheaper than the same effects bought separately. The others contribute "
            "nothing to the sheet until you pick them."
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


class PowersSection(TitledSection):
    """Powers section: launches the Power Constructor and lists saved powers as a tree."""

    # A build change (add/remove/edit a power, group, re-cost) — marks the sheet dirty.
    changed = Signal()
    # A runtime on/off toggle. It updates the live sheet numbers (a trait boost drops
    # in or out) but is *not* part of the point build, so it costs nothing and does not
    # re-derive the block itself — the sheet wires it to the same refreshes as
    # ``changed`` minus FACTS_CHANGED. It *is* saved with the character, though (a
    # Growth held at Large reopens at Large), so it does carry the dirty flag.
    runtimeChanged = Signal()
    #: A card's roll line was right-clicked and pinned — carries a
    #: :class:`~mm_companion.core.rules.pins.PinRef`. Only ever raised on a sheet a
    #: GM opened from a card (see :meth:`set_pin_target`).
    pinRequested = Signal(object)
    #: The same, for a line that was already on the card.
    unpinRequested = Signal(object)

    #: A card's 🎲 was pressed — roll that line. Carries a
    #: :class:`~mm_companion.core.rules.RollSpec`. Neither a build change nor a
    #: runtime one: rolling a power changes nothing about the power.
    rollRequested = Signal(object)

    #: A sentence for the roll history — what a use of Extra Effort bought and what it
    #: cost. Carries the text, like the System block's own note.
    noteRequested = Signal(str)
    #: Extra Effort was shrugged off with a Determination heroic feat, which costs a Hero
    #: Point (p22). Carries the delta; the System block owns the pips and moves them.
    heroPointRequested = Signal(int)
    #: Extra Effort's fatigue was applied to the character. The same fan-out the
    #: Conditions block's own signal drives — the model changed, and every view over a
    #: condition restates itself.
    conditionsChanged = Signal()

    #: How long a card takes to ease between its live and switched-off looks. A class
    #: attribute so tests can zero it and assert on the resting state without waiting
    #: on a timer.
    TRANSITION_MS = 180

    def __init__(
        self,
        data: GameData,
        character: Character,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._character = character
        self._locked = False
        # Whether this sheet was opened from a GM card, and what is already on
        # that card. Both are set by the sheet after construction.
        self._pins = PinMenuState()
        # Per node id, how switched-off that node's card currently *looks* (0 live, 1
        # fully off). Survives the card teardown a toggle triggers, so the replacement
        # card can ease on from where its predecessor was — see _show_activation.
        self._card_off: dict[str, float] = {}
        # Per-array share-slider coordinators, keyed by group id; see :class:`_SplitGroup`,
        # and the header labels waiting to be handed to them.
        self._splits: dict[str, _SplitGroup] = {}
        self._pool_labels: dict[str, QLabel] = {}
        self._card_off_prev: dict[str, float] = {}
        # Keep constructor windows referenced so Qt doesn't garbage-collect them the
        # moment the click handler returns.
        self._windows: list[PowerConstructorWindow] = []

        layout = QVBoxLayout(self)
        self._empty = QLabel("No powers yet")
        self._empty.setEnabled(False)
        layout.addWidget(self._empty)

        # The saved powers stack above the Add button, one card each; the top-level
        # list is the root of the drag-and-drop tree.
        self._list_host = NodeList("")
        self._list_host.combineRequested.connect(self._on_combine)
        self._list_host.moveRequested.connect(self._on_move)
        layout.addWidget(self._list_host)

        self._add_button = QPushButton("Add Power")
        self._add_button.clicked.connect(self._open_constructor)
        layout.addWidget(self._add_button)

        # Seed from the (possibly loaded) model.
        self._rebuild_list()

    # -- constructor lifecycle --------------------------------------------
    def _open_constructor(self) -> None:
        window = PowerConstructorWindow(self._data, character=self._character)
        window.powerSaved.connect(self._on_power_saved)
        # The constructor is a window, not a block, so it cannot reach the roller
        # itself; its Improvise panel asks and this section hands the request on the
        # same way its own cards do.
        window.rollRequested.connect(self.rollRequested)
        window.closed.connect(lambda w=window: self._on_window_closed(w))
        self._windows.append(window)
        window.show()

    def _on_power_saved(self, power: Power) -> None:
        self._character.powers.append(power)
        self._rebuild_list()
        self.changed.emit()

    def _edit_power(self, power: Power) -> None:
        """Reopen the constructor pre-loaded with an existing power for editing.

        The constructor edits a deep copy and hands it back on save; the copy then
        replaces the original in place (identity match), so an unsaved close is a
        no-op and a save swaps in exactly the power that was opened.
        """
        window = PowerConstructorWindow(self._data, character=self._character, power=power)
        window.rollRequested.connect(self.rollRequested)
        window.powerSaved.connect(
            lambda edited, original=power: self._on_power_edited(original, edited)
        )
        window.closed.connect(lambda w=window: self._on_window_closed(w))
        self._windows.append(window)
        window.show()

    def _on_power_edited(self, original: Power, edited: Power) -> None:
        located = self._locate(original.id)
        if located is not None:
            _node, siblings, index, _parent = located
            siblings[index] = edited
        else:  # the original was removed while the editor was open — treat as an add
            self._character.powers.append(edited)
        self._rebuild_list()
        self.changed.emit()

    def _on_window_closed(self, window: PowerConstructorWindow) -> None:
        if window in self._windows:
            self._windows.remove(window)

    # -- tree lookup / mutation seams (headless-testable) -----------------
    def _locate(
        self,
        node_id: str,
        nodes: list[PowerNode] | None = None,
        parent: PowerGroup | None = None,
    ) -> tuple[PowerNode, list[PowerNode], int, PowerGroup | None] | None:
        """Find a node by id, returning ``(node, its list, index, parent group)``.

        The list is the actual mutable container (top-level ``powers`` or a group's
        ``children``), so callers can splice in place. ``None`` when the id is absent.
        """
        nodes = self._character.powers if nodes is None else nodes
        for index, node in enumerate(nodes):
            if node.id == node_id:
                return node, nodes, index, parent
            if isinstance(node, PowerGroup):
                found = self._locate(node_id, node.children, node)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _subtree_ids(node: PowerNode) -> set[str]:
        ids = {node.id}
        if isinstance(node, PowerGroup):
            for child in node.children:
                ids |= PowersSection._subtree_ids(child)
        return ids

    def _groupable(self, node_id: str) -> bool:
        """Whether this node may join a group at all — false for a **power stunt**.

        A stunt is bought with Extra Effort and a Hero Point rather than with points, so
        it costs 0 (see :func:`~mm_companion.core.rules.node_cost`). Inside an array that
        makes it the cheapest member by definition, which moves the base, the pool and
        every other member's flat price — a temporary card silently repricing the build
        it was taken from. It is not saved either (``strip_stunts``), so the group it
        left would come back a member short.

        The tree is drag-and-drop and nothing else refuses a drop, so this is asked in
        three places: ``NodeList``'s admission rule (which *shows* the refusal), and both
        mutation seams, which are the ones that hold the invariant.

        A node this section cannot find is refused too. It cannot arrive from a drag
        within the tree, and the mutation seams would drop it on the floor anyway — so
        accepting it would mean lighting the target up for a move that never happens,
        which is the failure this whole guard exists to avoid.
        """

        node = self._locate(node_id)
        return node is not None and not power_is_stunt(node[0])

    def _on_combine(self, source_id: str, target_id: str) -> None:
        """Group the dragged node with a drop target into a new Independent group.

        Wraps the target (a card, or a whole group when dropped on its title bar) and
        the source into a fresh :class:`PowerGroup`, replacing the target in place —
        nesting naturally when the target already sits inside a group. Rejected when
        the two are the same node, when the target lives inside the source's own
        subtree, or when either of them is a stunt (:meth:`_groupable`).
        """
        if source_id == target_id:
            return
        if not (self._groupable(source_id) and self._groupable(target_id)):
            return
        source = self._locate(source_id)
        target = self._locate(target_id)
        if source is None or target is None:
            return
        source_node, _src_list, _src_index, _src_parent = source
        if target_id in self._subtree_ids(source_node):
            return  # can't group a node with its own descendant
        # Remove the source, then re-find the target (its index may have shifted).
        source_node, src_list, src_index, _ = source
        src_list.pop(src_index)
        target = self._locate(target_id)
        if target is None:  # defensive: the target should still be findable here
            src_list.insert(src_index, source_node)  # put it back where it was
            return
        target_node, tgt_list, tgt_index, _ = target
        group = PowerGroup(
            mode=STRUCTURE_INDEPENDENT,
            children=[target_node, source_node],
            active_child_id=target_node.id,
        )
        tgt_list[tgt_index] = group
        # The two members are inside a fresh Independent group now and neither is an
        # array member any more; the group itself may have landed in one.
        self._after_structural_change(source_node.id, target_node.id, group.id)

    def _on_move(self, source_id: str, parent_id: str, index: int) -> None:
        """Move the dragged node into a list (top-level or a group) at *index*.

        This is how a card is reordered, pulled out of a group (dropped in a higher
        list), or added to a group as another member (dropped in the group's body).
        Rejected when the destination lives inside the moved node's own subtree, or when
        a stunt is being moved *into* a group (:meth:`_groupable`) — reordering one at
        the top level is fine, since that is where it belongs.
        """
        source = self._locate(source_id)
        if source is None:
            return
        source_node, src_list, src_index, _ = source
        if parent_id == "":
            dest_list: list[PowerNode] = self._character.powers
        else:
            if not self._groupable(source_id):
                return
            if parent_id in self._subtree_ids(source_node):
                return  # can't move a node into itself
            dest = self._locate(parent_id)
            if dest is None or not isinstance(dest[0], PowerGroup):
                return
            dest_list = dest[0].children
        src_list.pop(src_index)
        if dest_list is src_list and src_index < index:
            index -= 1  # the pop shifted everything after the source down one
        index = max(0, min(index, len(dest_list)))
        dest_list.insert(index, source_node)
        self._after_structural_change(source_node.id)

    def _after_structural_change(self, *moved_ids: str) -> None:
        """Tidy the tree after a combine/move, then rebuild and signal the change.

        *moved_ids* are the nodes whose **parent** just changed; each is re-seated
        against its new one (:meth:`_reseat_dynamic`). After the tidying, not before: a
        singleton group collapsing re-parents its child a second time, and a node seated
        against the parent it had in between would be seated against a group that no
        longer exists.
        """

        self._collapse_singletons()
        self._normalize_arrays()
        for node_id in moved_ids:
            self._reseat_dynamic(node_id)
        self._rebuild_list()
        self.changed.emit()

    def _reseat_dynamic(self, node_id: str) -> None:
        """Make a node's Dynamic flag agree with the group it now sits in.

        ``dynamic`` is a fact about a node's *place* — it means "a Dynamic member of the
        array I am in" — and it was the one piece of build state a drag never touched.
        Two things went wrong for it. A card dropped into a Dynamic array joined as a
        plain 1-point alternate: it got no share dial, it stayed mutually exclusive with
        siblings that were not, and it was mispriced, while the group's switch went on
        reading *Dynamic array* because :func:`_group_mode` lights on **any** member. And
        a card dragged *out* kept both the flag and its share, so dropping it into some
        other array turned that array Dynamic without anyone saying so.

        So a node landing inside a Dynamic array joins it, and one landing anywhere else
        stops claiming to be in one. Its **share** is only ever cleared, never invented:
        joining an array gives a member the right to hold points, not the points.

        Scoped to the node that moved, deliberately. The same rule applied to every child
        on every rebuild would quietly migrate a mixed array saved while Dynamic was a
        per-member checkbox — which reads as what it is today precisely because nothing
        migrates it (see ``docs/notes/powers.md``).
        """

        located = self._locate(node_id)
        if located is None:
            return
        node, _siblings, _index, parent = located
        joined = (
            parent is not None
            and parent.mode == STRUCTURE_ARRAY
            and _group_mode(parent) == MODE_ARRAY_DYNAMIC
        )
        node.dynamic = joined
        if not joined:
            node.dynamic_points = None

    def _collapse_singletons(self) -> None:
        """Dissolve groups left trivial by a move: one child unwraps, zero drops out."""

        def collapse(nodes: list[PowerNode]) -> list[PowerNode]:
            result: list[PowerNode] = []
            for node in nodes:
                if isinstance(node, PowerGroup):
                    node.children[:] = collapse(node.children)
                    if len(node.children) == 1:
                        result.append(node.children[0])
                    elif node.children:
                        result.append(node)
                    # an emptied group is dropped
                else:
                    result.append(node)
            return result

        self._character.powers[:] = collapse(self._character.powers)

    def _normalize_arrays(self) -> None:
        """Point every array group's ``active_child_id`` at a real child (else the first)."""

        def normalize(nodes: list[PowerNode]) -> None:
            for node in nodes:
                if isinstance(node, PowerGroup):
                    if node.mode == STRUCTURE_ARRAY and node.children:
                        ids = {child.id for child in node.children}
                        if node.active_child_id not in ids:
                            node.active_child_id = node.children[0].id
                    normalize(node.children)

        normalize(self._character.powers)

    # -- rendering --------------------------------------------------------
    def refresh(self) -> None:
        """Rebuild the cards from the current character state.

        The public seam the sheet calls when a fact *outside* this section changes a
        power's displayed numbers — an ability (a Strength-Based Damage folds in
        Strength; an attack power's PL cap tracks Attack) or the character's Power
        Level (which sets every attack cap). It only reads the model, so it never
        emits :attr:`changed` (no signal loop back to the triggering section).
        """
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        """Rebuild the whole card tree from the model, toggling the empty label.

        Every runtime setter ends here — flipping one power can restate another card's
        numbers — so this runs on a plain mid-play click, and the block is momentarily
        empty while it does. :func:`~mm_companion.ui.widgets.preserved_scroll` is what
        stops that shrinking the page out from under the card just clicked.
        """
        with preserved_scroll(self):
            self._normalize_arrays()  # a valid active member per array before drawing
            # Hand the on-screen progress over to the cards about to be built, and start
            # a fresh map — so a power that was removed or ungrouped leaves nothing
            # behind for a later node to inherit.
            self._card_off_prev, self._card_off = self._card_off, {}
            # One coordinator per array holding a split, rebuilt with the cards it
            # joins: a dial from a torn-down card must never be restated.
            self._splits = {}
            self._pool_labels = {}
            self._list_host.clear()
            for node in self._character.powers:
                self._list_host.add_entry(node.id, self._render_node(node, None))
            self._empty.setVisible(not self._character.powers)
            self.set_priced_title("Powers", powers_points_spent(self._character, self._data))

    def _render_node(
        self, node: PowerNode, parent: PowerGroup | None, interactive: bool = True
    ) -> QWidget:
        """A widget for one tree node — a group container or a leaf power card.

        ``interactive`` is ``False`` when an enclosing group is currently switched off
        (a Linked group's one toggle turns its whole subtree off); it greys out the
        node's runtime-activation controls so a member can't be re-activated while its
        group is inactive. Structural chrome (drag/edit/remove) is unaffected.
        """
        if isinstance(node, PowerGroup):
            return self._make_group_card(node, parent, interactive)
        return self._make_card(node, parent, interactive)

    # -- group card -------------------------------------------------------
    def _make_group_card(
        self, group: PowerGroup, parent: PowerGroup | None, interactive: bool = True
    ) -> QWidget:
        """A framed container: a mode title bar over its members, rendered indented.

        A group that owns a switch (a Linked group, or any group sitting inside an
        array) is clicked on its title bar the same way a leaf card is clicked on its
        body — and dims as a whole, members included, when it is switched off.
        """
        card = DraggableCard(group.id, group=True)
        self._arm_activation(card, group, parent, interactive)
        layout = QVBoxLayout(card)
        layout.addWidget(self._group_header(group, card, parent))

        # A Linked group that is off forces its whole subtree off, so its members'
        # activation controls are disabled; other modes just pass interactivity down.
        child_interactive = interactive and (
            self._group_is_active(group) if group.mode == STRUCTURE_LINKED else True
        )
        # A group refuses a stunt outright, and shows the refusal rather than accepting
        # the drop and quietly doing nothing (see :meth:`_groupable`).
        inner = NodeList(group.id, accepts=self._groupable)
        inner.combineRequested.connect(self._on_combine)
        inner.moveRequested.connect(self._on_move)
        for child in group.children:
            inner.add_entry(child.id, self._render_node(child, group, child_interactive))
        # Now that the members' sliders exist, give the header's readout to the thing
        # that counts it down while one of them is moving.
        split = self._splits.get(group.id)
        readout = self._pool_labels.get(group.id)
        if split is not None and readout is not None:
            split.set_readout(readout)
            split.restate()
        indent = QWidget()
        indent_layout = QHBoxLayout(indent)
        indent_layout.setContentsMargins(int(theme.metric("space.indent")), 0, 0, 0)
        indent_layout.addWidget(inner)
        layout.addWidget(indent)
        self._show_activation(card, group, parent)
        return card

    def _group_header(
        self,
        group: PowerGroup,
        card: DraggableCard,
        parent: PowerGroup | None,
    ) -> QWidget:
        """The group's title bar: grip, name + rename, mode toggle, cost, ungroup.

        The bar carries no activation control of its own — it is a plain widget, so a
        click on it falls through to the group card, which is the switch.
        """
        header = GroupHeader()
        header.powerDropped.connect(lambda src, gid=group.id: self._on_combine(src, gid))
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)

        grip = DragHandle()
        grip.setToolTip("Drag to move this group, or drop a power here to group it with this one")
        grip.dragStarted.connect(card.start_drag)
        row.addWidget(grip)
        grip.setVisible(not self._locked)

        mode_label = _MODE_LABELS.get(_group_mode(group), _MODE_LABELS[STRUCTURE_INDEPENDENT])
        label = QLabel(group.name or mode_label)
        label.setStyleSheet(BOLD_STYLE)
        row.addWidget(label)

        rename = QPushButton("✎")
        rename.setFixedWidth(int(theme.metric("column.chip-button")))
        rename.setToolTip("Rename this group")
        rename.clicked.connect(lambda _checked=False, g=group: self._rename_group(g))
        row.addWidget(rename)
        rename.setVisible(not self._locked)

        # Order matters: the lock keeps whichever segment is lit, so the mode has to
        # be set before it — see _ModeToggle.set_locked.
        toggle = _ModeToggle()
        toggle.set_mode(_group_mode(group))
        toggle.modeChanged.connect(lambda mode, g=group: self._set_group_mode(g, mode))
        toggle.set_locked(self._locked)
        row.addWidget(toggle)

        row.addStretch()

        split = self._pool_readout(group)
        if split is not None:
            row.addWidget(split)
        # Stays in a locked sheet, unlike the rename and ungroup buttons either side of
        # it: handing the pool back is the same free action the share dials are, and
        # those are live locked too (:class:`_RankDial`).
        release = self._pool_release(group)
        if release is not None:
            row.addWidget(release)
        if split is not None or release is not None:
            # The pool is a sentence and the cost beside it is a number; without a gap
            # they read as one phrase ("not split 15 PP").
            row.addSpacing(int(theme.metric("space.lg")))

        cost = QLabel(f"{node_display_cost(group, parent, self._data, self._character)} PP")
        cost.setEnabled(False)
        self._explain_cost(cost, group)
        row.addWidget(cost)

        ungroup = QPushButton("✕")
        ungroup.setFixedWidth(int(theme.metric("column.chip-button")))
        ungroup.setToolTip("Ungroup — dissolve this group, keeping its powers")
        ungroup.clicked.connect(lambda _checked=False, g=group: self._ungroup(g))
        row.addWidget(ungroup)
        ungroup.setVisible(not self._locked)
        return header

    def _explain_cost(self, label: QLabel, node: PowerNode) -> None:
        """Put the working behind a node's price on its cost label, when there is any.

        The constructor prints the same working beside its total, in full — there is
        room on a line of its own. Here there is not: a card's header already carries a
        name, up to three badges, a pool readout and three buttons, and a group's carries
        a mode toggle and a hand-back button as well. So the arithmetic goes on the
        tooltip of the one thing it explains, which is where a player asking "why does
        this say 23 when the cards say 10, 16 and 20" is already pointing.
        """

        parts = [node_cost_formula(node, self._data, self._character)]
        if isinstance(node, PowerGroup):
            # Not a warning: three genuinely separate removable devices really are
            # charged three times, and nothing can tell that build from one device split
            # across three cards. It states the arithmetic and lets the player decide.
            parts.append(group_scope_note(node, self._data, self._character))
        tooltip = "\n".join(part for part in parts if part)
        if tooltip:
            label.setToolTip(tooltip)

    def _arm_card_menu(self, card: DraggableCard, power: Power) -> None:
        """Arm the card's right-click menu — Extra Effort, then this power's counters.

        Both are things a power can be *used for* rather than things it calls for, which
        is why neither is in the dice footer: putting the counter rolls there gave every
        attack card and every weapon in the Equipment block a die button for a case the
        GM has to approve first, and Extra Effort is a decision before it is a number.
        A right-click menu costs no card space and is where the app already puts a
        card-adjacent action (the footer's own Pin menu).

        A card with nothing to offer — an always-on Protection, which can neither be
        readied nor pushed — gets no menu rather than an empty one.
        """

        if not counter_rolls(power, self._character, self._data) and not pushable_effects(
            power, self._data
        ):
            return
        card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        card.customContextMenuRequested.connect(
            lambda pos, c=card, p=power: self._show_card_menu(c, p, pos)
        )

    def card_menu(self, card: DraggableCard, power: Power) -> QMenu:
        """The card's whole menu, built but not shown.

        Split from :meth:`_show_card_menu` so the wiring can be checked without ``exec``
        — a modal menu headless is a test that hangs rather than a test that passes.
        """

        menu = QMenu(card)
        offered = add_power_effort_actions(
            menu, power, self._character, self._data, self.use_extra_effort
        )
        if any(effect.extra_effort for effect in power.effects):
            clear = menu.addAction("Clear Extra Effort")
            clear.setToolTip(
                "Extra Effort lasts until the end of your turn, and nothing here tracks "
                "turns — take the ranks back when it is over."
            )
            clear.triggered.connect(lambda _checked=False, p=power: self.clear_extra_effort(p))
            offered = True
        specs = counter_rolls(power, self._character, self._data)
        if specs and offered:
            menu.addSeparator()
        self.fill_counter_menu(menu, specs)
        return menu

    def _show_card_menu(self, card: DraggableCard, power: Power, pos) -> None:
        self.card_menu(card, power).exec(card.mapToGlobal(pos))

    def use_extra_effort(
        self, use, power: Power, effect: PowerEffectInstance, effect_name: str
    ) -> bool:
        """Confirm one use of Extra Effort against this effect, and charge it.

        The push itself is *runtime* state on the effect, so it rides the same signal a
        card toggle does; the fatigue is applied to the shared model by
        :func:`~mm_companion.core.rules.spend_extra_effort`, through the very condition
        resolver the Conditions block applies with. Returns ``False`` when the dialog was
        cancelled, so nothing was spent and nothing was gained.

        A **power stunt** is the one use that cannot be confirmed in a single dialog: it
        is a whole alternate effect the player has yet to build, so it opens the
        constructor first and charges the effort when something comes back
        (:meth:`_open_stunt`). ``True`` there means "the constructor is open", not "it
        was spent" — nothing is until the build is confirmed.
        """

        if use.id == USE_POWER_STUNT:
            self._open_stunt(use, power, effect, effect_name)
            return True

        dialog = ExtraEffortDialog(
            self._character,
            self._data,
            use,
            effect=effect,
            effect_name=effect_name,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        outcome = spend_extra_effort(
            self._character,
            self._data,
            use,
            effect=effect,
            effect_name=effect_name,
            doubled=dialog.doubled,
            determination=dialog.determination,
        )
        if dialog.spend_hero_point:
            self.heroPointRequested.emit(-1)
        self.noteRequested.emit(outcome.note)
        self._rebuild_list()
        self.runtimeChanged.emit()
        self.conditionsChanged.emit()
        return True

    def _open_stunt(self, use, power: Power, effect: PowerEffectInstance, effect_name: str) -> None:
        """Open the constructor to build a stunt of ``power``.

        The build comes **first** and the effort is charged on the way back
        (:meth:`_on_stunt_saved`): a player who closes the constructor without saving has
        changed their mind, and charging them a rung of the fatigue ladder for a stunt
        that does not exist would be the app inventing a rule.
        """

        window = PowerConstructorWindow(self._data, character=self._character)
        window.rollRequested.connect(self.rollRequested)
        window.powerSaved.connect(
            lambda built, u=use, p=power, e=effect, n=effect_name: self._on_stunt_saved(
                built, u, p, e, n
            )
        )
        window.closed.connect(lambda w=window: self._on_window_closed(w))
        self._windows.append(window)
        window.show()

    def _on_stunt_saved(
        self, built: Power, use, power: Power, effect: PowerEffectInstance, effect_name: str
    ) -> bool:
        """Charge the Extra Effort, then put the finished stunt on the sheet as its own card.

        Cancelling the cost dialog here drops the build rather than adding it free: the
        dialog is the "yes, spend it" step, and a stunt nobody paid for is not a stunt.

        The stunt is appended at the top level even when its source sits inside a group —
        it is a card of its own, marked with what it came from, rather than a member of
        anybody's array. It costs no points and is not saved (see ``Power.stunt_of``), so
        this rides ``runtimeChanged`` rather than ``changed``.
        """

        dialog = ExtraEffortDialog(
            self._character,
            self._data,
            use,
            effect=effect,
            effect_name=effect_name,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        built.stunt_of = power.id
        self._character.powers.append(built)
        outcome = spend_extra_effort(
            self._character,
            self._data,
            use,
            effect=effect,
            effect_name=effect_name,
            doubled=dialog.doubled,
            determination=dialog.determination,
        )
        if dialog.spend_hero_point:
            self.heroPointRequested.emit(-1)
        self.noteRequested.emit(outcome.note)
        self._rebuild_list()
        self.runtimeChanged.emit()
        self.conditionsChanged.emit()
        return True

    def clear_extra_effort(self, power: Power) -> bool:
        """Take back every rank Extra Effort pushed into this power; ``False`` if none."""

        if not clear_power_extra_effort(power):
            return False
        self._rebuild_list()
        self.runtimeChanged.emit()
        return True

    def fill_counter_menu(self, menu: QMenu, specs: list) -> None:
        """Add one entry per counter roll to ``menu``, each asking the roller for it."""

        for spec in specs:
            action = menu.addAction(f"{spec.label}  +{spec.modifier}")
            action.setToolTip(spec.hint)
            action.triggered.connect(lambda _checked=False, s=spec: self.rollRequested.emit(s))

    def _pool_readout(self, group: PowerGroup) -> QWidget | None:
        """How much of a Dynamic array's pool is currently spread, or ``None``.

        A readout, not a control: the split itself is made on each member's own rank
        slider, which is the point of moving it there — a member's share and the rank
        that share buys are one gesture instead of a number typed into a dialog and a
        rank worked out afterwards. What the header still owes the player is the one
        number no single slider can show, which is how much of the pool is spoken for.

        Leaving part of a pool unassigned stays legal, so nothing is tinted for it: this
        says what is spent and what is left, not what is wrong. The one thing it does
        flag is a split that has come to *more* than the pool, which no slider can
        produce but a rebuild can — editing the array moves the pool underneath a split
        already made.

        **It is there before the first split, too**, which is the point it is most
        needed at. It used to appear only once something had been assigned, so the pool
        was invisible for exactly the gesture that creates it: a player could not see how
        many points there were to spread until they had already spread some. An unsplit
        array therefore states the pool and says it is not split, which is also the only
        place a Dynamic array announces *which of its two regimes it is in* — click a
        card to pick one alternate, or move a slider and run several at once.

        It is handed to the array's :class:`_SplitGroup`, so it counts down while a
        member's slider is still moving rather than a rebuild later.
        """

        if group.mode != STRUCTURE_ARRAY or not any(c.dynamic for c in group.children):
            return None
        pool = array_pool_points(group, self._data)
        if pool <= 0:
            return None
        label = QLabel()
        label.setToolTip(
            "This array's points are shared across its Dynamic members, which run at "
            "the same time at reduced effectiveness. Move a member's slider to give it "
            "a share of the pool; hand the whole pool back with the button beside this."
        )
        # A group's header is built before its members' cards, so their sliders have not
        # registered yet and the coordinator does not exist. Say the truth now and let
        # :meth:`_make_group_card` hand the label over once they have.
        assigned = sum(c.dynamic_points or 0 for c in group.children if c.dynamic)
        label.setText(_SplitGroup.readout_text(assigned, pool))
        label.setStyleSheet(muted_style())
        self._pool_labels[group.id] = label
        return label

    def _pool_release(self, group: PowerGroup) -> QWidget | None:
        """The one gesture that hands a split pool back, or ``None`` for an array with none.

        Sliding every member to nothing was the only way back to an ordinary array, and
        it is one gesture per member for a decision the player makes once — the array's
        own tooltip had to describe it as a chore ("slide them all to nothing"). Worse,
        a split array's cards stop being clickable, so the way *out* of the pool was the
        one thing the array offered no control for.

        It writes what sliding every dial to zero writes and nothing else: the shares go,
        and the array's selected alternate is switched back on through the same
        :meth:`_set_array_active` a click on its card would have used — because with the
        shares gone that member is what the array is running
        (:func:`~mm_companion.core.rules.live_array_children`), and the shares that put
        it down are no longer there to say so.

        Runtime, so it is one undo step and emits ``runtimeChanged`` like every other
        control on a card. Shown only for an array that *has* a split; a button that is
        there and does nothing is the thing the disarmed card click already was.
        """

        if not _pool_is_split(group):
            return None
        button = QPushButton("↺")
        button.setFixedWidth(int(theme.metric("column.chip-button")))
        button.setToolTip(
            "Hand the pool back — clear every member's share and return the array to "
            "one alternate at a time."
        )
        button.clicked.connect(lambda _checked=False, g=group: self._release_pool(g))
        return button

    def _release_pool(self, group: PowerGroup) -> None:
        """Clear every share in *group* and put its selected alternate back on."""

        for child in group.children:
            child.dynamic_points = None
            self._hold_member(child, None)  # the notch each was held at goes with it
        child_id = group.active_child_id or (group.children[0].id if group.children else "")
        if child_id:
            self._set_array_active(group, child_id)  # rebuilds and signals for us
            return
        self._rebuild_list()
        self.runtimeChanged.emit()

    def _effect_pool_readout(self, power: Power) -> QWidget | None:
        """What an ``array`` power's own effects have spread of its pool, or ``None``.

        Same sentence as the group header's (:meth:`_pool_readout`, and the same
        :meth:`_SplitGroup.readout_text` writes both), one level down: the members are
        this power's effects rather than sibling cards, and the pool is what its base
        effect costs. It lands on the card's own header because that is the only place a
        power-wide fact can go — there is no group bar above a leaf card.
        """

        if power.structure != STRUCTURE_ARRAY or len(power.effects) < 2:
            return None
        if not any(effect.dynamic for effect in power.effects):
            return None
        pool = power_pool_points(power, self._data)
        if pool <= 0:
            return None
        assigned = sum(e.dynamic_points or 0 for e in power.effects if e.dynamic)
        label = QLabel(_SplitGroup.readout_text(assigned, pool))
        label.setStyleSheet(muted_style())
        label.setToolTip(
            "This power's points are shared across its Dynamic effects, which run at "
            "the same time at reduced effectiveness. Move an effect's slider to give it "
            "a share of the pool; hand the whole pool back with the button beside this."
        )
        self._pool_labels[power.id] = label
        return label

    def _effect_pool_release(self, power: Power) -> QWidget | None:
        """The effect-level hand-back — :meth:`_pool_release` one level down."""

        if not live_array_effects(power):
            return None
        button = QPushButton("↺")
        button.setFixedWidth(int(theme.metric("column.chip-button")))
        button.setToolTip(
            "Hand the pool back — clear every effect's share and return this power to "
            "one effect at a time."
        )
        button.clicked.connect(lambda _checked=False, p=power: self._release_effect_pool(p))
        return button

    def _release_effect_pool(self, power: Power) -> None:
        """Clear every effect's share; the *Using* picker decides again."""

        for effect in power.effects:
            effect.dynamic_points = None
            effect.current_rank = None  # the notch it was held at goes with the share
        self._rebuild_list()
        self.runtimeChanged.emit()

    def _rename_group(self, group: PowerGroup) -> None:
        """Prompt for a new group name; blank clears it back to the mode label."""
        placeholder = _MODE_LABELS.get(_group_mode(group), _MODE_LABELS[STRUCTURE_INDEPENDENT])
        name, ok = QInputDialog.getText(
            self,
            "Rename group",
            "Group name:",
            QLineEdit.EchoMode.Normal,
            group.name or placeholder,
        )
        if not ok:
            return
        group.name = name.strip()
        self._rebuild_list()
        self.changed.emit()

    def _set_group_mode(self, group: PowerGroup, mode: str) -> None:
        """Put a group on one of the toggle's four segments.

        *Dynamic array* is a view rather than a stored mode (:func:`_group_mode`), so it
        is written as an array whose every member carries the flag, and picking plain
        *Array* is how it is taken back off. The cost math is untouched by that: it
        still prices Dynamic per member, which is what the rules do.
        """

        dynamic = mode == MODE_ARRAY_DYNAMIC
        group.mode = STRUCTURE_ARRAY if dynamic else mode
        for child in group.children:
            child.dynamic = dynamic
            if not dynamic:
                child.dynamic_points = None
        self._normalize_arrays()
        self._rebuild_list()
        self.changed.emit()

    def _node_has_standing(self, node: PowerNode) -> bool:
        """Whether any leaf under *node* contributes a standing (non-instant) bonus."""
        return any(power_has_standing_effect(p, self._data) for p in self._leaf_powers(node))

    def _node_is_gateable(self, node: PowerNode) -> bool:
        """Whether any leaf under *node* carries a runtime gate (so it can be turned off)."""
        return any(power_runtime_gates(p, self._data) for p in self._leaf_powers(node))

    # -- what a click on a card does --------------------------------------
    def _activation_role(self, node: PowerNode, parent: PowerGroup | None) -> str:
        """What clicking this node's card does: ``"select"``, ``"toggle"``, or ``""``.

        ``"select"`` — the node is a member of a multi-member *array* in which something
        stands on the sheet, so clicking it makes it the live alternate (its siblings
        drop). Both a standing member and an instant one behave this way; an all-instant
        array has nothing to keep active, so its members aren't clickable.

        ``"toggle"`` — the node carries its own on/off switch: a Linked *group* that is
        gateable and standing (its members switch as one), or a standalone power that
        has a runtime gate *and* a standing bonus.

        ``""`` — nothing to switch (a permanent ungated power, a pure-instant attack),
        or the switch belongs to an ancestor: anything under a Linked group is driven by
        that group's card, so its own card stays inert and lets the click bubble up.

        The Linked test looks at the whole ancestor chain, not just the immediate
        parent: a member may sit inside a sub-group (dragging one card onto another
        always makes an *Independent* group) that is itself inside the Linked group, and
        it still has to switch with its linked siblings rather than sprout its own switch.
        """
        if self._selectable_array_member(parent):
            # ...unless the array's points are split, when the pool decides who is
            # running and selecting a member decides nothing. The click used to be armed
            # anyway: the cursor promised something, `active_child_id` quietly moved, and
            # nothing on screen changed. `_arm_activation` still tooltips *why*, because
            # a card that has silently stopped being a control is worse than one that
            # says it has.
            return "" if _pool_is_split(parent) else "select"
        if self._linked_ancestor(node) is not None:
            return ""
        if isinstance(node, PowerGroup):
            if (
                node.mode == STRUCTURE_LINKED
                and self._node_is_gateable(node)
                and self._node_has_standing(node)
            ):
                return "toggle"
            return ""
        if power_runtime_gates(node, self._data) and power_has_standing_effect(node, self._data):
            return "toggle"
        return ""

    def _selectable_array_member(self, parent: PowerGroup | None) -> bool:
        """Whether a card in *parent* is one of a live array's mutually exclusive members.

        An array only has something to select between if it has two of them and at least
        one *stands* on the sheet — an all-instant array keeps nothing active, so its
        members are not switches. Asked by :meth:`_activation_role` (does a click do
        anything) and :meth:`_node_is_inactive` (is this one running), which have to
        agree about what an array is even when the pool has taken the click away.
        """

        return (
            isinstance(parent, PowerGroup)
            and parent.mode == STRUCTURE_ARRAY
            and len(parent.children) >= 2
            and any(self._node_has_standing(child) for child in parent.children)
        )

    def _node_is_inactive(self, node: PowerNode, parent: PowerGroup | None, role: str) -> bool:
        """Whether the card should be drawn in its dimmed, switched-off state."""
        if self._selectable_array_member(parent):
            # Which member is *running* rather than which is selected: once the pool is
            # split every Dynamic member holding a share is live at once, so dimming all
            # but the selected one would contradict the numbers on the sheet. Asked of
            # the array rather than of the card's role, because a split takes the role
            # away and the dimming has to outlive it.
            if not any(child is node for child in live_array_children(parent)):
                return True
            # ...and a Dynamic member the fallback woke up while its own share dial sits
            # on "Off" is switched off, whatever the array did with it (see
            # :meth:`_on_share_dialled`). Only a Dynamic member can be parked that way,
            # so an ordinary alternate is left reading exactly as it always did.
            return bool(node.dynamic) and not self._member_is_running(node)
        if role == "toggle":
            if isinstance(node, PowerGroup):
                return not self._group_is_active(node)
            return not self._power_is_active(node)
        return False

    def _arm_activation(
        self,
        card: DraggableCard,
        node: PowerNode,
        parent: PowerGroup | None,
        interactive: bool,
    ) -> None:
        """Make the card its power's switch, if the power has one to offer.

        The card *is* the control — there is no separate checkbox — so the whole frame
        becomes the click target, and says so (see :meth:`DraggableCard.set_clickable`).
        ``interactive`` is ``False`` for a card inside a switched-off Linked group, which
        still shows its state but can't be clicked back on past its group.

        A member of a *split* array is the one card that gets the hint without the
        click: the pool has taken the decision over, so there is nothing to arm, but a
        card that has quietly stopped being a control needs to say so more than one that
        still is.
        """
        role = self._activation_role(node, parent)
        if self._selectable_array_member(parent) and _pool_is_split(parent):
            card.setToolTip(_SPLIT_SELECT_HINT)
            return
        if not (role and interactive):
            return
        card.set_clickable(True)
        card.setToolTip(_CLICK_HINTS[role])
        card.clicked.connect(lambda n=node, p=parent, r=role: self._on_card_clicked(n, p, r))

    def _show_activation(
        self, card: DraggableCard, node: PowerNode, parent: PowerGroup | None
    ) -> None:
        """Put the card into its on/off look, easing there when the state just changed.

        Every runtime setter ends in :meth:`_rebuild_list`, because flipping one power
        can change the numbers printed on *another* card (switching off an Enhanced
        Trait restates a Strength-Based Damage). So no card survives a toggle and there
        is nothing to animate across it. Instead the section remembers, per node id, the
        progress each card was last *showing* — and a freshly built card picks up from
        exactly there and eases to its new target. Because the running animation writes
        that progress back on every frame, a second click mid-transition resumes from
        where the eye left off rather than snapping.

        Called once the card's children exist, so the type scaling can see them.
        """
        role = self._activation_role(node, parent)
        target = 1.0 if self._node_is_inactive(node, parent, role) else 0.0
        previous = self._card_off_prev.get(node.id)
        self._card_off[node.id] = target
        if previous is None or previous == target or self.TRANSITION_MS <= 0:
            card.set_off_progress(target)
            return
        card.set_off_progress(previous)
        ease = QVariantAnimation(card)
        ease.setStartValue(previous)
        ease.setEndValue(target)
        ease.setDuration(self.TRANSITION_MS)
        ease.setEasingCurve(QEasingCurve.Type.OutCubic)
        ease.valueChanged.connect(lambda value, c=card, i=node.id: self._on_ease(c, i, value))
        ease.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_ease(self, card: DraggableCard, node_id: str, progress: float) -> None:
        card.set_off_progress(progress)
        self._card_off[node_id] = progress

    def _on_card_clicked(self, node: PowerNode, parent: PowerGroup | None, role: str) -> None:
        """Apply a card click: select an array's live alternate, or flip a switch."""
        if role == "select":
            if isinstance(parent, PowerGroup) and active_array_child(parent) is not node:
                # An array always keeps exactly one live member, so clicking the current
                # one is a no-op rather than switching the whole array off.
                self._set_array_active(parent, node.id)
        elif role == "toggle":
            if isinstance(node, PowerGroup):
                self._set_group_active(node, not self._group_is_active(node))
            else:
                self._set_power_active(node, not self._power_is_active(node))

    def _set_array_active(self, group: PowerGroup, child_id: str) -> None:
        """Select an array member as the live alternate and switch it on.

        Only the selected member contributes to the sheet (:func:`live_powers` descends
        into it alone), so its siblings' bonuses drop off automatically. The newly-live
        member also has its runtime gates flipped on so its effect actually applies.
        """
        group.active_child_id = child_id
        located = self._locate(child_id)
        if located is not None:
            for member in self._leaf_powers(located[0]):
                member.activated = True
                member.item_present = True
                for effect in member.effects:
                    effect.toggled_on = True
        self._rebuild_list()
        self.runtimeChanged.emit()

    def _ungroup(self, group: PowerGroup) -> None:
        """Dissolve a group, splicing its members back into the group's own slot."""
        located = self._locate(group.id)
        if located is None:
            return
        _node, siblings, index, _parent = located
        siblings[index : index + 1] = group.children
        self._after_structural_change()

    # -- leaf power card --------------------------------------------------
    def _make_card(
        self, power: Power, parent: PowerGroup | None, interactive: bool = True
    ) -> QWidget:
        """A stat-block card for one power: header, description, effects, roll line.

        Each effect carries its full game-term breakdown inline (the same data the Power
        Constructor shows while building), rendered quietly under the effect's name so
        the derived system values are always on the page, never behind a hover.

        The card body doubles as the power's on/off switch — see
        :meth:`_arm_activation` and :meth:`_show_activation`.
        """
        card = DraggableCard(power.id)
        self._arm_activation(card, power, parent, interactive)
        self._arm_card_menu(card, power)
        layout = QVBoxLayout(card)
        layout.addWidget(self._header_row(power, card, parent))

        if power.description:
            desc = QLabel(power.description)
            desc.setWordWrap(True)
            desc.setStyleSheet(muted_style(italic=True))
            layout.addWidget(desc)

        effects = self._effects_block(power)
        if effects is not None:
            layout.addWidget(effects)

        # Under the breakdown that names the effects, above the dials that turn one of
        # them up: which of an array's effects is running is the choice you make first.
        selector = self._effect_selector(power, interactive)
        if selector is not None:
            layout.addWidget(selector)

        # A Dynamic member's share of its array's pool, made on the same slider a rank
        # is: the share and the rank it buys are one gesture rather than a number typed
        # into a dialog and a rank worked out afterwards.
        share = self._share_dial(power, parent, interactive)
        if share is not None:
            layout.addWidget(share)
            if interactive:
                card.keep_lit(share)
        for dial in self._effect_share_dials(power, interactive):
            layout.addWidget(dial)
            if interactive:
                card.keep_lit(dial)
        # ...and now that they have registered, let the coordinator drive the header's
        # readout, the same handover :meth:`_make_group_card` makes one level up.
        effect_split = self._splits.get(power.id)
        effect_readout = self._pool_labels.get(power.id)
        if effect_split is not None and effect_readout is not None:
            effect_split.set_readout(effect_readout)
            effect_split.restate()

        # A dialled effect is a range, not a switch: a slider over the ranks the wielder
        # can hold it at, under the effect breakdown that explains what each notch is
        # worth and above the dice, with the rest of the mid-play controls.
        # Every dial stays at full strength while the card recedes: zero is off and
        # sliding up wakes the power, so a dial is the one live control on a card that
        # is showing itself switched off (:meth:`DraggableCard.keep_lit`). Only while it
        # really is live — inside a switched-off Linked group it is inert, and an inert
        # control recedes with everything else it cannot do.
        for dial in self._rank_dials(power, parent, interactive, shared=share is not None):
            layout.addWidget(dial)
            if interactive:
                card.keep_lit(dial)

        # A dedicated footer for the numbers that come up mid-play — one line per roll.
        # A power that rolls nothing gets neither the footer nor its rule.
        rolls = self._rolls_block(power)
        if rolls is not None:
            layout.addWidget(hline_separator())
            layout.addWidget(rolls)
        self._show_activation(card, power, parent)
        return card

    def _header_row(
        self,
        power: Power,
        card: DraggableCard,
        parent: PowerGroup | None,
    ) -> QWidget:
        """Name + PL warning on the left; the cost and edit/remove chrome on the right,
        led by a drag grip (hidden when locked).

        Returns a host widget (not a bare layout) so every child has a parent the
        moment it is created. Calling ``setVisible(True)`` on a *parentless* widget
        shows it as a momentary top-level window — on Windows that flashes a small
        window on screen and is slow to realize; the edit/remove buttons hit exactly
        that path, so the header must own them before their visibility is set.
        """
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)

        grip = DragHandle()
        grip.dragStarted.connect(card.start_drag)
        layout.addWidget(grip)
        grip.setVisible(not self._locked)

        name = QLabel(power_display_name(power, self._data))
        # The size goes on the QFont, not into the stylesheet: a stylesheet font-size
        # would outrank the card's own font and so sit out the switched-off transition.
        font = name.font()
        font.setPointSizeF(theme.font_size("size.card-name"))
        # A Debilitated condition naming this power loses it — strike the header through
        # and redden it (display-only; the power's point cost is untouched).
        if power.name and power.name in debilitated_traits(self._character, self._data):
            name.setStyleSheet(tinted_style("tint.worse"))
            font.setStrikeOut(True)
            name.setToolTip("Debilitated — this power is effectively lost")
        else:
            name.setStyleSheet(BOLD_STYLE)
        name.setFont(font)
        layout.addWidget(name)

        # A stunt says so, and says what it is a stunt *of*: it is a card of its own
        # rather than a member of the source power's array, so the relationship is only
        # ever visible here. Muted, because it is provenance rather than a number.
        if power_is_stunt(power):
            source = stunt_source(power, self._character)
            of = power_display_name(source, self._data) if source else "a power now gone"
            badge = QLabel(f"✦ stunt of {of}")
            badge.setStyleSheet(muted_style(italic=True))
            badge.setToolTip(
                "A power stunt: a temporary alternate effect bought with Extra Effort "
                "rather than with Power Points (p20). It costs nothing, it is not saved "
                "with the character, and it lasts as long as the scene does."
            )
            layout.addWidget(badge)

        # A power that breaks any build rule carries a warning marker naming every
        # breach; enforcement is a warning for now (see storage.pl_enforcement). This is
        # the same walk over the same POWER_CHECKS registry the Power Constructor's
        # warning band makes, so the two cannot disagree — before it, the card showed
        # Power Level and a stunt's ceiling alone, and a character built under a
        # different ruleset could carry an over-spent allocation, an over-budget imposed
        # effect or an over-budget minion with nothing on the sheet saying so.
        violations = power_violations(power, self._character, self._data)
        if violations:
            warning = QLabel("⚠")
            warning.setStyleSheet(tinted_style("tint.warning"))
            warning.setToolTip("\n".join(violations))
            layout.addWidget(warning)

        # A homerule power (one carrying a Dev-mode override or a blank Custom modifier)
        # is badged so a bent value on the sheet is never mistaken for a by-the-book one.
        if power_is_homerule(power) or power_has_custom_modifier(power, self._data):
            homerule = QLabel("⌂")
            homerule.setStyleSheet(tinted_style("tint.homerule"))
            homerule.setToolTip(
                "Homerule power — carries manual (Dev-mode) overrides or a custom modifier."
            )
            layout.addWidget(homerule)
        layout.addStretch()

        # The effect-level twin of a group header's pool line. An array exists at two
        # levels and so does its pool, but only the group level ever said so: a power
        # whose own effects were split coordinated their sliders live and stated the
        # pool nowhere, which is the one number no single slider can show.
        pool = self._effect_pool_readout(power)
        if pool is not None:
            layout.addWidget(pool)
        release = self._effect_pool_release(power)
        if release is not None:
            layout.addWidget(release)
        if pool is not None or release is not None:
            layout.addSpacing(int(theme.metric("space.lg")))

        # Inside an array group a non-base member contributes only its flat pooled cost;
        # every other card shows its full assembled cost (node_display_cost decides). A
        # stunt contributes nothing, and "0 PP" beside a real build reads as a bug — so it
        # says what it is instead, and keeps the number it *would* have cost in the
        # tooltip, since that is the number its ceiling is measured against.
        if power_is_stunt(power):
            cost = QLabel("Stunt")
            cost.setToolTip(
                f"Bought with Extra Effort, not with points — it would cost "
                f"{power_total_cost(power, self._data, self._character)} PP."
            )
        else:
            cost = QLabel(f"{node_display_cost(power, parent, self._data, self._character)} PP")
            self._explain_cost(cost, power)
        cost.setEnabled(False)
        layout.addWidget(cost)

        # Add each button to the (host-owned) layout *before* setting visibility:
        # addWidget reparents it to `host`, so setVisible acts on a parented child.
        edit = QPushButton("✎")
        edit.setFixedWidth(int(theme.metric("column.chip-button")))
        edit.setToolTip("Edit this power")
        edit.clicked.connect(lambda _checked=False, p=power: self._edit_power(p))
        layout.addWidget(edit)
        edit.setVisible(not self._locked)

        remove = QPushButton("✕")
        remove.setFixedWidth(int(theme.metric("column.chip-button")))
        remove.setToolTip("Remove this power")
        remove.clicked.connect(lambda _checked=False, p=power: self._remove_power(p))
        layout.addWidget(remove)
        remove.setVisible(not self._locked)
        return host

    # -- the array's live effect ------------------------------------------
    def _effect_selector(self, power: Power, interactive: bool) -> QWidget | None:
        """The picker for which of an array power's own effects is in use; ``None``
        for every power that is not one, which is nearly all of them.

        Also ``None`` **once the power's points are split**: every effect holding a share
        is running at the same time, so the selection has stopped deciding anything and
        a picker that still moved ``active_effect`` would be a control with nothing
        visible behind it. That is the same bargain ``_activation_role`` strikes one
        level up, where a split array's member cards stop being clickable. The way back
        is the same too — slide every share to nothing.
        """

        if not power_effects_are_array(power) or live_array_effects(power):
            return None
        titles = [effect_title(effect, self._character, self._data) for effect in power.effects]
        current = active_array_effect_index(power, self._data, self._character)
        selector = _EffectSelector(titles, current, interactive)
        selector.effectPicked.connect(lambda index, p=power: self._on_effect_picked(p, index))
        return selector

    def _on_effect_picked(self, power: Power, index: int) -> None:
        """Put an array power onto one of its own effects.

        Runtime, so it emits ``runtimeChanged`` rather than ``changed`` — the same
        bargain the rank dial and the array-member click strike. The rebuild is what
        redraws every other effect's summary as no longer contributing.
        """

        power.active_effect = index
        self._rebuild_list()
        self.runtimeChanged.emit()

    # -- the Dynamic share dial -------------------------------------------
    def _share_dial(
        self, node: PowerNode, parent: PowerGroup | None, interactive: bool
    ) -> QWidget | None:
        """The slider a Dynamic member's share is made on — and its **only** slider.

        The split used to be a modal dialog of spin boxes reached from the group header.
        It is the same slider a rank is dialled on now, for the reason the dialog itself
        had to keep explaining: a share is only ever interesting for the rank it buys, so
        the notches *are* the ranks and the points are what each one spends
        (:func:`~mm_companion.core.rules.dynamic_share_steps`). A member costing 2 points
        a rank moves the split 2 points a notch and one costing 1 moves it by 1, so every
        stop is a legal price for both of them.

        It **replaces** the rank dial rather than sitting beside it — :meth:`_rank_dials`
        stands down for a member under a share. Two sliders each claiming the same rank
        deadlocked: the rank one wrote a value the share then clamped away, and because
        the clamp was a minimum that written-and-clamped value survived as a floor the
        share could no longer lift.

        The groove **ends where the pool does**: a member can be dragged to its
        right-hand end and no further, and it gains a division for every point a sibling
        hands back. A Growth 6 holding all six of a six-point pool leaves an Elongation 3
        with a slider of one notch; drop the Growth a rung and the Elongation has two.
        The slider is always drawn, even at one notch — it used to disappear outright
        once its siblings had spent the pool, with no way to give it points again.
        """

        if parent is None or not node.dynamic or parent.mode != STRUCTURE_ARRAY:
            return None
        pool = array_pool_points(parent, self._data)
        full = dynamic_member_cost(node, self._data)
        if pool <= 0 or full <= 0:
            return None
        held = max(0, node.dynamic_points or 0)
        others = sum(c.dynamic_points or 0 for c in parent.children if c.dynamic) - held
        return self._build_share_dial(
            node,
            full,
            held,
            max(0, pool - others),
            interactive,
            split=self._split_for(parent, pool),
            fallback=self._fallback_share(node, parent, full),
        )

    def _effect_share_dials(self, power: Power, interactive: bool) -> list[QWidget]:
        """The same slider one level down: a power's *own* Dynamic effects, sharing its pool.

        An array exists at two levels and so does its pool, so the control does too. The
        arithmetic is identical — only what holds the share differs, an effect rather
        than a whole card — which is why both go through :meth:`_build_share_dial` and
        cannot price the same rank two ways.
        """

        if power.structure != STRUCTURE_ARRAY or len(power.effects) < 2:
            return []
        pool = power_pool_points(power, self._data)
        if pool <= 0:
            return []
        assigned = sum(e.dynamic_points or 0 for e in power.effects if e.dynamic)
        dials: list[QWidget] = []
        for effect in power.effects:
            if not effect.dynamic:
                continue
            full = dynamic_member_cost(effect, self._data)
            if full <= 0:
                continue
            held = max(0, effect.dynamic_points or 0)
            dial = self._build_share_dial(
                effect,
                full,
                held,
                max(0, pool - (assigned - held)),
                interactive,
                power=power,
                split=self._split_for(power, pool),
                fallback=self._fallback_share(effect, power, full),
            )
            if dial is not None:
                dials.append(dial)
        return dials

    def _build_share_dial(
        self,
        node,
        full: int,
        held: int,
        affordable: int,
        interactive: bool,
        power: Power | None = None,
        split: _SplitGroup | None = None,
        fallback: tuple[int, int | None] = (0, None),
    ) -> QWidget | None:
        """One share slider, whatever holds the share.

        *affordable* is the most this member could pay for — what is unassigned plus what
        it already holds — and it is where the groove ends, so the right-hand end of the
        track is always the most the player can ask for. A share stored above it (a
        rebuild moved the pool under a split already made) still seats the handle at its
        true notch rather than quietly reading low, so the number on the card and the
        number charged against the pool are the same one.

        *fallback* is the notch a member running on **no** share at all is standing on —
        an unsplit array's selected alternate (:meth:`_fallback_share`). It is a reading
        rather than a claim, so it is what the handle is drawn on and what a commit
        landing back on it counts as *no change*, while the pool goes on being counted
        without it until the player actually moves the handle.

        **And its label says so**, by naming the rank without a price. Every other notch
        is priced because moving the handle there spends that many points; the one a
        member was *found* on spends nothing, and reading ``10 PP · Growth 6`` under a
        header saying the array is not split invited exactly the wrong conclusion — that
        this member was holding the whole pool. :class:`_SplitGroup` already counts a
        phantom as zero (:meth:`_SplitGroup._held`); this is the label agreeing with the
        arithmetic. The price appears the moment the handle moves, which is the moment
        the points are genuinely spoken for.
        """

        notches = self._share_notches(node, full, held)
        if len(notches) < 2:
            return None
        steps = [points for points, _rank in notches]
        seat = fallback if fallback[0] else (held, self._held_rank(node, held, full))
        index = self._seat(notches, *seat)
        labels = {
            i: self._share_label(node, points, rank, full, power)
            for i, (points, rank) in enumerate(notches)
        }
        if fallback[0]:
            labels[index] = self._share_label(
                node, notches[index][0], notches[index][1], full, power, priced=False
            )
        dial = _RankDial(
            self._share_caption(node, power),
            len(steps) - 1,  # brought in to what is affordable by set_ceiling below
            index,
            labels,
            interactive,
        )
        dial.set_ceiling(max(self._share_index(steps, affordable), index))
        if split is not None:
            split.add(dial, steps, phantom=bool(fallback[0]))
        dial.rankPicked.connect(
            lambda picked, n=node, nt=notches, seated=index: self._on_share_dialled(
                n, nt, picked, seated
            )
        )
        return dial

    @staticmethod
    def _seat(notches: list[tuple[int, int | None]], points: int, rank: int | None) -> int:
        """Which notch a member sitting on *points*, standing at *rank*, is drawn on.

        The last notch at the share it holds — the most that share buys, which is what a
        member nobody has held below its ceiling is running at — unless one of the
        notches at that price is the rank it is actually standing at, which is the whole
        reason two of them can share a price (:meth:`_share_notches`).
        """

        seat = 0
        for index, (price, hold) in enumerate(notches):
            if price != points:
                continue
            seat = index
            if rank is not None and hold == rank:
                return index
        return seat

    def _held_rank(self, node, points: int, full: int) -> int | None:
        """The rank this member is deliberately held at, below what its share buys.

        The model's side of the notch the player stopped on, and read by exactly the rule
        the sheet reads it by (:func:`~mm_companion.core.rules.dynamic_held_rank`), so the
        handle and the rank on the card can never come from two different answers.
        """

        effects = _held_effects(node)
        if len(effects) != 1:
            return None
        return dynamic_held_rank(effects[0], points, full)

    def _hold_member(self, node, hold: int | None) -> None:
        """Pin a member to the rank its notch names, or let its share decide again.

        Written only where it is doing work — a notch that stands exactly where its share
        already reaches stores nothing — so a file gains a ``current_rank`` only for a
        member deliberately held below its ceiling, and a value left behind by a rank dial
        the effect had before it joined the pool is cleared the first time the slider is
        touched.
        """

        effects = _held_effects(node)
        if len(effects) != 1:
            return
        effect = effects[0]
        if hold is None or hold <= 0:
            effect.current_rank = None
            return
        full = dynamic_member_cost(node, self._data)
        buys = dynamic_rank_share(effect.rank, node.dynamic_points or 0, full)
        effect.current_rank = hold if hold < buys else None

    def _fallback_share(self, node, host, full: int) -> tuple[int, int | None]:
        """The notch a member holding *no* share is running on anyway — share and rank.

        Zero for every member the pool is actually rationing — its own share seats its
        dial, and that is the whole story. But an array whose shares are all handed back
        is not split at all, and :func:`~mm_companion.core.rules.live_array_children`
        then falls back to running its **selected alternate** at the rank it stands at;
        one level down, :func:`~mm_companion.core.rules.effect_is_selected` says the same
        of a power's own effects. That member is running, so its one slider has to say
        where — seating it on "Off" is the same lie the zero notch was fixed for at the
        other end (see :meth:`_on_share_dialled`), and the one an untouched Dynamic array
        told about every member it was running.

        Priced through the exact inverse of what a share buys
        (:func:`~mm_companion.core.rules.dynamic_share_points`), so the handle lands on
        the notch that *would* buy what the member is running: a member at its full rank
        lands on its whole cost, which is what an unsplit array's alternate is worth. One
        holding several effects is priced whole rather than by whichever of them was
        asked, since the split rations them together anyway.

        A reading, not a claim: nothing is written, and the array's :class:`_SplitGroup`
        leaves these points out of the pool it counts down until the handle is moved. The
        rank comes back with the price so the handle lands on the right one of the two
        notches that can share it (:meth:`_seat`).
        """

        if node.dynamic_points is not None or not self._member_is_running(node):
            return (0, None)
        if isinstance(node, PowerEffectInstance):
            if not effect_is_selected(host, node, self._data, self._character):
                return (0, None)
            effects = [node]
        else:
            if not any(child is node for child in live_array_children(host)):
                return (0, None)
            effects = _held_effects(node)
        if len(effects) != 1:
            return (full, None)
        rank = effect_current_rank(effects[0], self._data, self._character)
        return (dynamic_share_points(effects[0].rank, rank, full), rank)

    def _split_for(self, host, pool: int) -> _SplitGroup:
        """The coordinator joining one array's share sliders, made on first ask.

        Keyed by the host's id, so a group of cards and a power's own effects each get
        their own — an array exists at two levels and the two pools are separate.
        """

        split = self._splits.get(host.id)
        if split is None:
            split = _SplitGroup(pool)
            self._splits[host.id] = split
        return split

    def _share_caption(self, node, power: Power | None) -> str:
        """What the share dial calls itself — the same word the rank dial would use.

        A Dynamic member's slider *is* its rank slider, so it answers to the same name: a
        size effect's ladder is captioned "Size" whether or not a pool is rationing it,
        and a member holding more than one effect is captioned "Share" because its label
        already names them all.
        """

        effects = _held_effects(node)
        if len(effects) != 1:
            return "Share"
        host = power if power is not None else (node if isinstance(node, Power) else None)
        if host is not None and size_steps(host, effects[0], self._character, self._data):
            return "Size"
        return "Rank"

    def _share_notches(self, node, full: int, held: int) -> list[tuple[int, int | None]]:
        """Every ``(share, rank)`` this member's slider can stop on, cheapest first.

        **One notch per rank, not per price.** A share buys a ceiling rather than a rank,
        and the two are different ladders wherever a member does not cost a round number
        of points a rank: five points of a six-rank member costing five buys all six, and
        nothing at all buys exactly five. Pricing the notches meant that rank simply was
        not on the slider — a Growth could be Large or Gargantuan and never Huge, which
        for a size effect is not a rounding error but a rung the player wanted (bigger is
        easier to hit and impossible to hide). So every rank gets a notch, priced at the
        cheapest share that *reaches* it, and the notch carries the rank it stands at:
        the two notches that share a price differ by where they stop, which is what
        :func:`~mm_companion.core.rules.dynamic_held_rank` reads back.

        The rank is ``None`` for a member holding **several** effects, whose share
        rations them all together and which therefore has no single rank to stand at;
        those notches are the prices its effects can stop on, as they always were.

        A stored share that is not a notch (a hand-edited file, or a ladder that changed
        under it) is folded in the same way: rounding it down for display while the pool
        went on charging the full amount lost the difference the moment the dial was
        touched.
        """

        effects = _held_effects(node)
        notches: list[tuple[int, int | None]] = [(0, 0)]
        if len(effects) == 1:
            rank = effects[0].rank
            notches += [(dynamic_share_points(rank, r, full), r) for r in range(1, rank + 1)]
        else:
            prices: set[int] = set()
            for effect in effects:
                prices.update(p for p, _rank in dynamic_share_steps(effect.rank, full))
            notches += [(p, None) for p in sorted(prices) if p > 0]
        if held > 0 and all(points != held for points, _rank in notches):
            notches.append((held, None))
            notches.sort(key=lambda notch: notch[0])  # stable: the ranks keep their order
        return notches

    def _share_steps(self, node, full: int, held: int) -> list[int]:
        """What each notch of :meth:`_share_notches` **spends**, cheapest first.

        The ladder as the pool sees it, which is what bounds it: how much of it is
        reachable is :meth:`_RankDial.set_ceiling`'s business, and because the affordable
        notches are always a *prefix* of this list (they are the ones under a budget, and
        the list is sorted) bringing the end in never changes what a notch means. Two
        notches at one price are two ranks the same share can stop at, so the prefix
        holds and the ceiling lands on the higher of them.
        """

        return [points for points, _rank in self._share_notches(node, full, held)]

    @staticmethod
    def _share_index(steps: list[int], budget: int) -> int:
        """The highest notch *budget* points can pay for."""

        best = 0
        for index, points in enumerate(steps):
            if points <= budget:
                best = index
        return best

    def _share_label(
        self,
        node,
        points: int,
        hold: int | None,
        full: int,
        power: Power | None = None,
        priced: bool = True,
    ) -> str:
        """What one notch of the share dial costs and buys, named effect by effect.

        The rank *is* the answer the rules give — the book's own example is a Flight 5
        costing 10 points held to "1 rank of Flight" by the 2 assigned to it (p101) — so
        a notch reads "6 PP · Flight 3" rather than a fraction the reader has to convert.
        A share too small for even one rank of anything says so instead of reading as
        rank 0.

        *hold* is the rank the notch **stands** at where that is the player's to choose
        (:meth:`_share_notches`), and it is the only thing separating the two notches
        that share a price: "5 PP · Huge" is the one that stops there, and
        "5 PP · Gargantuan" beside it spends the same points on the next rung.

        **A size effect is named by the size it becomes**, exactly as the rank dial this
        slider replaces names it (:meth:`_dial_labels`). Moving a Growth into a Dynamic
        array used to swap its ladder of Large/Huge/Gargantuan for bare rank numbers, so
        the one control the player had left said less than the one it replaced — and
        "Huge" is the thing being chosen, while the rank is an accounting fact the card
        already prints.

        *priced* is off for the notch a member was merely **found** on rather than paid
        for (see :meth:`_build_share_dial`): the rank is true, the price is not yet.
        """

        if points <= 0:
            return "Off"
        effects = _held_effects(node)
        shares = [
            dynamic_rank_share(e.rank, points, full) if hold is None else hold for e in effects
        ]
        if not shares:
            return f"{points} PP" if priced else ""
        if not any(shares):
            return f"{points} PP · too few points to run"
        parts = [
            self._notch_name(node, effect, share, power)
            for effect, share in zip(effects, shares, strict=True)
        ]
        prefix = f"{points} PP · " if priced else ""
        return prefix + ", ".join(parts[:2]) + (", …" if len(parts) > 2 else "")

    def _notch_name(
        self, node, effect: PowerEffectInstance, share: int, power: Power | None
    ) -> str:
        """What one effect *is* at *share* ranks — its size category, or "Name rank".

        The same choice :meth:`_dial_labels` makes for the plain rank dial, made in the
        one other place a rank is put into words, so a size effect reads the same under a
        share as it does without one. A member holding several effects keeps the effect's
        name in front of the rung, since the label has to say which of them it means.
        """

        host = power if power is not None else (node if isinstance(node, Power) else None)
        steps = size_steps(host, effect, self._character, self._data) if host is not None else ()
        category = self._dial_labels(steps).get(share) if steps else None
        name = effect_display_name(effect, self._data)
        if category is None:
            return f"{name} {share}"
        return category if len(_held_effects(node)) == 1 else f"{name} {category}"

    def _on_share_dialled(
        self,
        node,
        notches: list[tuple[int, int | None]],
        index: int,
        seated: int | None = None,
    ) -> None:
        """Hand a member the share its slider was left on — and at zero, switch it off.

        Runtime, so it emits ``runtimeChanged`` like the rank dial it replaces. A notch at
        zero is stored as *no share at all* rather than a zero — the two behave
        identically and the model writes ``dynamic_points`` only when it is set, so
        sliding every member back to nothing leaves a saved character byte-for-byte what
        it was before anyone split the pool. That is also the way back to an ordinary
        array, which is what clearing the split used to be a button for.

        **Zero is off, the same as it is on the rank dial this slider replaces**, and it
        has to be said out loud here. Handing a share back ordinarily drops the member
        out of :func:`~mm_companion.core.rules.live_array_children` all by itself — but
        not the *last* one: with every share back the array falls back to its selected
        alternate at full rank, so a Growth parked on "Off" came straight back on and the
        sheet went on reading Gargantuan under a slider saying the power was off. So the
        notch flips the member's own master switches too, exactly as a click on its card
        would, and a notch above zero flips them back — every switch the card flips
        (:meth:`_set_member_running`), and only on this member's own leaves, which is
        what keeps a share from switching off a Linked group it happens to sit inside.

        A commit that lands where it started rebuilds nothing: the deferred commit and
        the live preview between them can both report a notch the model already holds,
        and tearing every card down to write the number that is already there is how a
        gesture ends up fighting the widget making it. *Where it started* is the notch the
        handle was **drawn** on (:meth:`_build_share_dial` passes its index back) and the
        switch: the share it holds for a member the pool is rationing, and the share it is
        running on for one the fallback woke (:meth:`_fallback_share`) — so leaving that
        handle where it sits keeps the array unsplit, while dragging it down to zero still
        puts the member down.

        A notch is a **share and a rank**, and both are written: two notches can spend the
        same points and stop at different ranks (:meth:`_share_notches`), so the index is
        what a commit is compared by and :meth:`_hold_member` stores the rank the handle
        stopped at whenever it is below what those points buy.
        """

        points, hold = notches[max(0, min(index, len(notches) - 1))]
        running = points > 0
        if seated is None:
            full = dynamic_member_cost(node, self._data)
            share = node.dynamic_points or 0
            seated = self._seat(notches, share, self._held_rank(node, share, full))
        if index == seated and self._member_is_running(node) == running:
            return
        node.dynamic_points = points or None
        self._hold_member(node, hold if running else None)
        self._set_member_running(node, running)
        self._rebuild_list()
        self.runtimeChanged.emit()

    @staticmethod
    def _member_is_running(node) -> bool:
        """Whether a Dynamic array member's own switches are on.

        Not whether it is *contributing* — that is the pool's question
        (:func:`~mm_companion.core.rules.live_array_children`) and the gates' — only
        whether the player has left this member switched on. A member is a whole card or
        sub-group at the group level and a single effect at the power's own level, and
        each answers with the flag its level switches.
        """

        if isinstance(node, PowerEffectInstance):
            return node.toggled_on
        return all(PowersSection._power_is_active(p) for p in PowersSection._leaf_powers(node))

    @staticmethod
    def _set_member_running(node, running: bool) -> None:
        """Switch one Dynamic member on or off, leaving its siblings alone.

        **Every** switch on the member's own leaves, not just ``activated``. A card
        click switches a power off through :meth:`_set_power_active`, which clears
        ``activated``, ``item_present`` and each effect's ``toggled_on`` together — so a
        share dialled up afterwards that raised only ``activated`` left the other two
        down, and :func:`~mm_companion.core.rules.effect_is_active` went on reading the
        member as off. The points were spent, the card lit up, and the sheet never moved:
        a Speed parked in a Dynamic array by a card click could not be bought back on.
        Which flags a given power's gates actually consult is
        :func:`~mm_companion.core.rules.effect_is_active`'s business, so this raises the
        same three the card does and lets it choose.

        *Its own leaves* is still the whole of the scope: reaching wider, as
        :meth:`_set_power_active` does for a Linked group, would let one member's share
        switch off a Linked group the array happens to sit inside.
        """

        if isinstance(node, PowerEffectInstance):
            node.toggled_on = running
            return
        for power in PowersSection._leaf_powers(node):
            power.activated = running
            power.item_present = running
            for effect in power.effects:
                effect.toggled_on = running

    # -- the rank dial ----------------------------------------------------
    @staticmethod
    def _rank_is_shared(power: Power, parent: PowerGroup | None) -> bool:
        """Whether this whole card's ranks are decided by a share of an array's pool.

        A Dynamic member of an array group is rationed as a unit — every effect under it
        is held down by the same proportion — so none of them has a rank of its own to
        dial. It is asked of the *parent*, which is the half :meth:`_rank_dials` used to
        miss: ``live_array_effects`` answers about a power's own effects and is empty for
        a leaf card, so a Growth that was a Dynamic member kept its Size dial and fought
        its Share dial for the same number.

        Necessary but **not sufficient**: whether the share dial actually gets built is
        :meth:`_share_dial`'s answer, and :meth:`_rank_dials` takes it as *shared*. This
        alone stood a card's rank dial down for every Dynamic member, including the ones
        the share dial then declined to draw for (a pool of nothing, a member costing
        nothing, a ladder with one rung) — which left the card with neither control and
        no way to turn the power up at all.
        """

        return parent is not None and parent.mode == STRUCTURE_ARRAY and power.dynamic

    def _rank_dials(
        self,
        power: Power,
        parent: PowerGroup | None,
        interactive: bool,
        shared: bool | None = None,
    ) -> list[QWidget]:
        """One :class:`_RankDial` per effect that has ranks worth choosing between.

        One way in, and the block names neither Growth nor Damage:
        :func:`~mm_companion.core.rules.effect_has_rank_dial`, which is the *Add a rank
        slider* checkbox in the constructor's Extended settings when the player has
        touched it and the ruleset's own answer when they have not — and the ruleset
        says yes to anything carrying a size readout, so a mod's own size effect gets a
        ladder without touching this file. That is the whole of the rule now: a Growth's
        checkbox used to be a control that changed nothing, because a size effect got its
        dial whatever the box said.

        A single-rank effect gets nothing: dialling a Growth 1 is exactly the card's own
        on/off switch, and a second way to press it is not a choice. A size effect is
        measured by its *rungs* rather than its rank there, since the ladder a Growth 1
        climbs is real even though its rank is one.

        **An effect whose rank a share decides gets nothing here** — whether the share
        is held by its own power (a Dynamic array of effects) or by the card it sits on
        (a Dynamic member of an array group). Its rank is not the player's to set
        directly; the share dial is the control that moves it, and it is deliberately the
        only one. Two sliders claiming one rank is what made a Growth's ladder snap back:
        this one wrote a rank the share then clamped away, and the clamped value stayed
        behind as a floor the share could not lift.

        The caption names the *effect* only when the power has more than one, which is
        the same bargain the dice footer's labels strike: on the ordinary single-effect
        Growth card "Size" is the whole story, while a Growth linked to a Shrinking needs
        to say which dial is which.
        """
        # *shared* is whether a share dial was really drawn for this card. Defaulted from
        # the flag alone so a caller that has not built one can still ask, but the card
        # passes the truth: a member whose share dial was declined keeps its rank dial.
        if self._rank_is_shared(power, parent) and (shared is not False):
            return []  # the whole card is rationed; see _share_dial
        dials: list[QWidget] = []
        for effect in power.effects:
            if any(e is effect for e in live_array_effects(power)):
                continue  # its rank is its share; see _share_dial
            steps = size_steps(power, effect, self._character, self._data)
            sized = len(steps) >= 2
            if not effect_has_rank_dial(effect, self._data) or not (sized or effect.rank > 1):
                continue
            caption = "Size" if sized else "Rank"
            if len(power.effects) > 1:
                caption = effect_title(effect, self._character, self._data)
            # Where the handle sits. A *size* effect is positioned by whether it is
            # standing on the sheet — a Growth that is switched off is nowhere, not at
            # rank 1. An instant effect (a Damage) never "stands" at all, so asking the
            # same question would peg every blast card at Off; what matters there is
            # simply whether the card is switched on.
            standing = (
                effect_stands(power, effect, self._data, self._character)
                if sized
                else self._power_is_active(power)
                and self._character is not None
                and any(p is power for p in live_powers(self._character.powers))
            )
            dial = _RankDial(
                caption,
                effect.rank,
                effect_current_rank(effect, self._data, self._character) if standing else 0,
                self._dial_labels(steps),
                interactive,
            )
            dial.rankPicked.connect(
                lambda rank, p=power, e=effect, g=parent: self._on_rank_dialled(p, e, g, rank)
            )
            dials.append(dial)
        return dials

    @staticmethod
    def _dial_labels(steps) -> dict[int, str]:
        """What each notch of the dial *is*, where a plain rank number won't do.

        A size effect's notches are named by the size the wielder becomes, one entry per
        rank rather than per rung: the Size Table clamps several ranks into one category
        near its ends, and a dial that skipped them would refuse to stop where the
        player put it. Zero always reads "Off"; anything the map doesn't cover falls back
        to its bare rank.
        """
        labels = {0: "Off"}
        for step in steps:
            for rank in range(step.rank, step.last_rank + 1):
                labels[rank] = step.category
        return labels

    def _on_rank_dialled(
        self, power: Power, effect: PowerEffectInstance, parent: PowerGroup | None, rank: int
    ) -> None:
        """Hold an effect at *rank* — or, at zero, switch the power off.

        The dial does exactly what a click on the card would have done, and then lands on
        the notch asked for. So moving it on a dormant power is a request to *be* that
        rank: it wakes the power (flipping its switches, or becoming its array's live
        alternate) at the chosen notch rather than at full rank. And sliding back to zero
        is the card's own click again — off it goes, which is what makes the dial a whole
        control rather than one that can only ever turn a power on. The one exception is
        an array's live member, where clicking the card is deliberately a no-op: an array
        always keeps exactly one member live, and dialling one to zero must not switch
        the whole array off.

        The rank is written before any activation path, so whichever one runs rebuilds
        the cards with it already in place.
        """
        role = self._activation_role(power, parent)
        if rank <= 0:
            if role != "select":
                self._set_power_active(power, False)  # rebuilds and emits
            else:
                self._rebuild_list()  # put the handle back where the array left it
            return
        effect.current_rank = rank
        if role == "select" and isinstance(parent, PowerGroup):
            if active_array_child(parent) is not power:
                self._set_array_active(parent, power.id)  # rebuilds and emits
                return
        if not self._power_is_active(power):
            self._set_power_active(power, True)  # rebuilds and emits
            return
        self._rebuild_list()
        self.runtimeChanged.emit()

    # -- effect summary and dice footer -----------------------------------
    def _effects_block(self, power: Power) -> QWidget | None:
        """The per-effect summary — each effect's extras/flaws beside its game terms.

        Drawn by :mod:`mm_companion.ui.cards.effects`, which the Equipment block
        renders its items with too: an item wraps a real :class:`Power`, so both
        cards show the same breakdown from the same code.
        """
        return effects_block(power, self._character, self._data)

    def _rolls_block(self, power: Power) -> QWidget | None:
        """The card's dice footer, one line per roll; ``None`` when nothing is rolled.

        A power that resolves without dice — a Protection, a permanent Enhanced Trait —
        gets no footer at all rather than a line saying so: the absence *is* the answer,
        and a placeholder on every passive power is pure noise.

        A pin names a roll by *which entry of* :func:`~mm_companion.core.rules.
        power_rolls` it is, so the footer is handed the power's id and turns a line
        index into a :class:`~mm_companion.core.rules.pins.PinRef` — including for
        the resistance line, which the wielder never rolls but which is the number a
        GM most wants on a mook's card.
        """
        specs = self._rolls(power)
        if not specs:
            return None
        footer = RollsFooter(
            specs,
            pins=self._pins,
            pin_ref=lambda index, pid=power.id: PinRef(PIN_POWER, pid, index),
        )
        footer.rollRequested.connect(self.rollRequested)
        footer.pinRequested.connect(self.pinRequested)
        footer.unpinRequested.connect(self.unpinRequested)
        return footer

    def set_pin_target(self, enabled: bool) -> None:
        """Whether a card's roll lines offer to pin at all."""
        self._pins.enabled = enabled

    def set_pinned(self, refs) -> None:
        """Which parameters are already on the card, so a line can offer Unpin."""
        self._pins.set_pinned(refs)

    def _rolls(self, power: Power):
        """Every roll the power calls for, as specs; see :func:`~mm_companion.core.
        rules.power_rolls`."""
        return power_rolls(power, self._character, self._data)

    def _rolls_lines(self, power: Power) -> list[str]:
        """One entry per die roll the power calls for; see module-level
        :func:`roll_lines`."""
        return roll_lines(power, self._character, self._data)

    def _remove_power(self, power: Power) -> None:
        located = self._locate(power.id)
        if located is None:
            return
        _node, siblings, index, _parent = located
        siblings.pop(index)
        self._after_structural_change()

    # -- runtime on/off ---------------------------------------------------
    @staticmethod
    def _power_is_active(power: Power) -> bool:
        """Whether every runtime switch on the power is currently in its 'on' state."""
        return power.activated and power.item_present and all(e.toggled_on for e in power.effects)

    def _set_power_active(self, power: Power, active: bool) -> None:
        """Flip the power's runtime switches — and its whole linked group — together.

        A single "Active" control drives whichever gate the power carries (Activation,
        Removable, or a Sustained toggle); ``rules.effect_is_active`` reads only the
        flags the power's gates make relevant. Members of a Linked group switch on/off
        as one, so if this power sits directly in a Linked group every leaf under that
        group is flipped too. The ``changed`` signal is already wired to refresh the
        stats/skills sections, so the boosted totals update live.
        """
        for member in self._linked_activation_set(power):
            member.activated = active
            member.item_present = active
            for effect in member.effects:
                effect.toggled_on = active
        self._rebuild_list()
        self.runtimeChanged.emit()

    def _group_is_active(self, group: PowerGroup) -> bool:
        """Whether every leaf power under a linked group is currently switched on."""
        return all(self._power_is_active(p) for p in self._leaf_powers(group))

    def _set_group_active(self, group: PowerGroup, active: bool) -> None:
        """Flip every power under a linked group on/off as one (Decision 3).

        A Linked group presents a single Active toggle rather than a per-card switch,
        so a permanent member drops off with its sustained sibling. Mirrors
        :meth:`_set_power_active` but spans the whole group's leaves.
        """
        for member in self._leaf_powers(group):
            member.activated = active
            member.item_present = active
            for effect in member.effects:
                effect.toggled_on = active
        self._rebuild_list()
        self.runtimeChanged.emit()

    def _linked_activation_set(self, power: Power) -> list[Power]:
        """Every leaf power that switches on/off together with *power*.

        Just ``[power]`` unless it sits somewhere under a Linked group, in which case
        all leaf powers under that group activate as one — including through any
        intervening sub-group, and out to the *outermost* Linked group when they nest.
        """
        linked = self._linked_ancestor(power)
        return self._leaf_powers(linked) if linked is not None else [power]

    def _ancestor_groups(self, node_id: str) -> list[PowerGroup]:
        """Every group enclosing *node_id*, outermost first (empty at the top level)."""

        def walk(nodes: list[PowerNode], trail: list[PowerGroup]) -> list[PowerGroup] | None:
            for node in nodes:
                if node.id == node_id:
                    return trail
                if isinstance(node, PowerGroup):
                    found = walk(node.children, [*trail, node])
                    if found is not None:
                        return found
            return None

        return walk(self._character.powers, []) or []

    def _linked_ancestor(self, node: PowerNode) -> PowerGroup | None:
        """The outermost Linked group enclosing *node*, or ``None``.

        Outermost rather than nearest: nested Linked groups all switch as one, so the
        activation set is the widest of them.
        """
        return next((g for g in self._ancestor_groups(node.id) if g.mode == STRUCTURE_LINKED), None)

    @staticmethod
    def _leaf_powers(node: PowerNode) -> list[Power]:
        """This node's leaves — one node's worth of :func:`leaf_powers`."""
        return list(leaf_powers([node]))

    def set_locked(self, locked: bool) -> None:
        """In read-only view mode, hide the editing entry points (Add / Remove / group chrome)."""
        self._locked = locked
        self._add_button.setVisible(not locked)
        self._rebuild_list()
