"""The dice roller for Mutants & Masterminds checks.

Set a bonus and a penalty (each a labeled slider linked to a spin box) and,
optionally, a Difficulty Class, then click the big D20 to play a short roll
animation and read the result. When a DC is set the result also shows the degree
of success/failure. Past rolls stack up in a history panel (each card can be
removed or saved); a saved roll can be named, dragged to reorder, and lives in a
persistent "quick rolls" strip pinned to the bottom for one-click reuse.

Three widgets, smallest first:

* :class:`DiceRollerPanel` (here) — the roll controls alone (settings, die + readout,
  quick rolls). Its shape, its Request row and its quick rolls are mixins in
  :mod:`mm_companion.ui.roller`; the rolling itself stays in this module.
* :class:`~mm_companion.ui.roller.history.LocalRollHistory` — the private list of
  what *this* app rolled, each card removable and saveable.
* :class:`~mm_companion.ui.roller.view.DiceRollerView` — a panel plus **whichever
  history is the right one** (see below). This is what the sheet's Dice block
  (:mod:`mm_companion.ui.sections.dice`) puts on screen.

Both composites **reflow to the space they are given** (see
:mod:`mm_companion.ui.reflow`), each deciding from its own width, which is what
lets the one Dice block suit the tall narrow right-hand pinned strip *and* a short
wide bottom one: a column of four parts, ``[panel][history]``, or one row of four
as the room grows. There is a fourth shape the reflow does *not* choose — the
compact one, where the whole window has shrunk to the roller alone and the panel
puts its quick rolls beside the die (:meth:`DiceRollerPanel.set_compact`, driven
by :mod:`mm_companion.ui.compact`).

**In a session the panel does not roll.** It asks the session — the server
resolves every roll and broadcasts the result, so nobody reports their own
number — and the answer arrives back on
:attr:`~mm_companion.ui.session_bridge.SessionBridge.rollAdded`. The tumble
animation covers the round trip, which is why it is a fixed 1.4 s rather than
instant, and every *other* seat holds the roll a little longer than that
(``session_bridge.PEER_ROLL_DELAY_MS``), so the roller sees the number first.
Outside a session the panel rolls locally.

Nothing here owns character state — it drives :mod:`mm_companion.core.dice`
directly (no game rules live here) and persists quick rolls through
:mod:`mm_companion.core.storage`.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence

from PySide6.QtCore import QElapsedTimer, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QIcon, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.diagnostics import log_exception
from mm_companion.core.dice import ROUTINE_DIE, CheckResult, reroll_d20, resolve_check, roll_d20
from mm_companion.core.rules import AttackChoice, RollSpec, targetable_attack
from mm_companion.core.session.model import KIND_ROLL
from mm_companion.ui import theme
from mm_companion.ui.action_grid import ActionGridPopup, GridItem, GridSection
from mm_companion.ui.action_menu import (
    ACTION_REROLL,
    NOTHING_TO_REROLL,
    action_tooltip,
    choose_from_menu,
)
from mm_companion.ui.flow_layout import FlowLayout
from mm_companion.ui.reflow import ReflowBox
from mm_companion.ui.roll_history import (
    HIDDEN_MARK,
    degree_label,
    escape_rich_text,
    reroll_label,
)
from mm_companion.ui.roller.action_lane import ActionLane, ActionLine, LineLimit, LineShift
from mm_companion.ui.roller.quick_rolls import QuickRollStrip, RollerQuickRollsMixin
from mm_companion.ui.roller.requests import RollerRequestsMixin
from mm_companion.ui.roller.shape import RollerShapeMixin
from mm_companion.ui.session_bridge import SessionBridge, live_session
from mm_companion.ui.svg_assets import die_resource, svg_pixmap
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import (
    ElidingLabel,
    help_markup,
    make_signed_spin_box,
    make_spin_box,
    tinted_style,
)

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

# What the chip and the readout say while a trait is loaded. Both are part of the
# same guard: a loaded chip arms the die, so the panel has to be obvious about it.
CHIP_PREFIX = "🎲 Rolling: "
IDLE_PROMPT = "Click the die to roll."

#: How far the one Bonus/Penalty slider runs either way from 0.
MODIFIER_LIMIT = 20
MODIFIER_LABEL = "Bonus/Penalty"

#: Written under an attack rolled with no DC and at no one: nothing judged the hit.
NO_DC_WARNING = (
    "⚠ No DC and no target: the hit was not judged. Put the target's Defense in the "
    "DC box, or aim the attack at a creature."
)

#: The roller's take-10 box (PDF p17): a routine check, rolled as a 10.
ROUTINE_LABEL = "Routine (take 10)"
ROUTINE_HINT = (
    "**Routine check (take 10):** the die counts as a 10, so there is no natural 1 or 20. For a "
    "check made without pressure, and for attacks or interactions against a minion.\n\nUnticks "
    "itself after one roll."
)


def d20_pixmap(ratio: float = 1.0, size: int | None = None) -> QPixmap:
    """The theme's d20 drawing, *size* pixels square, for a screen of *ratio*.

    A thin wrapper over :func:`~mm_companion.ui.svg_assets.svg_pixmap`, which loads,
    fits (the die is not square, so it is fitted rather than stretched) and caches.
    The artwork is vector, so *size* is what it is **rasterised** at: compact mode's
    smaller die is a second render, not a scaled-down pixmap. The size and the
    drawing are theme tokens read at call time.
    """
    if size is None:
        size = int(theme.metric("die.size"))
    return svg_pixmap(die_resource(theme.asset("die")), QSize(size, size), ratio)


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

    Worded as a **state**: the die is armed until the chip is cleared, and every
    later click folds this modifier in. A caption reading only "Athletics +9" was
    easy to skim past.
    """

    trait = f"{spec.label} {spec.modifier:+d}" if spec.modifier else spec.label
    return f"{CHIP_PREFIX}{trait}"


def readout_html(
    label: str,
    die: int,
    modifier: int,
    dc: int | None,
    degree: int | None,
    critical: bool = False,
    *,
    hidden: bool = False,
    routine: bool = False,
    warning: str = "",
) -> str:
    """The rich text under the die: the roll's name, the total and its working, the grade
    (and a *warning* line, such as :data:`NO_DC_WARNING`)."""

    total = die + modifier
    muted = theme.color("text.muted.rich")
    html = ""
    if label:
        html += (
            f"<span style='color:{theme.color('accent.dice')}'>"
            f"{escape_rich_text(label)}</span><br>"
        )
    made = "routine" if routine else "d20"
    html += (
        f"<span style='font-size:{theme.font_size('size.roll-readout')}pt'>"
        f"<b>{total}</b></span> "
        f"<span style='color:{muted}'>({made} {die} {modifier:+d})</span>"
    )
    if dc is not None:
        html += f" vs DC {dc}"
    if degree is not None:
        color = theme.color("tint.better" if degree > 0 else "tint.worse")
        html += f"<br><span style='color:{color}'>{degree_label(degree, critical, die)}</span>"
    if hidden:
        note = f"{HIDDEN_MARK} only you can see this roll"
        html += f"<br><span style='color:{muted}'>{note}</span>"
    if warning:
        html += f"<br><span style='color:{theme.color('tint.worse')}'>"
        html += f"{escape_rich_text(warning)}</span>"
    return html


class ReadoutLabel(QLabel):
    """The readout under the die, whose preferred width does not follow its text.

    A word-wrapped ``QLabel`` prefers a width worked out from whatever it is showing,
    and the die part beside the quick rolls (compact) or the other two parts (a row)
    takes exactly its preferred width. So every roll moved the die: a long name pushed
    the column wider and the quick rolls narrower, the next short one pulled them back,
    and the text re-wrapped each time. The preferred width is instead the width of a
    **template result** — two-digit numbers throughout and a several-degree failure on a
    natural 1 — drawn in the same markup and fonts, so it follows the theme and never
    the roll. A longer name wraps inside it.
    """

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self._reference: tuple[tuple, int] | None = None

    def _reference_width(self) -> int:
        html = readout_html("", 1, 20, 40, -4, True)
        key = (self.font().toString(), html)
        if self._reference is None or self._reference[0] != key:
            doc = QTextDocument()
            doc.setDefaultFont(self.font())
            doc.setHtml(html)
            margins = self.contentsMargins()
            width = math.ceil(doc.idealWidth()) + margins.left() + margins.right()
            self._reference = (key, width)
        return self._reference[1]

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = self._reference_width()
        return QSize(width, self.heightForWidth(width))


class DiceRollerPanel(
    RollerShapeMixin, RollerRequestsMixin, RollerQuickRollsMixin, ReflowBox, QWidget
):
    """The roll controls: the settings, the die with its readout, and the quick rolls.

    Reusable on its own — :class:`DiceRollerView` puts one beside a history panel,
    and GM Mode embeds one with ``hidden_option=True`` so the GM can roll without
    it reaching anybody's history.

    Its three parts **reflow** (see :mod:`mm_companion.ui.reflow`): a column when
    narrow, a row once there is width for all three.

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

    #: Whether a roll of ours is out with the session *right now*, which is exactly
    #: as long as a paired history may hold one of our own rolls back. It has to be
    #: a window rather than a standing flag: a held roll is only ever released by
    #: :attr:`sessionRollRevealed`, so an own roll reaching a history with no tumble
    #: waiting for it — a reconnect replaying the log, or a record that arrives
    #: after :meth:`_abandon_roll` gave up — would be held for the rest of the
    #: session and the player would watch their own rolls disappear. Raised ``True``
    #: before the request goes out and ``False`` on **both** ways the roll can end.
    awaitingOwnRoll = Signal(bool)

    #: The quick-roll strip gained, lost or renamed a chip, so a paired history's
    #: stars have to be re-lit. The *space* that costs is :attr:`contentChanged`'s
    #: business, raised alongside it.
    quickRollsChanged = Signal()

    #: The panel's content changed shape — a chip came or went, a spec was loaded or
    #: cleared. Whatever holds the panel has to share its space out again, because a
    #: splitter child with no stretch keeps the pixels it was given and a minimum
    #: going *down* provokes no resize at all. Raised alongside
    #: :attr:`quickRollsChanged` rather than folded into it: that one is about *what
    #: is in* the strip (the histories' stars), this one about how much room the
    #: panel now needs.
    contentChanged = Signal()

    #: The Request row's button was pressed — ask the table to roll this
    #: :class:`~mm_companion.core.rules.RollSpec`. The panel neither sends it nor
    #: rolls it: where a request goes is the same question its host already answers
    #: for a note (the session, or the private history), and the panel has no
    #: session of its own to answer it with.
    rollRequested = Signal(object)

    #: A tile of the Actions popup was picked — its action key (``"impress"``).
    #: Like :attr:`rollRequested` the panel only reports it: running an action means
    #: a dialog, a character and perhaps a session, none of which the panel has. The
    #: one exception is Reroll, which is the panel's own and never reported.
    actionRequested = Signal(str)

    #: A roll that had action lines on it has produced a number: the
    #: ``((key, value), …)`` it was made with. What the host makes of them is its own
    #: business — an All-Out Attack's Defense penalty lands on the sheet from here, hit or
    #: miss. Never raised for a reroll (that throws a roll already paid for) or for a roll
    #: the session never answered.
    linesRolled = Signal(object)

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
        # What the sheet asked to roll, if anything — see :meth:`load_spec`. A spec
        # that was *loaded* is sticky: it survives the roll so the sliders can be
        # nudged and the die thrown again.
        self._spec: RollSpec | None = None
        # Whether the loaded spec is a minion's (see :meth:`load_spec`).
        self._spec_minion = False
        # ...but one that arrived through :meth:`roll_spec` is not — see
        # :meth:`_settle_spec`.
        self._transient = False
        # The last roll this panel threw — its bonus, penalty, DC, hidden flag, label
        # and spec — which is what Reroll throws again (see :meth:`reroll`). ``None``
        # until there has been one. While :attr:`_rerolling` is set the roll in
        # flight reads its parameters from here rather than from the inputs.
        self._last_roll: dict | None = None
        # The roll in flight, until it has a result: only a roll that came back
        # becomes the one Reroll throws again — an abandoned one never resolved.
        self._pending_last: dict | None = None
        self._rerolling = False
        # Whether the readout holds a rolled number rather than the resting prompt,
        # which is what stops the prompt from overwriting a result — see
        # :meth:`_prompt_readout`.
        self._showing_result = False
        # Last chance to adjust a spec before it is loaded — see :meth:`set_localizer`.
        self._localizer: Callable[[RollSpec], RollSpec] | None = None
        # The traits the Request row offers, pushed in by the host (the panel reads
        # no game data) — see :meth:`set_roll_choices`. Empty until it is, which is
        # why the row starts hidden.
        self._request_specs: list[RollSpec] = []
        # Builds the Actions popup's sections, pushed in by the host — see
        # :meth:`set_action_menu`. Without one the popup offers Reroll alone.
        self._menu_builder: Callable[[Mapping[str, str | None]], Sequence[GridSection]] | None = (
            None
        )
        # The popup while it is open (it deletes itself on close).
        self._action_popup: ActionGridPopup | None = None
        # The character's attacks, for the attack picker — pushed in by the host
        # (see :meth:`set_attack_choices`). Without one, nothing needing an attack runs.
        self._attack_choices: Callable[[], Sequence[AttackChoice]] | None = None
        # The keys of the action lines the roll in flight used, which its result
        # spends (see :meth:`_spend_extras`). Empty on a reroll: that throws the last
        # roll's lines again and leaves the current ones alone.
        self._rolled_lines: tuple[tuple[str, int | None], ...] = ()
        # What the action lines do to a spec, pushed in by the host (see
        # :meth:`set_line_shift`); without one they ride on the roll and change nothing.
        self._line_shift: LineShift | None = None
        # The spec the roll in flight is *made of*: the loaded one with the action lines
        # applied (:meth:`with_lines`). The chip keeps the loaded one, so the lines can be
        # set again and the die thrown again. ``None`` between rolls.
        self._flight: RollSpec | None = None
        self._quick_rolls: list[dict] = self._load_quick_rolls()
        # Whether the panel is in its fixed compact shape (see :meth:`_apply_shape`),
        # in which case the width-driven reflow stands down — and the two separate
        # reasons it might be: the window has shrunk to the roller alone, or the
        # user prefers this shape everywhere.
        self._compact = False
        self._window_compact = False
        # The user's chosen shape, one of ``storage.DICE_LAYOUTS``. Compact is the
        # panel's own business; Extended is the *view's* (it pins the splitter's
        # axis) and reaches the panel only as the column lock below — under it the
        # controls stay stacked however wide the block, which is what makes Extended
        # the controls-beside-the-history shape rather than a row of four.
        self._preference = storage.dice_layout()
        self._column_locked = False

        # The three reflowing parts, in order. The layout's *direction* is what the
        # reflow changes, so the parts are added once and never re-parented.
        self._settings_part = self._build_roll_settings()
        self._die_part = self._build_die()
        self._quick_part = self._build_quick_rolls()
        # Holds the quick rolls beside the die in compact mode, and nothing at all
        # the rest of the time. Built once so the two parts have a stable place to
        # move to and back from.
        self._pair = QWidget(self)
        self._pair.setVisible(False)
        self._pair_box = QHBoxLayout(self._pair)
        self._pair_box.setContentsMargins(0, 0, 0, 0)
        self._pair_box.setSpacing(self.REFLOW_SPACING)

        self._box = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(self.REFLOW_SPACING)
        for part in self.reflow_parts():
            self._box.addWidget(part)

        self._rebuild_quick_strip()
        self.init_reflow()
        # And then, if the user prefers it, straight into the compact shape — the
        # reflow has to have laid the parts out once before they can be moved.
        self._apply_shape()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        self.sync_reflow()

    # -- construction --------------------------------------------------------

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

        # One two-way slider, centred on 0: a bonus to the right, a penalty to the
        # left. It used to be two, which spent a row of the panel saying one number.
        self._modifier_slider, self._modifier_spin = self._make_modifier()
        self._modifier_label = QLabel(MODIFIER_LABEL)
        self._modifier_spin.valueChanged.connect(self._mark_extras)

        grid.addWidget(self._modifier_label, 1, 0)
        grid.addWidget(self._modifier_slider, 1, 1)
        grid.addWidget(self._modifier_spin, 1, 2)

        # The action lane: one line per action riding on the next roll. It holds a
        # line's height even when empty, so picking an action never resizes the roller.
        self._lane = ActionLane()
        self._lane.changed.connect(self._on_lane_changed)
        grid.addWidget(self._lane, 2, 0, 1, 3)

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
            "Roll without anyone else seeing it. A hidden roll is never sent to the players."
        )
        self._hidden_check.setVisible(self._hidden_option)
        row = 4
        if self._hidden_option:
            grid.addWidget(self._hidden_check, row, 0, 1, 3)
            row += 1

        # Take 10 (PDF p17): for a check made under no pressure, and for any attack or
        # interaction against a minion (p25). One roll's worth — it unticks once the
        # roll is made, so it is never left on for the next one against a villain.
        self._routine_check = QCheckBox(ROUTINE_LABEL)
        self._routine_check.setToolTip(help_markup(ROUTINE_HINT))
        grid.addWidget(self._routine_check, row, 0, 1, 3)
        row += 1

        self._build_request_row(grid, row)
        self._build_action_row(grid, row + 1)
        return group

    def _build_action_row(self, grid: QGridLayout, row: int) -> None:
        """The **Actions** button, centred under the Request row, across the grid.

        It opens the Actions popup (:meth:`open_actions`): the combat maneuvers, the
        other actions, the actions taken against other characters (Impress, Area) and
        Reroll, as a grid of tiles. It replaced a row of push buttons that could only
        ever grow sideways.
        """

        self._actions_button = QPushButton("Actions")
        self._actions_button.setToolTip(
            "Combat maneuvers and actions: attack maneuvers, Aid, Grab, Impress, Reroll…"
        )
        self._actions_button.clicked.connect(self.open_actions)
        grid.addWidget(self._actions_button, row, 0, 1, 3, Qt.AlignmentFlag.AlignHCenter)

    def set_action_menu(
        self, build: Callable[[Mapping[str, str | None]], Sequence[GridSection]] | None
    ) -> None:
        """Have *build* make the Actions popup's sections each time it opens.

        Called with the panel's **own** actions — Reroll, mapped to ``None`` when there
        is a roll to throw again or to the reason there is not — to fold into what the
        host offers (see :func:`~mm_companion.ui.action_menu.combat_menu`). Built at
        every opening rather than once, because what is available moves: a roll comes
        back, an NPC group is formed. Each pick other than Reroll is reported on
        :attr:`actionRequested`.
        """
        self._menu_builder = build

    def own_actions(self) -> dict[str, str | None]:
        """The actions the panel runs itself, and whether each can run right now."""
        return {ACTION_REROLL: None if self._last_roll is not None else NOTHING_TO_REROLL}

    @property
    def can_reroll(self) -> bool:
        """Whether there is a roll that came back, for Reroll to throw again."""
        return self._last_roll is not None

    def _action_sections(self) -> list[GridSection]:
        own = self.own_actions()
        if self._menu_builder is not None:
            return list(self._menu_builder(own))
        reason = own[ACTION_REROLL]
        tile = GridItem(
            ACTION_REROLL,
            "Reroll",
            ACTION_REROLL,
            action_tooltip("Reroll", "Throw your last roll again.", reason),
            enabled=reason is None,
        )
        return [GridSection("Dice", (tile,))]

    def open_actions(self) -> ActionGridPopup | None:
        """Open the Actions popup under its button, and return it.

        A failure to build it is logged and costs the popup, never the panel: the
        sections come from game data a mod may have written.
        """
        if self._action_popup is not None:
            self._action_popup.close()
        try:
            popup = ActionGridPopup(self._action_sections(), self._actions_button)
        except Exception:  # noqa: BLE001 - a bad menu must not take the roller down
            log_exception("Building the Actions popup")
            return None
        popup.picked.connect(self._on_action_picked)
        popup.destroyed.connect(lambda _=None, p=popup: self._forget_popup(p))
        self._action_popup = popup
        popup.show_under(self._actions_button)
        return popup

    def _forget_popup(self, popup: ActionGridPopup) -> None:
        if self._action_popup is popup:
            self._action_popup = None

    def _on_action_picked(self, key: str) -> None:
        if key == ACTION_REROLL:
            self.reroll()
        else:
            self.actionRequested.emit(key)

    def _build_spec_chip(self) -> QWidget:
        """The strip naming what the sheet asked to roll — hidden until it does.

        It sits *above* the sliders because it is what they modify: the loaded stat
        supplies the base number and the sliders are the extras on top. The ``✕``
        puts the panel back to a plain manual roll.

        Drawn **loudly** (an accent fill inside an accent border), with a ring on the
        die to match (:meth:`_apply_die_style`): a chip left loaded arms every later
        click with a modifier and maybe a DC, so it must be the most obvious thing in
        the panel. The stylesheet is scoped to the object name, since a bare rule
        would reach the ``✕`` inside too.

        The caption **elides** rather than wraps: a wrapped caption asked for a second
        line through height-for-width, which nothing above the Roll box passes on, so
        it was drawn squashed. The whole name is in the tooltip and the readout.
        """

        chip = QFrame()
        chip.setObjectName("specChip")
        chip.setStyleSheet(
            "#specChip {"
            f" background: {theme.wash('accent.dice', 0.15)};"
            f" border: {int(theme.metric('border.width.emphasis'))}px solid"
            f" {theme.color('accent.dice')};"
            f" border-radius: {int(theme.metric('radius.chip'))}px;"
            "}"
        )
        chip.setVisible(False)
        chip.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QHBoxLayout(chip)
        margin = int(theme.metric("space.sm"))
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(int(theme.metric("space.xs")))

        self._spec_label = ElidingLabel()
        self._spec_label.setStyleSheet(tinted_style("accent.dice", bold=True))
        layout.addWidget(self._spec_label, stretch=1)

        clear = QPushButton("✕")
        clear.setFixedWidth(int(theme.metric("column.chip-button")))
        clear.setToolTip("Stop rolling this trait")
        clear.clicked.connect(lambda: self.load_spec(None))
        layout.addWidget(clear, alignment=Qt.AlignmentFlag.AlignVCenter)
        return chip

    def _make_modifier(self) -> tuple[QSlider, QWidget]:
        """The Bonus/Penalty slider, −20..+20 and centred, linked two-way to a signed
        spin box (``+3`` / ``-3``) that keeps its arrows."""
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(-MODIFIER_LIMIT, MODIFIER_LIMIT)
        slider.setValue(0)
        # A tick at the centre, so 0 is findable by eye on a groove that runs both ways.
        slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        slider.setTickInterval(MODIFIER_LIMIT)
        guard_wheel(slider)
        spin = make_signed_spin_box(MODIFIER_LIMIT, buttons=True)
        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        return slider, spin

    def _mark_extras(self) -> None:
        """Tint the Bonus/Penalty row while it holds anything: green above 0, red below.

        The slider is spent by the next roll (:meth:`_spend_extras`), so a value
        sitting in it is a pending charge on that roll and has to be as visible as
        a loaded trait. Only ``color`` and weight go on the spin box: a ``border``,
        ``padding`` or ``background`` there would lay its edit field over the arrows
        (rule 4 in the theme notes).
        """
        value = self._modifier_spin.value()
        style = ""
        if value:
            style = tinted_style("tint.better" if value > 0 else "tint.worse")
        self._modifier_label.setStyleSheet(style)
        self._modifier_spin.setStyleSheet(f"QSpinBox {{ {style} }}" if style else "")

    def _spend_extras(self) -> None:
        """Zero the slider, and drop the action lines the roll used, once the roll they
        were set for has produced a number.

        A bonus or penalty is a circumstance of *one* check — cover against this
        attack, Extra Effort's "+2 on a single check" — so a value left standing in
        the slider used to ride along silently on every later roll. An action line is
        the same: it rides on one roll. A line greyed out on the roll (no attack
        loaded) did not ride on it, so it stays. Called beside :meth:`_settle_spec`
        and for the same reason; not from :meth:`_abandon_roll`, whose roll the player
        will want to throw again as it was.
        """
        self._modifier_spin.setValue(0)
        rolled, self._rolled_lines = self._rolled_lines, ()
        for key, _value in rolled:
            self._lane.remove(key)
        if rolled:
            self.linesRolled.emit(rolled)

    # -- the action lane -----------------------------------------------------

    def set_attack_choices(self, choices: Callable[[], Sequence[AttackChoice]] | None) -> None:
        """Have *choices* list the character's attacks for the attack picker.

        Called each time the picker opens (powers switch on and off). The panel reads
        no game data, so the host supplies this, like :meth:`set_roll_choices`.
        """
        self._attack_choices = choices

    def attack_choices(self) -> list[AttackChoice]:
        """The character's attacks as the host lists them now (``[]`` without a host)."""
        if self._attack_choices is None:
            return []
        try:
            return list(self._attack_choices())
        except Exception:  # noqa: BLE001 - a bad build must cost the menu, not the roller
            log_exception("Listing the attacks for the attack picker")
            return []

    def menu_point(self) -> QPoint:
        """Where a chooser opened from the Actions popup appears: under its button."""
        button = self._actions_button
        return button.mapToGlobal(button.rect().bottomLeft())

    def choose_attack(self) -> RollSpec | None:
        """Offer the character's attacks on a plain menu and return the one picked
        (``None``: dismissed, or no attacks). Loads nothing itself."""
        choices = self.attack_choices()
        if not choices:
            return None
        index = choose_from_menu(self, [c.caption for c in choices], self.menu_point())
        return None if index is None else choices[index].spec

    def loaded_attack(self) -> RollSpec | None:
        """The loaded spec when it is an attack (it forces a save), else ``None``."""
        spec = self._spec
        return spec if spec is not None and targetable_attack(spec) else None

    def start_action_line(
        self,
        key: str,
        name: str,
        *,
        limit: LineLimit | None = None,
        fixed: str = "",
        needs_attack: bool = False,
        tooltip: str = "",
        tone: str = "tint.better",
    ) -> ActionLine | None:
        """Put an action on the lane for the next roll; ``None`` if it was called off.

        *limit* makes it configurable (a slider over ``0..limit(spec)[0]``, starting at
        the maximum); otherwise it shows *fixed*. A line that *needs_attack* with no
        attack loaded first opens the attack picker, and the attack picked is loaded
        onto the chip; dismissing the picker calls the action off.
        """
        if needs_attack and self.loaded_attack() is None:
            spec = self.choose_attack()
            if spec is None:
                return None
            self.load_spec(spec)
        line = ActionLine(
            key,
            name,
            limit=limit,
            fixed=fixed,
            needs_attack=needs_attack,
            tooltip=tooltip,
            tone=tone,
        )
        self._lane.add(line)
        line.bind(self.loaded_attack())
        return line

    def set_line_shift(self, shift: LineShift | None) -> None:
        """Have *shift* say what the action lines do to the spec they ride on —
        ``shift(spec, lines) -> spec``, ``lines`` being :meth:`active_lines`. The attack
        maneuvers move its modifier and its save's DC; the panel reads no game data, so
        the host supplies this."""
        self._line_shift = shift

    def with_lines(self, spec: RollSpec | None) -> RollSpec | None:
        """*spec* with the active lines applied — what a roll of it would be made of.

        A failure in the host's shift is logged and costs the lines' effect, never the
        roll.
        """
        lines = self.active_lines()
        if spec is None or not lines or self._line_shift is None:
            return spec
        try:
            return self._line_shift(spec, lines)
        except Exception:  # noqa: BLE001 - a bad action must not cost the die
            log_exception("Applying the action lines to a roll")
            return spec

    def action_lines(self) -> list[ActionLine]:
        """The lines on the lane, in order (greyed ones too)."""
        return self._lane.lines()

    def active_lines(self) -> list[tuple[str, int | None]]:
        """``(key, value)`` for every line that applies to the roll as things stand."""
        return [(line.key, line.value()) for line in self._lane.lines() if line.available]

    def _on_lane_changed(self) -> None:
        self.updateGeometry()
        self.contentChanged.emit()

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
        self._die_button.setObjectName("dieButton")
        self._die_button.setFlat(True)
        self._die_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._die_button.clicked.connect(self._start_roll)

        self._face = QLabel("?")
        self._face.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._face.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._face.setStyleSheet(f"color: {theme.color('dice.face')};")
        self._set_die_size("die.size", "size.dice-face")

        grid.addWidget(self._die_button, 0, 0)
        grid.addWidget(self._face, 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        column.addWidget(container, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._readout = ReadoutLabel(IDLE_PROMPT)
        self._readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._readout.setWordWrap(True)
        self._readout.setTextFormat(Qt.TextFormat.RichText)
        # Side margins because this is the widest thing in the panel and the only
        # one not inside a group box: the die above it is a fixed square with room
        # to spare, while a named roll ("Close Attack (Enhanced Strength)") is as
        # long as the power it came from and wraps at whatever width the panel has.
        # Without them it wraps flush against the block's own border, which reads as
        # text that has overflowed rather than text that has wrapped.
        pad = int(theme.metric("space.lg"))
        self._readout.setContentsMargins(pad, 0, pad, 0)
        column.addWidget(self._readout)
        return part

    def _set_die_size(self, size_token: str, face_token: str) -> None:
        """Draw the die at the size *size_token* names, with *face_token*'s numeral.

        Two tokens rather than one because the pair only reads right together: a
        40pt face on compact mode's 96px die overflows it. The face size goes on
        the label's :class:`QFont` and never into a stylesheet, which would outrank
        the widget font.

        The artwork is re-rasterised rather than scaled, so the small die is as
        crisp as the large one.
        """
        size = int(theme.metric(size_token))
        padding = int(theme.metric("die.padding"))
        self._die_button.setIcon(QIcon(d20_pixmap(self.devicePixelRatioF(), size)))
        self._die_button.setIconSize(QSize(size, size))
        self._die_button.setFixedSize(size + padding, size + padding)
        # The ring goes back on afterwards: a stylesheet set before the resize is
        # not lost, but this method is the one place the die is (re)dressed, and
        # compact mode calls it while a trait may well be loaded.
        self._apply_die_style()

        font = QFont()
        font.setPointSizeF(theme.font_size(face_token))
        font.setBold(True)
        self._face.setFont(font)

    def _apply_die_style(self) -> None:
        """Ring the die while a trait is loaded, and leave it bare when none is.

        The other half of the loud chip: the chip says what is loaded, and the thing
        you are about to click says it is loaded with *something*. The ring is drawn
        inside the button's existing ``die.padding`` chrome, so the button's fixed
        size never moves — a size change here would ripple out through
        :attr:`contentChanged` into the splitter for what is only a highlight.
        """
        if self._spec is None:
            # An *empty* sheet rather than "no border": a rule of our own would take
            # the flat button's hover painting away from the style with it.
            self._die_button.setStyleSheet("")
            return
        border = (
            f"border: {int(theme.metric('border.width.emphasis'))}px solid"
            f" {theme.color('accent.dice')};"
            f" border-radius: {int(theme.metric('radius.card'))}px;"
        )
        self._die_button.setStyleSheet(
            f"#dieButton {{ {border} background: {theme.wash('accent.dice', 0.10)}; }}"
            # Hover has to be restated for the same reason: a background of ours
            # outranks the one the style would have painted under the cursor.
            f"#dieButton:hover {{ {border} background: {theme.wash('accent.dice', 0.20)}; }}"
        )

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
            self._modifier_slider,
            self._modifier_spin,
            self._lane,
            self._dc_check,
            self._dc_spin,
            self._hidden_check,
            self._routine_check,
            self._request_combo,
            self._request_dc,
            self._request_button,
            self._quick_container,
            self._actions_button,
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

    def load_spec(
        self, spec: RollSpec | None, *, minion: bool = False, localize: bool = True
    ) -> None:
        """Load *spec* as the thing this panel rolls, or ``None`` to go back to manual.

        *minion* says the trait is a minion's, so a natural 20 on it is no added
        success (PDF p25). Only the GM's window rolls as anybody else, so only it
        passes one; it goes with the spec and is forgotten with it.

        Sticky on purpose: a *loaded* chip stays after the roll so the sliders can be
        adjusted and the die thrown again for the same trait. That is what a single
        click on a stat line asks for, and it is why this is not the method a roll
        goes through — see :meth:`roll_spec`, which drops what it loaded again.

        A spec that names its own DC (a power's save) ticks the DC box and fills it
        in, so the number it will roll against is visible rather than implied; a spec
        without one leaves the box exactly as the player left it.

        Clearing the chip takes that DC off again (it used to stay, grading later
        manual rolls against a trait no longer loaded). Only a DC this method set is
        withdrawn; one the player typed is theirs.
        """
        # *localize* false puts back a spec this panel already held, which has been
        # through the localizer once (twice would add this sheet's number again).
        if spec is not None and localize and self._localizer is not None:
            spec = self._localizer(spec)
        # An explicit load is always a deliberate one, whatever a roll in flight had
        # in mind: it cancels the transience :meth:`roll_spec` asked for, so a spec
        # arriving over the bus mid-tumble is not swept away when the die settles.
        self._transient = False
        had_own_dc = self._spec is not None and self._spec.dc is not None
        self._spec = spec
        self._spec_minion = spec is not None and minion
        if spec is not None and spec.dc is not None:
            self._dc_check.setChecked(True)
            self._dc_spin.setValue(spec.dc)
        elif had_own_dc:
            self._dc_check.setChecked(False)
            self._dc_spin.setValue(0)
        self._spec_chip.setVisible(spec is not None)
        if spec is not None:
            caption = spec_caption(spec)
            self._spec_label.setText(caption)
            # The label covers most of the chip, so it carries the hint too — beneath
            # the whole caption, which an elided label would otherwise offer alone.
            self._spec_label.set_hover_text(f"{caption}\n\n{spec.hint}" if spec.hint else "")
            self._spec_chip.setToolTip(spec.hint)
        self._apply_die_style()
        # The lines that need an attack follow what is loaded: grey without one, their
        # limits worked out again (and reset to the maximum) for a different one.
        self._lane.bind(self.loaded_attack())
        self._prompt_readout(replace_result=spec is not None)
        self.updateGeometry()
        # Showing the chip makes this panel taller — the bug this used to cause is
        # written up on :attr:`contentChanged`.
        self.contentChanged.emit()

    def _prompt_readout(self, *, replace_result: bool) -> None:
        """Say what the die will throw, under it, where the result appears.

        *replace_result* is what keeps this from eating a number: loading a trait
        replaces a result the player has already read (they have just said what they
        want to roll next, and the card is in the history either way), but *clearing*
        one must not — and clearing is exactly what :meth:`_settle_spec` does one
        line after the roll wrote its result there.
        """
        if self._showing_result and not replace_result:
            return
        self._showing_result = False
        if self._spec is None:
            self._readout.setText(IDLE_PROMPT)
            return
        self._readout.setText(f"Rolling {escape_rich_text(self._spec.label)} — click the die.")

    def add_bonus(self, amount: int) -> None:
        """Add *amount* to the Bonus/Penalty slider, clamped to the range it offers.

        For Extra Effort's "+2 on a single check", which is the one benefit that lands on
        the next roll rather than on the build. Added to whatever is set rather than
        replacing it: the player may already have dialled in a circumstance bonus, and
        taking that away would be charging them a rung of fatigue to lose two points.
        The next roll spends it (:meth:`_spend_extras`), which is what "a single check"
        asks for.
        """

        spin = self._modifier_spin
        spin.setValue(max(spin.minimum(), min(spin.maximum(), spin.value() + amount)))

    def roll_spec(
        self,
        spec: RollSpec | None,
        *,
        minion: bool = False,
        before: Callable[[RollSpec | None], bool] | None = None,
    ) -> bool:
        """Load *spec*, roll it at once, and let it go again. ``False`` if a die is
        already tumbling. *minion* is :meth:`load_spec`'s.

        *before* is handed the spec the roll will be made of (the action lines applied)
        just before the die goes, and ``False`` from it calls the roll off. It is how an
        aimed attack declares to the GM exactly what is about to be rolled.

        The one public way in for the sheet: a double-clicked stat line, a power's
        🎲, or a follow-up chip on a history card all land here.

        What it loads is **transient**, gone once the die settles
        (:meth:`_settle_spec`). It is loaded at all because the roll is made out of it
        (:meth:`_roll_parameters`, :meth:`_roll_label`, :attr:`localRoll`). It must
        not *stay*: a double-click fires the single click first, so the roller was
        left armed with a trait the player had only asked to throw once.
        """
        if self._rolling:
            return False
        held = (self._spec, self._spec_minion, self._transient)
        self.load_spec(spec, minion=minion)
        self._transient = spec is not None
        if not self._start_roll(before):
            # Called off before the die went: the chip goes back to what it held.
            self.load_spec(held[0], minion=held[1], localize=False)
            self._transient = held[2]
            return False
        return True

    @property
    def rolling(self) -> bool:
        """Whether a die is tumbling now — :meth:`roll_spec` would refuse another."""
        return self._rolling

    def _routine(self) -> bool:
        """Whether the roll in flight is a routine check — the last roll's, on a reroll."""
        if self._rerolling and self._last_roll is not None:
            return bool(self._last_roll.get("routine"))
        return self._routine_check.isChecked()

    def _as_minion(self) -> bool:
        """Whether the roll in flight is a minion's — the last roll's, on a reroll."""
        if self._rerolling and self._last_roll is not None:
            return bool(self._last_roll.get("minion"))
        return self._spec_minion

    def _spend_routine(self) -> None:
        """A routine check is one roll's worth: untick it once that roll is made."""
        self._routine_check.setChecked(False)

    def _settle_spec(self) -> None:
        """Drop a spec that was only loaded to be rolled, now that it has been.

        Called at the very end of both paths that produce a number, after the readout
        and the history have had their look at :attr:`_spec`. Not from
        :meth:`_abandon_roll`: a roll the session never answered is one the player
        will want to throw again, and taking the trait away would send them back to
        the sheet to click it a second time.
        """
        if not self._transient:
            return
        self._transient = False
        self.load_spec(None)

    def adjustment(self) -> int:
        """The slider's situational extra as it stands (a penalty is below 0).

        What another roll made on this panel's behalf takes over — an aimed attack's
        single save, which the GM's app rolls through the server rather than here, is
        made at the GM's current bonus and penalty.
        """
        return self._modifier_spin.value()

    def _roll_parameters(self) -> tuple[int, int, int | None, bool]:
        """What the inputs currently ask for: bonus, penalty, DC, hidden.

        The loaded spec's modifier is folded in **on top of** the sliders rather than
        written into them — the sliders are the situational extras ("+2 for cover"),
        and a trait bonus can in any case exceed their 0-20 range. The net is split
        back into a non-negative bonus and penalty, which is the shape the wire and
        the history cards speak.
        """
        if self._rerolling and self._last_roll is not None:
            last = self._last_roll
            return (last["bonus"], last["penalty"], last["dc"], last["hidden"])
        value = self._modifier_spin.value()
        bonus, penalty = max(value, 0), max(-value, 0)
        dc = self._dc_spin.value() if self._dc_check.isChecked() else None
        spec = self._made_of()
        if spec is not None:
            net = spec.modifier + bonus - penalty
            bonus, penalty = max(net, 0), max(-net, 0)
            if spec.dc is not None:
                dc = spec.dc
        return (bonus, penalty, dc, self._hidden_option and self._hidden_check.isChecked())

    def _roll_label(self) -> str:
        """The name this roll travels under — the loaded spec's, or nothing.

        A re-roll travels under its original's name; the ``reroll`` flag beside it
        is what marks it (:func:`~mm_companion.ui.roll_history.reroll_label`).
        """
        if self._rerolling and self._last_roll is not None:
            return self._last_roll["label"]
        spec = self._made_of()
        return spec.label if spec is not None else ""

    def _made_of(self) -> RollSpec | None:
        """The spec a roll is made of now: the one in flight (the lines applied) while
        there is one, else the loaded one."""
        return self._flight if self._flight is not None else self._spec

    def _rolled_spec(self) -> RollSpec | None:
        """The trait the roll in flight is of: the last roll's on a re-roll.

        Read from here rather than loaded onto the chip, so a re-roll neither runs
        the localizer a second time over a spec it already filled in nor disturbs a
        trait the player has loaded since.
        """
        if self._rerolling and self._last_roll is not None:
            return self._last_roll["spec"]
        return self._made_of()

    def reroll(self) -> bool:
        """Throw the last roll again — same bonus, penalty, DC and trait — luckily.

        The die is a re-roll's (:func:`~mm_companion.core.dice.reroll_d20`): one that
        comes up 1-10 counts 10 more, so it always lands on 11-20 — the Luck
        advantage, and the Hero Point spent on one. In a session the *server* rolls it
        (``reroll`` on the request), for the reason it rolls everything. What it
        throws again is the parameters the last roll was *made* with, not what the
        inputs say now, so nudging a slider after a roll does not change its re-roll.
        ``False`` with nothing to re-roll or a die still tumbling.
        """
        if self._rolling or self._last_roll is None:
            return False
        if self._last_roll.get("routine"):
            # Nothing was rolled, so there is no die to be lucky with.
            return False
        self._rerolling = True
        self._start_roll()
        return True

    def _start_roll(self, before: Callable[[RollSpec | None], bool] | None = None) -> bool:
        """Begin a roll: lock the inputs, tumble the die, then reveal the result.

        In a session the request goes out *now* and the tumble runs until the
        server's answer arrives; on its own the die is thrown when the tumble
        ends. Either way the inputs stay locked until there is a number.

        The action lines are applied here, once (:meth:`with_lines`): the roll is made
        of that spec, and Reroll throws it again as it was. *before* is
        :meth:`roll_spec`'s. ``False`` when it called the roll off, or a die tumbles.
        """
        if self._rolling:
            return False
        if not self._rerolling:
            lines = tuple(self.active_lines())
            flight = self.with_lines(self._spec)
            if before is not None and not before(flight):
                return False
            self._flight = flight if flight is not self._spec else None
            bonus, penalty, dc, hidden = self._roll_parameters()
            self._pending_last = {
                "bonus": bonus,
                "penalty": penalty,
                "dc": dc,
                "hidden": hidden,
                "label": self._roll_label(),
                "spec": self._made_of(),
                "routine": self._routine_check.isChecked(),
                "minion": self._spec_minion,
                "lines": lines,
            }
            self._rolled_lines = lines
        else:
            self._rolled_lines = ()
        self._rolling = True
        self._die_button.setEnabled(False)
        for widget in self._input_widgets():
            widget.setEnabled(False)
        self._roll_clock.restart()
        self._request_session_roll()
        self._tick_roll()
        return True

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
        # Before the request, for the same reason the listener above is: a hosted
        # session resolves in-process and the answer comes back *during* the call,
        # so a history told afterwards would already have shown the card.
        self.awaitingOwnRoll.emit(True)
        sent = bridge.request_roll(
            label=self._roll_label(),
            bonus=bonus,
            penalty=penalty,
            dc=dc,
            hidden=hidden,
            # What is being rolled travels with it, so the *other* seats can act on
            # it: the target's player sees the attack land and clicks the save off
            # its card. The server echoes it back on the record.
            spec=None if self._rolled_spec() is None else self._rolled_spec().to_dict(),
            reroll=self._rerolling,
            routine=self._routine(),
            minion=self._as_minion(),
        )
        if not sent:
            self._abandon_roll(NOT_SENT)

    def _on_session_roll(self, roll: object) -> None:
        """A roll came back from the session — ours, or somebody else's.

        Only our own answers this roll. The die keeps tumbling for the rest of
        its animation even once the number is known, so a fast answer does not
        cut the roll short.

        The history feed carries notes and requests as well as rolls, and one of
        ours can land mid-tumble (spending a hero point on the attack being rolled
        is exactly when it would). Neither has a die, so taking one for the answer
        would settle the d20 on zero — hence the check is "is this a die roll",
        not just the seat and not merely "is this not a note".
        """
        if not self._awaiting or not isinstance(roll, dict):
            return
        if roll.get("kind", KIND_ROLL) != KIND_ROLL:
            return
        # A group roll the GM made carries this seat's name on its resistance line
        # but answers nothing this panel threw (see RollRecord.group).
        if roll.get("group"):
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

        routine, minion = self._routine(), self._as_minion()
        self._commit_last_roll()
        bonus, penalty, dc, _hidden = self._roll_parameters()
        if routine:
            die, natural = ROUTINE_DIE, False
        else:
            die, natural = reroll_d20() if self._rerolling else (roll_d20(), True)
        self._face.setText(str(die))

        modifier = bonus - penalty
        result = (
            resolve_check(modifier, dc, roll=die, natural=natural, added_success=not minion)
            if dc is not None
            else None
        )
        self._update_readout(
            die,
            modifier,
            dc,
            None if result is None else result.degree,
            bool(result is not None and result.critical),
            routine=routine,
        )
        self.localRoll.emit(
            {
                "die": die,
                "bonus": bonus,
                "penalty": penalty,
                "dc": dc,
                "result": result,
                "label": self._roll_label(),
                "spec": self._rolled_spec(),
                "reroll": self._rerolling,
                "routine": routine,
                "minion": minion,
            }
        )
        self._unlock_inputs()
        # Last, so everything above still sees what was rolled.
        self._rerolling = False
        self._flight = None
        self._settle_spec()
        self._spend_extras()
        self._spend_routine()

    def _commit_last_roll(self) -> None:
        """The roll in flight has a result, so it is now the one Reroll throws."""
        if self._pending_last is not None:
            self._last_roll, self._pending_last = self._pending_last, None

    def _reveal_session_roll(self, roll: dict) -> None:
        """Show the number the session rolled for us and let go of the inputs."""
        self._commit_last_roll()
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
            routine=bool(roll.get("routine")),
        )
        self._unlock_inputs()
        # The die has settled — a paired history panel can now show this own roll.
        # The spec is already on the record (the server recorded what we sent and
        # broadcast it), so this is the same dict every other seat is reading.
        self.sessionRollRevealed.emit(roll)
        # And the window closes *after* the reveal, so the card this roll produced
        # is placed by the cue above rather than by the flush the close performs.
        self.awaitingOwnRoll.emit(False)
        self._rerolling = False
        self._flight = None
        self._settle_spec()
        self._spend_extras()
        self._spend_routine()

    def _abandon_roll(self, message: str) -> None:
        """Give up on a session roll that never came back.

        Giving up is about the *die*, not about the roll: the server may well have
        rolled it and simply been slow to say so, in which case the record turns up
        on the history's own feed moments later. So the readout keeps saying nobody
        answered — that was true of the tumble — while
        :attr:`awaitingOwnRoll` closes the window and lets the card land whenever
        it arrives. A history that went on deferring here would swallow the roll
        for good, since nothing would ever reveal it.
        """
        if not self._rolling and not self._awaiting:
            return
        self._rolling = False
        self._awaiting = False
        self._pending = None
        self._pending_last = None
        self._rerolling = False
        self._rolled_lines = ()
        self._flight = None
        self._disconnect_session()
        self.awaitingOwnRoll.emit(False)
        self._face.setText("?")
        self._readout.setText(f"<span style='color:{theme.color('tint.worse')}'>{message}</span>")
        self._showing_result = True  # not a number, but not the prompt either
        # The chip stays (see :meth:`_settle_spec`); only its transience goes, so the
        # trait is still loaded and the die can simply be clicked again.
        self._transient = False
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
        # And the Ask button follows whether a trait is chosen, for the same reason.
        self._sync_request_button()

    def _unjudged_attack(self, dc: int | None) -> bool:
        """Whether the roll just made is an attack nothing will judge: no DC to beat,
        and not aimed at anyone (an aimed attack is judged by the GM's app)."""
        spec = self._rolled_spec()
        return dc is None and spec is not None and targetable_attack(spec) and not spec.targets

    def _update_readout(
        self,
        die: int,
        modifier: int,
        dc: int | None,
        degree: int | None,
        critical: bool = False,
        *,
        hidden: bool = False,
        routine: bool = False,
    ) -> None:
        self._showing_result = True
        label = reroll_label(self._roll_label(), self._rerolling)
        html = readout_html(
            label,
            die,
            modifier,
            dc,
            degree,
            critical,
            hidden=hidden,
            routine=routine,
            warning=NO_DC_WARNING if self._unjudged_attack(dc) else "",
        )
        self._readout.setText(html)
