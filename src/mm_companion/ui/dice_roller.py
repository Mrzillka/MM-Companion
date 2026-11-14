"""The dice roller for Mutants & Masterminds checks.

Set a modifier (a signed box with the common circumstance steps either side of it)
and, optionally, a Difficulty Class, then click the D20 to play a short roll
animation and read the result beside it — or pick **Take 10** (Actions) for a routine check.
When a DC is set the result also shows the degree of success/failure. Past rolls
stack up in a history panel (each card can be removed or saved); a saved roll can be
named, dragged to reorder, and lives in a persistent row of "quick roll" pills for
one-click reuse.

Three widgets, smallest first:

* :class:`DiceRollerPanel` (here) — the roll controls alone (settings, die + readout,
  quick rolls). Its shape, its Ask to Roll form and its quick rolls are mixins in
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
from dataclasses import replace

from PySide6.QtCore import QElapsedTimer, QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QPalette,
    QPixmap,
    QShortcut,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.diagnostics import log_exception
from mm_companion.core.dice import ROUTINE_DIE, CheckResult, reroll_d20, resolve_check, roll_d20
from mm_companion.core.rules import (
    OWN_ATTACK_HINT,
    AttackChoice,
    RollSpec,
    targetable_attack,
)
from mm_companion.core.session.model import KIND_ROLL
from mm_companion.ui import theme
from mm_companion.ui.action_grid import ActionGridPopup, GridSection
from mm_companion.ui.action_menu import (
    ACTION_REQUEST,
    ACTION_REROLL,
    ACTION_TAKE_TEN,
    NOTHING_TO_REROLL,
    choose_from_menu,
    own_items,
)
from mm_companion.ui.flow_layout import FlowLayout
from mm_companion.ui.reflow import ReflowBox
from mm_companion.ui.roll_history import (
    HIDDEN_MARK,
    NATURAL_ONE,
    NATURAL_TOKENS,
    NATURAL_TWENTY,
    degree_html,
    degree_label,
    escape_rich_text,
    fit_button,
    label_names_dc,
    natural_kind,
    reroll_label,
    roll_breakdown,
    roll_working,
    staged_breakdown,
    staged_working,
)
from mm_companion.ui.roller.action_lane import (
    ActionLane,
    ActionLine,
    LineDescribe,
    LineFit,
    LineLimit,
    LineShift,
)
from mm_companion.ui.roller.controls import RollControls
from mm_companion.ui.roller.fields import FieldRow
from mm_companion.ui.roller.flourish import DieBurst, pulse_halo, shake
from mm_companion.ui.roller.quick_rolls import QuickRollStrip, RollerQuickRollsMixin
from mm_companion.ui.roller.requests import RollerRequestsMixin
from mm_companion.ui.roller.shape import RollerShapeMixin
from mm_companion.ui.session_bridge import SessionBridge, live_session
from mm_companion.ui.svg_assets import die_resource, svg_pixmap
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import (
    ElidingLabel,
    discard_widget,
    help_markup,
    make_signed_spin_box,
    make_spin_box,
    select_all_on_focus,
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

#: A roll made twice for the higher (Aim) is thrown **one die after the other** (the
#: user's call, 2026-11-13): the die lands on the first, rests there for
#: :data:`AIM_PAUSE_MS` while the readout says so, then tumbles for
#: :data:`AIM_SECOND_MS` and lands on the second. Both were thrown at once before, and
#: the result named the dropped one only as a struck-through number.
AIM_PAUSE_MS = 700
AIM_SECOND_MS = 900
#: What the readout says while the die rests on the first of two.
AIM_FIRST_TEXT = "First die: <b>{die}</b> — rolling the second…"

NO_ANSWER = "The session did not answer, so nothing was rolled."
NOT_SENT = "The roll could not be sent to the session."

# What the chip and the readout say while a trait is loaded. Both are part of the
# same guard: a loaded chip arms the die, so the panel has to be obvious about it.
CHIP_PREFIX = "🎲 "
IDLE_PROMPT = "Click the die to roll."
#: Written in the chip's empty slot.
CHIP_SLOT_HINT = "Click a trait on a sheet to roll it"

#: How far the one modifier box runs either way from 0.
MODIFIER_LIMIT = 30
MODIFIER_LABEL = "Modifier"
MODIFIER_HINT = (
    "A bonus or penalty on this one roll: cover, a good tool, a distraction. The next "
    "roll uses it up."
)
#: The slider the modifier row can have instead of its step buttons (Settings ▸ General):
#: its range. Lopsided on purpose (the user's call, 2026-11-10): bonuses stack higher
#: than penalties at a table. The box beside it still takes the full ±MODIFIER_LIMIT.
MODIFIER_SLIDER_MIN = -10
MODIFIER_SLIDER_MAX = 20
#: Where its ticks fall: every major circumstance step.
MODIFIER_SLIDER_TICKS = 5
MODIFIER_SLIDER_HINT = (
    f"Slide for a bonus or penalty on this one roll, {MODIFIER_SLIDER_MIN:+d} to "
    f"{MODIFIER_SLIDER_MAX:+d}; type in the box for more. The next roll uses it up."
)

#: The Bonus and Penalty rows the modifier can be instead (Settings ▸ General, the
#: user's call, 2026-11-13): how far each box and slider runs, from 0.
BONUS_LABEL = "Bonus"
PENALTY_LABEL = "Penalty"
SPLIT_LIMIT = MODIFIER_LIMIT
SPLIT_SLIDER_MAX = 20
BONUS_HINT = "A bonus on this one roll: a good tool, help, an advantage. The next roll uses it up."
PENALTY_HINT = (
    "A penalty on this one roll: cover, a distraction, poor footing. The next roll uses it up."
)

#: The DC's slider (Settings ▸ General ▸ Modifier, slider): how far it reaches. The box
#: beside it takes the full :data:`DC_LIMIT`.
DC_SLIDER_MAX = 40

#: The Actions button's caption: the arrow says it opens something.
ACTIONS_LABEL = "Actions ▾"

#: Beside an attack rolled with no DC and at no one: nothing judged the hit. As short
#: as it can be (the user's call, 2026-11-10: the whole sentence under the result was
#: too much); :data:`NO_DC_WARNING` is its tooltip.
NO_DC_SHORT = "⚠ No DC"
NO_DC_WARNING = (
    "No DC and no target: the hit was not judged. Put the target's Defense in the "
    "DC box, or aim the attack at a creature."
)
#: The same spot under a save rolled off one's own attack card, with
#: :data:`~mm_companion.core.rules.OWN_ATTACK_HINT` on hover.
OWN_ATTACK_SHORT = "⚠ Own attack"

#: The roller's Difficulty Class box: with a number in it, the roll is judged against it.
DC_LABEL = "DC"
#: What the DC box says while it holds none, muted like a placeholder. It was "not set"
#: for a while, which widened the box past a two-digit number; the user asked for the
#: dash back (2026-11-13). Emptying the box puts it back too (:class:`DcSpinBox`).
DC_NONE = "—"
#: The highest DC the box takes.
DC_LIMIT = 60
#: Where a bonus step takes a DC that is not set, until the host says otherwise
#: (``system.dcStart``).
DC_START = 10
DC_HINT = "Difficulty Class: the roll is judged against it. 0 or empty for none."

#: The roller's keyboard shortcuts, live anywhere in the window that shows the roller.
ROLL_SHORTCUT = "Ctrl+R"
REROLL_SHORTCUT = "Ctrl+Shift+R"
TAKE_TEN_SHORTCUT = "Ctrl+T"

#: How long the die glows with how the roll went before it settles back.
OUTCOME_GLOW_MS = 3000

#: The button under a result that throws it again.
REROLL_LABEL = "↻ Reroll"
REROLL_HINT = (
    "**Reroll:** throw this roll again — same trait, modifier and DC — as a lucky "
    "re-roll: a 1–10 counts 10 more (a Hero Point, or the Luck advantage)."
)


#: Qt's "no maximum" for a widget's size.
QWIDGETSIZE_MAX = 16777215


def _capsule_inset() -> int:
    """How far the Modifier and DC labels sit in from their column's edge."""
    return 2 * int(theme.metric("space.xs"))


def _label_gap() -> int:
    """The air after the Modifier and DC labels, on top of the grid's own spacing: the
    grid is spaced as tightly as its steps are, and a label butted up to its first step
    read as one. Small, because the right-hand strip has no pixel to spare."""
    return int(theme.metric("space.xs"))


def _caption_width(button: QPushButton) -> int:
    """How wide *button* has to be to show its caption: the caption and the chrome the
    style puts round it, without the platform's minimum push-button width (80px under
    the common style, whatever the caption). The chrome is measured off a caption long
    enough to be past that minimum, so a theme's padding is counted too."""
    long = button.text() * 6
    probe = QPushButton(long)
    probe.setFont(button.font())
    try:
        metrics = probe.fontMetrics()
        chrome = probe.sizeHint().width() - metrics.horizontalAdvance(long)
        return metrics.horizontalAdvance(button.text()) + max(0, chrome)
    finally:
        probe.deleteLater()


def _stretch_across(widget: QWidget) -> None:
    """Let *widget* take a share of its row's width: a justified row, not a ragged one."""
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, widget.sizePolicy().verticalPolicy())


def outcome_token(
    die: int, degree: int | None, critical: bool, *, routine: bool = False, lifted: bool = False
) -> str | None:
    """The colour token the die glows in once a roll lands, or ``None`` for no glow.

    A natural 20 glows ``dice.critical`` and a natural 1 ``dice.fumble``, with a DC or
    without (:func:`~mm_companion.ui.roll_history.natural_kind`); otherwise a graded
    roll glows by how it went, and an ungraded one not at all. *routine* (take 10) and
    *lifted* (a lucky re-roll's die, never a natural one) have no natural face to
    celebrate.
    """
    natural = natural_kind(die, degree, critical, routine=routine, lifted=lifted)
    if natural is not None:
        return NATURAL_TOKENS[natural]
    if degree is not None:
        return "tint.better" if degree > 0 else "tint.worse"
    return None


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

    It used to lead with "Rolling:", which made "Rolling: Blast: Attack +7" — two
    colons — and said what the halo on the die already says: that it is armed. The
    chip's loud accent frame and the halo carry the state; the caption names the roll.
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
    other_die: int = 0,
    natural: str | None = None,
) -> str:
    """The rich text under the die: the roll's name, the total and its working, the grade
    (and a *warning* line). A *natural* 20 or 1 opens with a banner in its colour."""

    total = die + modifier
    muted = theme.color("text.muted.rich")
    html = ""
    if natural is not None:
        banner = "★ NATURAL 20 ★" if natural == NATURAL_TWENTY else "✖ NATURAL 1"
        html += (
            f"<span style='color:{theme.color(NATURAL_TOKENS[natural])};font-weight:bold;"
            f"white-space:nowrap'>{banner}</span><br>"
        )
    if label:
        html += (
            f"<span style='color:{theme.color('accent.dice')}'>"
            f"{escape_rich_text(label)}</span><br>"
        )
    # Graded, the total itself wears how it went: the number is what the eye finds
    # first, and the degree line under it is the detail.
    tint = "" if degree is None else theme.color("tint.better" if degree > 0 else "tint.worse")
    colour = f"color:{tint};" if tint else ""
    big = (
        f"<span style='font-size:{theme.font_size('size.roll-readout')}pt;{colour}'>"
        f"<b>{total}</b></span>"
    )
    # The die, the net bonus or penalty, the total — the history's line; what the bonus
    # is made of is on hover (see :meth:`DiceRollerPanel._update_readout`).
    html += roll_working(die, modifier, routine=routine, other_die=other_die, total_html=big)
    # Not when the name already says it: "Toughness vs. 18" over "10 vs DC 18" was the
    # DC twice in two lines.
    if dc is not None and not label_names_dc(label, dc):
        html += f" vs DC {dc}"
    if degree is not None:
        html += f"<br><span style='color:{tint}'>{degree_html(degree, critical, die)}</span>"
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

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """As tall as the text is wrapped **at the width it has**.

        The layouts above the readout (the panel's reflow, the splitter) size it off
        its hints, never its height-for-width, and the hint's height is the template's
        width's. Squeezed narrower than that — the roller floated thinner than its
        recommended size — a long attack name wrapped to more lines than that height
        held, and the result line under it was cut in half (the user's report,
        2026-11-13). A word-wrapped label's height following its width is height, which
        scrolls; the width it reports stays a word's.
        """
        hint = super().minimumSizeHint()
        width = self.width()
        if width <= 0 or not self.wordWrap():
            return hint
        return QSize(hint.width(), max(hint.height(), self.heightForWidth(width)))

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        # A new width wraps the text anew: ask the layouts for the height that needs.
        # Narrower is only ever taller, so a scroll bar this brings on keeps itself on.
        if event.oldSize().width() != event.size().width():
            self.updateGeometry()


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

    #: The Ask to Roll form's button was pressed — ask the table to roll this
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
        # The tumble's next flicker. One timer rather than a chain of single shots, so
        # Aim's pause can stop it and its second tumble restart it without two chains.
        self._tumble_timer = QTimer(self)
        self._tumble_timer.setSingleShot(True)
        self._tumble_timer.timeout.connect(self._tick_roll)
        # Aim's two throws (see AIM_PAUSE_MS): 0 for a roll of one die, 1 while the first
        # tumbles, 2 while the die rests on it, 3 while the second tumbles. And the first
        # die, as ``(face, natural)``, once a roll made here has thrown it.
        self._aim_stage = 0
        self._aim_first: tuple[int, bool] | None = None
        self._aim_pause = QTimer(self)
        self._aim_pause.setSingleShot(True)
        self._aim_pause.timeout.connect(self._throw_second_die)
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
        # The traits the Ask to Roll form offers, pushed in by the host (the panel reads
        # no game data) — see :meth:`set_roll_choices`. Empty until it is, which is
        # why the Ask tile starts greyed.
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
        # What the roll in flight loads once it settles (its spec's ``then``: a
        # slammer's own save), or ``None``. Never set for a reroll.
        self._then: RollSpec | None = None
        # Set by Take 10 for the one roll it starts (see :meth:`take_ten`).
        self._routine_next = False
        # Set while a roll that has just landed clears its inputs (:meth:`_wind_down`):
        # those changes are the roll's, not the player's, and must not trade the result
        # beside the die for the preview (:meth:`_inputs_edited`).
        self._winding_down = False
        # The natural 20 or 1 the readout is showing, if any (no Reroll under a 20).
        self._shown_natural: str | None = None
        # The circumstance steps either side of the modifier box, pushed in by the
        # host (see :meth:`set_modifier_steps`); none until it does.
        self._step_buttons: list[QToolButton] = []
        # The same steps either side of the DC box, and where a bonus step takes a DC
        # that is not set (see :meth:`set_dc_start`).
        self._dc_step_buttons: list[QToolButton] = []
        # And either side of the Bonus and Penalty boxes (see :meth:`set_modifier_split`).
        self._split_step_buttons: list[QToolButton] = []
        self._dc_start = DC_START
        # The colour token the die glows in after a roll lands (see
        # :meth:`_glow_outcome`), and the timer that lets it settle back.
        self._outcome: str | None = None
        self._outcome_timer = QTimer(self)
        self._outcome_timer.setSingleShot(True)
        self._outcome_timer.timeout.connect(self._clear_outcome)
        # The burst over the die on a natural 20 or 1, made the first time one lands.
        self._burst: DieBurst | None = None
        # How the rolls this panel made with action lines were made, for a star saving
        # one to re-arm them (see :meth:`remember_recipe`), and the host's way of putting
        # a line back (:meth:`set_line_rearm`).
        self._recipes: dict = {}
        self._line_rearm = None
        # Step buttons or a slider beside the modifier box (Settings ▸ General).
        self._modifier_input = storage.modifier_input()
        # One Modifier row, or Bonus and Penalty rows (Settings ▸ General).
        self._modifier_split = storage.modifier_split()
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
        self._request_form = self._build_request_form()
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
        self._build_shortcuts()

    def _build_shortcuts(self) -> None:
        """Roll, reroll and take 10 from the keyboard, anywhere in the roller's window.

        Window-wide rather than needing the roller focused: mid-fight the pointer is
        on the sheet, and the point is not having to reach for the die. A shortcut
        only fires while the roller is on screen, so a closed Dice block takes its
        keys with it.
        """
        for key, slot in (
            (ROLL_SHORTCUT, self._shortcut_roll),
            (REROLL_SHORTCUT, self.reroll),
            (TAKE_TEN_SHORTCUT, self.take_ten),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda slot=slot: slot())

    def _shortcut_roll(self) -> bool:
        """The roll shortcut: what a click of the die does, while the die can be clicked."""
        if not self._die_button.isEnabled():
            return False
        return self._start_roll()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        self.sync_reflow()

    # -- construction --------------------------------------------------------

    def _build_roll_settings(self) -> QWidget:
        """The controls above the die: what is loaded, the two rows of controls, the lane.

        No group box round them: the block's own frame already says where the roller
        is, and a "Roll" frame, a "Quick rolls" one and a "History" one inside it were
        three boxes nested in a fourth.

        From the top: the chip slot (what the sheet loaded), then
        :class:`~mm_companion.ui.roller.controls.RollControls` — the modifier with its
        steps and **Actions**, the DC with its steps and **Take 10** — and the action
        lane *under both rows*, so neither button nor the DC moves when a pick puts a
        line on the lane (the user's layout, 2026-11-11). Each row's controls share out
        the whole width rather than huddling at the left edge.
        """
        part = QWidget()
        grid = QGridLayout(part)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(int(theme.metric("space.md")))
        grid.setVerticalSpacing(int(theme.metric("space.sm")))
        # Kept, because in a row the quick rolls move in under these controls
        # (:meth:`~mm_companion.ui.roller.shape.RollerShapeMixin.apply_reflow`).
        self._settings_grid = grid
        grid.setColumnStretch(1, 1)

        self._spec_chip = self._build_spec_chip()
        grid.addWidget(self._build_chip_slot(), 0, 0, 1, 2)

        # The modifier: one signed box between its penalty steps and its bonus steps,
        # −5 −1 [+0] +1 +5. It used to be a −30..+30 slider, which is no way to hit +2.
        # Every row is a FieldRow — a box, its steps, and the slider it can have instead
        # (Settings ▸ General) — so all of them read and behave alike.
        # Emptied, it clears to +0; focused, its value is selected, so a typed number
        # replaces it (the user's report, 2026-11-13) — the same for every box here.
        self._modifier_spin = make_signed_spin_box(MODIFIER_LIMIT, snug=True, blank_clears=True)
        select_all_on_focus(self._modifier_spin)
        # "+0" at rest, not "0": signed like every value it can hold, so it reads as the
        # modifier the steps either side of it move, not as a sixth button.
        self._modifier_spin.set_zero_text("+0")
        self._modifier_spin.setToolTip(MODIFIER_HINT)
        self._modifier_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._modifier_spin.valueChanged.connect(self._mark_extras)
        self._modifier_spin.valueChanged.connect(self._inputs_edited)
        self._modifier_spin.valueChanged.connect(self._spread_modifier)
        self._modifier_label = self._row_label(MODIFIER_LABEL, MODIFIER_HINT)
        self._modifier_slider = self._row_slider(
            MODIFIER_SLIDER_MIN, MODIFIER_SLIDER_MAX, MODIFIER_SLIDER_HINT
        )
        self._modifier_row = FieldRow(
            "modifier", self._modifier_label, self._modifier_spin, self._modifier_slider
        )

        # Or two rows, Bonus and Penalty, as before the maneuvers rework (Settings ▸
        # General). The one modifier box stays the number every roll reads; these two
        # write their difference into it (:meth:`_gather_modifier`) and follow it
        # (:meth:`_spread_modifier`).
        self._spreading = False
        self._bonus_spin = self._split_box(BONUS_HINT)
        self._penalty_spin = self._split_box(PENALTY_HINT)
        for box in (self._bonus_spin, self._penalty_spin):
            box.valueChanged.connect(self._mark_extras)
        self._bonus_label = self._row_label(BONUS_LABEL, BONUS_HINT)
        self._penalty_label = self._row_label(PENALTY_LABEL, PENALTY_HINT)
        self._bonus_row = FieldRow(
            "bonus",
            self._bonus_label,
            self._bonus_spin,
            self._row_slider(0, SPLIT_SLIDER_MAX, BONUS_HINT),
        )
        self._penalty_row = FieldRow(
            "penalty",
            self._penalty_label,
            self._penalty_spin,
            self._row_slider(0, SPLIT_SLIDER_MAX, PENALTY_HINT),
        )

        # The DC is **one** box where empty ("—") means no DC — it used to be a
        # checkbox beside a box that sat greyed at 15 while unticked, two controls for
        # one number. Nobody grades a roll against DC 0, so 0 is free to mean "none",
        # and emptying the box is how to say it.
        self._dc_label = self._row_label(DC_LABEL, DC_HINT)
        self._dc_spin = make_spin_box(
            0, DC_LIMIT, value=0, buttons=False, snug=True, blank_clears=True
        )
        select_all_on_focus(self._dc_spin)
        self._dc_spin.setSpecialValueText(DC_NONE)
        self._dc_spin.setToolTip(DC_HINT)
        # Centred like the modifier's box above it, which it lines up under.
        self._dc_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dc_spin.valueChanged.connect(self._mark_dc)
        self._dc_spin.valueChanged.connect(self._inputs_edited)
        self._dc_slider = self._row_slider(0, DC_SLIDER_MAX, DC_HINT)
        self._dc_row = FieldRow("dc", self._dc_label, self._dc_spin, self._dc_slider)

        # Take 10 is a tile in the Actions popup (and Ctrl+T): it was a button beside
        # the DC, little used for the room it took (the user's call, 2026-11-13).
        self._actions_button = QPushButton(ACTIONS_LABEL)
        self._actions_button.setToolTip(
            "Combat maneuvers and actions: attack maneuvers, Aid, Grab, Impress, Reroll…"
        )
        self._actions_button.clicked.connect(self.open_actions)

        # It may narrow to its caption: the platform's push-button minimum is wider than
        # it needs, and it was what pushed the buttons off the rows in the right strip.
        self._actions_button.setMinimumWidth(_caption_width(self._actions_button))

        self._controls = RollControls(
            modifier=self._modifier_row,
            bonus=self._bonus_row,
            penalty=self._penalty_row,
            dc=self._dc_row,
            actions=self._actions_button,
        )
        # The capsule a set DC is drawn as (see :meth:`_mark_dc`).
        self._dc_pair = self._controls.capsule
        grid.addWidget(self._controls, 1, 0, 1, 2)

        # The action lane: one line per action riding on the next roll.
        self._lane = ActionLane()
        self._lane.changed.connect(self._on_lane_changed)
        self._lane.edited.connect(self._refresh_preview)
        grid.addWidget(self._lane, 2, 0, 1, 2)
        self._apply_modifier_input()
        self._mark_dc()

        self._hidden_check = QCheckBox("Hidden roll")
        self._hidden_check.setToolTip(
            "Roll without anyone else seeing it. A hidden roll is never sent to the players."
        )
        self._hidden_check.setVisible(self._hidden_option)
        if self._hidden_option:
            grid.addWidget(self._hidden_check, 4, 0, 1, 2)
        return part

    # -- the field rows -------------------------------------------------------

    @staticmethod
    def _row_label(text: str, hint: str) -> QLabel:
        label = QLabel(text)
        label.setToolTip(hint)
        # The labels sit a little in from the column's edge: the DC capsule is drawn
        # from that edge (it cannot reach past the panel's), and its text needs air.
        label.setContentsMargins(_capsule_inset(), 0, _label_gap(), 0)
        return label

    @staticmethod
    def _row_slider(minimum: int, maximum: int, hint: str) -> QSlider:
        """A row's slider, for when the setting asks for sliders: ticks at every major
        circumstance step, as wide as the row shares out."""
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        slider.setTickInterval(MODIFIER_SLIDER_TICKS)
        slider.setPageStep(MODIFIER_SLIDER_TICKS)
        slider.setToolTip(hint)
        guard_wheel(slider)
        _stretch_across(slider)
        return slider

    def _split_box(self, hint: str) -> QSpinBox:
        """A Bonus or Penalty box: 0 up, centred, writing into the modifier."""
        box = make_spin_box(0, SPLIT_LIMIT, value=0, buttons=False, snug=True, blank_clears=True)
        select_all_on_focus(box)
        box.setToolTip(hint)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.valueChanged.connect(self._gather_modifier)
        return box

    def _gather_modifier(self, *_args) -> None:
        """Bonus or Penalty moved: the modifier is their difference."""
        if self._spreading:
            return
        net = self._bonus_spin.value() - self._penalty_spin.value()
        self._spreading = True
        try:
            self._modifier_spin.setValue(net)
        finally:
            self._spreading = False

    def _spread_modifier(self, value: int) -> None:
        """The modifier moved by itself (a step, a quick roll, a roll spending it): move
        Bonus or Penalty by the difference — a bonus added lands in Bonus, a penalty in
        Penalty — and empty both when it goes back to 0."""
        if self._spreading:
            return
        bonus, penalty = self._bonus_spin.value(), self._penalty_spin.value()
        delta = value - (bonus - penalty)
        if value == 0:
            bonus = penalty = 0
        elif delta > 0:
            bonus += delta
        elif delta < 0:
            penalty -= delta
        if max(bonus, penalty) > SPLIT_LIMIT:
            bonus, penalty = max(value, 0), max(-value, 0)
        self._spreading = True
        try:
            self._bonus_spin.setValue(bonus)
            self._penalty_spin.setValue(penalty)
        finally:
            self._spreading = False

    def modifier_input(self) -> str:
        """``"buttons"`` or ``"slider"``: how the rows are set right now."""
        return self._modifier_input

    def set_modifier_input(self, style: str) -> None:
        """Set every row — the modifier (or Bonus and Penalty), the DC — and the action
        lines with step buttons, or with sliders (``storage.MODIFIER_INPUTS``)."""
        if style not in storage.MODIFIER_INPUTS:
            style = storage.MODIFIER_INPUT_BUTTONS
        if style == self._modifier_input:
            return
        self._modifier_input = style
        self._apply_modifier_input()
        self.updateGeometry()
        self.contentChanged.emit()

    def modifier_split(self) -> bool:
        """Whether the modifier is showing as Bonus and Penalty rows."""
        return self._modifier_split

    def set_modifier_split(self, split: bool) -> None:
        """Show Bonus and Penalty rows (``True``) or the one Modifier row."""
        split = bool(split)
        if split == self._modifier_split:
            return
        self._modifier_split = split
        self._apply_modifier_input()
        self.updateGeometry()
        self.contentChanged.emit()

    def sync_modifier_input(self) -> None:
        """Re-read both settings (they were saved on the General page)."""
        self.set_modifier_input(storage.modifier_input())
        self.set_modifier_split(storage.modifier_split())

    def _apply_modifier_input(self) -> None:
        # Every row's widgets belong to the controls already, so showing is safe.
        self._controls.set_slider(self._modifier_input == storage.MODIFIER_INPUT_SLIDER)
        self._controls.set_split(self._modifier_split)
        self._lane.set_input(self._modifier_input)
        self._spread_modifier(self._modifier_spin.value())

    def _place_dc_capsule(self) -> None:
        """Lay the DC capsule over the DC row, a little proud of it."""
        controls = getattr(self, "_controls", None)
        if controls is not None:
            controls.place_capsule()

    def set_modifier_steps(self, steps: Sequence[int]) -> None:
        """Put *steps* either side of every box — the modifier's, Bonus's and
        Penalty's, the DC's — as penalties and as bonuses.

        ``(1, 5)`` — the host reads them from the game data — gives ``−5 −1 [+0] +1 +5``
        on every row. A step **adds** to its box, so +1 twice is +2. The panel reads no
        game data, so until a host calls this there are no steps, only the boxes.
        """
        old = [*self._step_buttons, *self._dc_step_buttons, *self._split_step_buttons]
        sizes = sorted({abs(int(step)) for step in steps if int(step)})
        signed = [-size for size in reversed(sizes)] + sizes

        def nudge(box: QSpinBox) -> Callable[[int], None]:
            return lambda amount: box.setValue(box.value() + amount)

        modifier = [self._step_button(step, self.add_bonus, "to the modifier") for step in signed]
        dc = [self._step_button(step, self._step_dc, "to the DC") for step in signed]
        bonus = [
            self._step_button(step, nudge(self._bonus_spin), "to the bonus") for step in signed
        ]
        penalty = [
            self._step_button(step, nudge(self._penalty_spin), "to the penalty") for step in signed
        ]
        self._step_buttons = modifier
        self._dc_step_buttons = dc
        self._split_step_buttons = [*bonus, *penalty]
        self._controls.set_steps(
            {"modifier": modifier, "dc": dc, "bonus": bonus, "penalty": penalty}
        )
        for button in old:
            discard_widget(button)
        self._apply_modifier_input()
        self.updateGeometry()
        self.contentChanged.emit()

    def set_dc_start(self, dc: int) -> None:
        """Where a bonus step takes a DC that is not set (the host reads it from the
        game data); 10 until it does."""
        self._dc_start = max(1, int(dc))

    def _step_dc(self, amount: int) -> None:
        """One DC step. Up from "not set" lands on the starting DC rather than on 1,
        and down past the bottom is "not set" again (the box's 0)."""
        value = self._dc_spin.value()
        if not value:
            if amount > 0:
                self._dc_spin.setValue(self._dc_start)
            return
        self._dc_spin.setValue(value + amount)

    def _step_button(self, step: int, apply: Callable[[int], None], what: str) -> QToolButton:
        """One step: **flat**, lit under the pointer. Boxed like the buttons beside them,
        five boxes in a row left the one field among them reading as just another
        button."""
        caption = f"{step:+d}".replace("-", "−")
        button = QToolButton()
        button.setText(caption)
        # Flat under Classic too, which draws a tool button natively.
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        # As narrow as its caption and a little air at the least — not the platform's
        # tool-button hint, which is what pushed the Actions button off the modifier row
        # in the 360px strip — as wide as the row shares out, and as tall as the field
        # beside it, so its hover fills the row.
        air = 2 * int(theme.metric("space.xs"))
        button.setMinimumWidth(button.fontMetrics().horizontalAdvance(caption) + air)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        button.setToolTip(f"{caption} {what}")
        button.setEnabled(not self._rolling)
        # Hidden until the grid parents it (a parentless widget is a window).
        button.hide()
        button.clicked.connect(lambda _=False, amount=step: apply(amount))
        return button

    def dc(self) -> int | None:
        """The DC box's number, or ``None`` when it is empty."""
        return self._dc_spin.value() or None

    def set_dc(self, dc: int | None) -> None:
        """Put *dc* in the DC box, or empty it (``None``)."""
        self._dc_spin.setValue(dc or 0)

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
        return {
            ACTION_REROLL: None if self._last_roll is not None else NOTHING_TO_REROLL,
            ACTION_REQUEST: self.request_reason(),
            ACTION_TAKE_TEN: None,
        }

    @property
    def can_reroll(self) -> bool:
        """Whether there is a roll that came back, for Reroll to throw again."""
        return self._last_roll is not None

    def _action_sections(self) -> list[GridSection]:
        own = self.own_actions()
        if self._menu_builder is not None:
            return list(self._menu_builder(own))
        shown, more = own_items(own)
        return [GridSection("Dice", tuple(shown)), GridSection("More", tuple(more), more=True)]

    def open_actions(self) -> ActionGridPopup | None:
        """Open the Actions popup under its button, and return it.

        A failure to build it is logged and costs the popup, never the panel: the
        sections come from game data a mod may have written.

        A click on the button while the popup is up only closes it: the popup catches
        that press first and does not pass it on to the button
        (:meth:`~mm_companion.ui.action_grid.ActionGridPopup.mousePressEvent`), which
        would otherwise land here and open it again.
        """
        if self._action_popup is not None:
            self._action_popup.close()
        try:
            popup = ActionGridPopup(
                self._action_sections(),
                self._actions_button,
                more_shown=storage.actions_show_more(),
            )
        except Exception:  # noqa: BLE001 - a bad menu must not take the roller down
            log_exception("Building the Actions popup")
            return None
        popup.picked.connect(self._on_action_picked)
        # Remembered by the app, not the panel: every roller opens it the same way.
        popup.moreToggled.connect(storage.set_actions_show_more)
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
        elif key == ACTION_TAKE_TEN:
            self.take_ten()
        elif key == ACTION_REQUEST:
            # Over the tile, as a submenu would open — but only once the Actions popup
            # has closed: two popups open at once fight over which one a click closes.
            at = self.menu_point()
            QTimer.singleShot(0, lambda: self.open_request(at))
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

    def _build_chip_slot(self) -> QStackedWidget:
        """Where the chip lands: an empty dashed outline until something is loaded.

        The chip used to be hidden when empty, so loading a trait added a row and the
        whole roller jumped taller (and back on ✕). A stack is as tall as its tallest
        page, so the slot keeps the chip's height either way. Swapped by
        :meth:`load_spec`. Dashed in the group frame's colour: ``border.empty`` is
        a palette role under Classic, which comes out as the background.
        """
        self._chip_slot = QStackedWidget()
        self._chip_slot.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._chip_empty = QFrame()
        self._chip_empty.setObjectName("specSlot")
        self._chip_empty.setStyleSheet(
            "#specSlot {"
            f" border: {int(theme.metric('border.width'))}px dashed"
            f" {theme.color('border.group')};"
            f" border-radius: {int(theme.metric('radius.chip'))}px;"
            "}"
        )
        # A muted line saying what goes here, so the empty slot reads as a place
        # waiting for something rather than a gap. Elided: it must never widen the roller.
        hint = QHBoxLayout(self._chip_empty)
        hint.setContentsMargins(int(theme.metric("space.md")), 0, int(theme.metric("space.md")), 0)
        self._chip_hint = ElidingLabel(CHIP_SLOT_HINT)
        self._chip_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chip_hint.setStyleSheet(f"color: {theme.color('text.muted')};")
        self._chip_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        hint.addWidget(self._chip_hint)
        self._chip_empty.setToolTip(CHIP_SLOT_HINT)
        self._chip_slot.addWidget(self._chip_empty)
        self._chip_slot.addWidget(self._spec_chip)
        self._chip_slot.setCurrentWidget(self._chip_empty)
        return self._chip_slot

    def _mark_extras(self) -> None:
        """Tint the Bonus/Penalty row while it holds anything: green above 0, red below.

        The slider is spent by the next roll (:meth:`_spend_extras`), so a value
        sitting in it is a pending charge on that roll and has to be as visible as
        a loaded trait. Only ``color`` goes on the spin box: a ``border``,
        ``padding`` or ``background`` there would lay its edit field over the arrows
        (rule 4 in the theme notes). Not bold either, on the label or the box: bold
        text is wider, so the roller grew sideways each time the value left 0.
        """
        value = self._modifier_spin.value()
        rows = (
            (
                self._modifier_label,
                self._modifier_spin,
                value,
                "tint.better" if value > 0 else "tint.worse",
            ),
            (self._bonus_label, self._bonus_spin, self._bonus_spin.value(), "tint.better"),
            (self._penalty_label, self._penalty_spin, self._penalty_spin.value(), "tint.worse"),
        )
        for label, box, amount, token in rows:
            style = tinted_style(token, bold=False) if amount else ""
            label.setStyleSheet(style)
            box.setStyleSheet(f"QSpinBox {{ {style} }}" if style else "")

    def _mark_dc(self) -> None:
        """Draw a set DC as a tinted capsule round its label and box.

        A DC set and forgotten grades every later roll against it, so a number in the
        box has to stand out from the resting "none". The capsule is the frame's, not
        the box's: a border or background on a spin box hands its edit field to the
        stylesheet engine (rule 4 in the theme notes), and tinting the text alone was
        tried first and read as a greyed-out, disabled field. The border is there,
        transparent, while no DC is set, so the capsule costs no pixels to appear.
        """
        token = "accent.dice"
        width = int(theme.metric("border.width"))
        radius = int(theme.metric("radius.chip"))
        self._place_dc_capsule()
        if self._dc_spin.value():
            look = (
                f"border: {width}px solid {theme.color(token)};"
                f" background: {theme.wash(token, 0.18)};"
            )
        else:
            look = f"border: {width}px solid transparent; background: transparent;"
        self._dc_pair.setStyleSheet(f"#dcPair {{ {look} border-radius: {radius}px; }}")
        self._dc_label.setStyleSheet(
            tinted_style(token, bold=False) if self._dc_spin.value() else ""
        )
        # "not set" reads as a placeholder only when it is muted like one. ``color``
        # alone on the spin box (theme rule 4).
        muted = theme.color("text.muted")
        self._dc_spin.setStyleSheet(
            "" if self._dc_spin.value() else f"QSpinBox {{ color: {muted}; }}"
        )

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
        then, self._then = self._then, None
        if then is not None:
            # Already filled in for this sheet: a second pass would add its number again.
            self.load_spec(then, localize=False)

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
        """Where a chooser opened from the Actions popup appears: over the popup, on
        the tile just picked (a submenu of it), or under the button outside a pick."""
        popup = self._action_popup
        point = popup.follow_up_point() if popup is not None else None
        if point is not None:
            return point
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
        fits: LineFit | None = None,
        describe: LineDescribe | None = None,
    ) -> ActionLine | None:
        """Put an action on the lane for the next roll; ``None`` if it was called off.

        *limit* makes it configurable (a value over ``0..limit(spec)[0]``, starting at
        the maximum); otherwise it shows *fixed*. A line that *needs_attack* with no
        attack loaded first opens the attack picker, and the attack picked is loaded
        onto the chip; dismissing the picker calls the action off. *fits* says why a
        fixed line cannot ride on the attack loaded (greyed with the reason). *describe*
        says in words what a configurable line's value trades, beside its steps.
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
            fits=fits,
            describe=describe,
        )
        self._lane.add(line)
        line.bind(self.loaded_attack())
        # A pick is the player setting up the next roll, so the result beside the die
        # gives way to what that roll now comes to (it is in the history either way).
        self._inputs_edited()
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

    def remove_action_line(self, key: str) -> None:
        """Take the line *key* off the lane, if it is there."""
        self._lane.remove(key)

    def action_lines(self) -> list[ActionLine]:
        """The lines on the lane, in order (greyed ones too)."""
        return self._lane.lines()

    def active_lines(self) -> list[tuple[str, int | None]]:
        """``(key, value)`` for every line that applies to the roll as things stand —
        not one greyed out, and not one set to 0, which trades nothing and so leaves no
        condition behind either (it goes off the lane on its own, see
        :data:`~mm_companion.ui.roller.action_lane.ZERO_GRACE_MS`)."""
        return [(line.key, line.value()) for line in self._lane.lines() if line.applies]

    def relimit_lines(self) -> None:
        """Work the lane's limits out again: what they read has moved (the actor's
        Defense after Growth, a condition). The host calls it when its character changes."""
        self._lane.relimit()

    def _on_lane_changed(self) -> None:
        self.updateGeometry()
        self.contentChanged.emit()
        # A line came or went — the player's doing, or the roll's as it lands, which
        # :meth:`_inputs_edited` tells apart.
        self._inputs_edited()

    def _build_die(self) -> QWidget:
        """The die and the number it rolled, side by side — one part, since the two
        belong together.

        The result sits **beside** the die, not under it: under it, a 200px die and
        three lines of readout were the tallest thing in the strip, where height is the
        scarce thing. Compact mode stacks them again (:meth:`_arrange_die`), since there
        the part sits beside the quick rolls and is narrow.
        """
        part = QWidget()
        self._die_box = QBoxLayout(QBoxLayout.Direction.LeftToRight, part)
        self._die_box.setContentsMargins(0, 0, 0, 0)
        self._die_box.setSpacing(int(theme.metric("space.md")))

        self._die_holder = QWidget()
        grid = QGridLayout(self._die_holder)
        grid.setContentsMargins(0, 0, 0, 0)

        self._die_button = QPushButton()
        self._die_button.setObjectName("dieButton")
        self._die_button.setFlat(True)
        self._die_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # Not ``_start_roll`` itself: ``clicked`` passes its ``checked`` flag to a slot
        # that takes an argument, and ``_start_roll``'s first one is the ``before``
        # callback — the die called ``False(…)`` and never rolled.
        self._die_button.clicked.connect(self._on_die_clicked)
        self._die_button.setToolTip(f"Roll ({ROLL_SHORTCUT}) — {REROLL_SHORTCUT} rerolls")
        self._die_hovered = False
        self._die_button.installEventFilter(self)

        self._face = QLabel("?")
        self._face.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._face.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._face.setStyleSheet(f"color: {theme.color('dice.face')};")
        self._set_die_size("die.size", "size.dice-face")

        grid.addWidget(self._die_button, 0, 0)
        grid.addWidget(self._face, 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)

        self._readout = ReadoutLabel()
        self._readout.setWordWrap(True)
        self._readout.setTextFormat(Qt.TextFormat.RichText)
        # A little air on the right, so a long name wraps short of the block's border
        # rather than flush against it, which reads as text that has overflowed.
        self._readout.setContentsMargins(0, 0, int(theme.metric("space.sm")), 0)

        # Reroll, right under the result it would throw again — the Actions popup's tile
        # was the only way to it, two clicks and a hunt away from the number in question.
        self._reroll_button = fit_button(REROLL_LABEL)
        self._reroll_button.setToolTip(help_markup(f"{REROLL_HINT} ({REROLL_SHORTCUT})"))
        self._reroll_button.clicked.connect(lambda _=False: self.reroll())
        self._reroll_button.hide()

        # A short flag under the result, the whole sentence on hover: an attack
        # nothing will judge, or a save resisting one's own attack (_result_flag).
        self._warning = QLabel(NO_DC_SHORT)
        self._warning.setStyleSheet(tinted_style("tint.worse", bold=True))
        self._warning.setToolTip(help_markup(NO_DC_WARNING))

        self._result = QWidget()
        column = QVBoxLayout(self._result)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(int(theme.metric("space.xs")))
        column.addStretch(1)
        column.addWidget(self._readout)
        column.addWidget(self._warning)
        self._warning.hide()
        column.addWidget(self._reroll_button)
        column.addStretch(1)
        self._result_column = column
        self._die_box.addWidget(self._die_holder)
        self._die_box.addWidget(self._result, stretch=1)
        self._arrange_die(stacked=False)
        self._prompt_readout(replace_result=True)
        return part

    def _arrange_die(self, *, stacked: bool) -> None:
        """The readout beside the die (the usual), or under it (compact mode)."""
        self._die_box.setDirection(
            QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
        )
        if stacked:
            self._die_box.setAlignment(self._die_holder, Qt.AlignmentFlag.AlignHCenter)
            self._readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._result_column.setAlignment(self._reroll_button, Qt.AlignmentFlag.AlignHCenter)
            self._result_column.setAlignment(self._warning, Qt.AlignmentFlag.AlignHCenter)
        else:
            self._die_box.setAlignment(self._die_holder, Qt.AlignmentFlag.AlignVCenter)
            self._readout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._result_column.setAlignment(self._reroll_button, Qt.AlignmentFlag.AlignLeft)
            self._result_column.setAlignment(self._warning, Qt.AlignmentFlag.AlignLeft)

    def _sync_reroll_button(self) -> None:
        """Show Reroll under a result that can be thrown again, and nowhere else.

        Not under the resting prompt, a roll the session never answered, a die still
        tumbling, or a routine check (take 10 rolled nothing to be lucky with).
        """
        last = self._last_roll
        show = (
            self._showing_result
            and not self._rolling
            and last is not None
            and not last.get("routine")
            # Nobody throws a natural 20 away. Reroll is still on the Actions popup and
            # Ctrl+Shift+R for the table that wants it.
            and self._shown_natural != NATURAL_TWENTY
        )
        if self._reroll_button.isHidden() == (not show):
            return
        self._reroll_button.setVisible(show)
        self._readout_changed()

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
        """Light a halo round the die's outline: how the last roll went, else that a
        trait is loaded, else (faintly) that the pointer is over it.

        A halo rather than a ring: the ring was a rounded square with a tinted fill
        drawn round the button, and after a failed roll the strip showed a red square
        that read as an error tile rather than "this roll failed". The halo is a soft
        glow of the drawing's own silhouette (a drop shadow at no offset), so it follows
        the d20 and costs no layout: the button's fixed size never moves, and nothing
        ripples out through :attr:`contentChanged` for what is only a highlight.

        The button itself is drawn bare in every state — no border, no fill, hover
        included — so nothing rectangular is ever lit behind the die.
        """
        # How the last roll went outranks the armed halo while it lasts: it is the
        # news, and the chip above still says what is loaded.
        token = self._outcome or ("accent.dice" if self._spec is not None else None)
        strength = 1.0 if self._outcome else 0.85
        if token is None and self._die_hovered and self._die_button.isEnabled():
            token, strength = "accent.dice", 0.45
        self._die_button.setStyleSheet(
            "#dieButton, #dieButton:hover, #dieButton:pressed, #dieButton:disabled"
            " { border: none; background: transparent; }"
        )
        if token is None:
            self._die_button.setGraphicsEffect(None)
            return
        colour = QColor(theme.color(token))
        if not colour.isValid():  # a preset that follows the OS names a palette role
            colour = self.palette().color(QPalette.ColorRole.Highlight)
        colour.setAlphaF(strength)
        halo = QGraphicsDropShadowEffect(self._die_button)
        halo.setOffset(0, 0)
        halo.setBlurRadius(float(theme.metric("die.halo")))
        halo.setColor(colour)
        # Owned by the button from here: setting the next one deletes this.
        self._die_button.setGraphicsEffect(halo)

    def halo_token(self) -> str | None:
        """The colour token the die's halo is lit in right now, or ``None`` for none."""
        if self._die_button.graphicsEffect() is None:
            return None
        if self._outcome:
            return self._outcome
        return "accent.dice"

    def eventFilter(self, watched, event) -> bool:  # noqa: ANN001, N802 - Qt override
        """The die's hover: a faint halo says it can be clicked."""
        if watched is getattr(self, "_die_button", None) and event.type() in (
            QEvent.Type.Enter,
            QEvent.Type.Leave,
        ):
            self._die_hovered = event.type() == QEvent.Type.Enter
            self._apply_die_style()
        return super().eventFilter(watched, event)

    def _glow_outcome(self, token: str | None) -> None:
        """Light the die's halo in *token* for :data:`OUTCOME_GLOW_MS` — how the roll went, at a
        glance — or, with ``None``, put it straight back to its resting look."""
        self._outcome = token
        if token is None:
            self._outcome_timer.stop()
        else:
            self._outcome_timer.start(OUTCOME_GLOW_MS)
        self._apply_die_style()

    def _clear_outcome(self) -> None:
        self._glow_outcome(None)

    @property
    def outcome(self) -> str | None:
        """The colour token the die is glowing in right now, or ``None``."""
        return self._outcome

    def _build_quick_rolls(self) -> QWidget:
        """The quick rolls: one wrapping row of pills, and nothing at all while empty.

        It used to be a framed "Quick rolls" box, a thin empty frame until the first ☆;
        the empty history now says how to fill it instead.
        """
        part = QWidget()
        layout = QVBoxLayout(part)
        layout.setContentsMargins(0, 0, 0, 0)
        self._quick_container = QuickRollStrip()
        self._quick_flow = FlowLayout(self._quick_container, spacing=int(theme.metric("space.sm")))
        # Each line of pills shares out the whole width, like the rows above it.
        self._quick_flow.set_justify(True)
        self._quick_flow.setContentsMargins(0, 0, 0, 0)
        self._quick_container.reordered.connect(self._reorder_quick_roll)
        layout.addWidget(self._quick_container)
        part.hide()
        return part

    # -- rolling -------------------------------------------------------------

    def _input_widgets(self) -> list[QWidget]:
        return [
            self._spec_chip,
            *(w for row in self._controls.all_rows() for w in (row.box, row.slider, *row.steps)),
            self._lane,
            self._dc_spin,
            self._hidden_check,
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

        A spec that names its own DC (a power's save) fills in the DC box, so the
        number it will roll against is visible rather than implied; a spec
        without one leaves the box exactly as the player left it.

        Clearing the chip takes that DC off again (it used to stay, grading later
        manual rolls against a trait no longer loaded). Only a DC this method set is
        withdrawn; one the player typed is theirs.
        """
        # *localize* false puts back a spec this panel already held, which has been
        # through the localizer once (twice would add this sheet's number again).
        if spec is not None and localize:
            if self._localizer is not None:
                spec = self._localizer(spec)
            elif spec.own_attack:
                # No sheet here (the GM's roller): no one's numbers go in, so it is
                # nobody resisting their own attack.
                spec = replace(spec, own_attack=False)
        # An explicit load is always a deliberate one, whatever a roll in flight had
        # in mind: it cancels the transience :meth:`roll_spec` asked for, so a spec
        # arriving over the bus mid-tumble is not swept away when the die settles.
        self._transient = False
        had_own_dc = self._spec is not None and self._spec.dc is not None
        self._spec = spec
        self._spec_minion = spec is not None and minion
        if spec is not None and spec.dc is not None:
            self.set_dc(spec.dc)
        elif had_own_dc:
            self.set_dc(None)
        self._chip_slot.setCurrentWidget(self._spec_chip if spec is not None else self._chip_empty)
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
        # The chip's slot keeps its height, but the readout under the die may not —
        # the bug a change of height used to cause is written up on
        # :attr:`contentChanged`.
        self.contentChanged.emit()

    def _prompt_readout(self, *, replace_result: bool) -> None:
        """Say the die is waiting, beside it, where the result appears.

        *replace_result* is what keeps this from eating a number: loading a trait
        replaces a result the player has already read (they have just said what they
        want to roll next, and the card is in the history either way), but *clearing*
        one must not — and clearing is exactly what :meth:`_settle_spec` does one
        line after the roll wrote its result there.
        """
        if self._showing_result and not replace_result:
            return
        if not hasattr(self, "_readout"):  # an input moved while the panel was building
            return
        was_result = self._showing_result
        self._showing_result = False
        self._shown_natural = None
        # The chip names what is loaded and the halo says the die is armed, so the name
        # is not repeated here (that made three). What *is* here is what the chip cannot
        # say: what a click rolls now, the modifier and the action lines added in. The
        # rich-text token: a palette role means nothing inside HTML.
        muted = theme.color("text.muted.rich")
        preview, breakdown = self.staged_preview()
        html = f"<b>{preview}</b><br>" if preview else ""
        html += f"<span style='color:{muted}'>{IDLE_PROMPT}</span>"
        before = self._readout.text()
        self._readout.setText(html)
        self._readout.setToolTip(breakdown)
        self._show_warning(None)
        self._sync_reroll_button()
        if was_result or before != html:
            # It may wrap to a different number of lines: whatever holds the panel
            # shares its height out again.
            self._readout_changed()

    def staged_preview(self) -> tuple[str, str]:
        """What a click of the die would roll now, as ``(line, hover)`` rich text —
        ``("d20 + 1 vs DC 15", <the breakdown>)`` — or ``("", "")`` for a plain d20
        with nothing set, where there is nothing to add up.

        The chip names the trait at its own number ("Blast: Attack +0 vs. Defense");
        the modifier box and an All-Out Attack's +3 are on top of that, and nothing
        showed the +1 they came to until the die had been thrown.
        """
        spec = self.with_lines(self._spec)
        box = self._modifier_spin.value()
        dc = spec.dc if spec is not None and spec.dc is not None else self.dc()
        if spec is None and not box and dc is None:
            return "", ""
        twice = spec is not None and spec.roll_twice
        modifier = box + (spec.modifier if spec is not None else 0)
        line = escape_rich_text(staged_working(modifier, twice=twice))
        if dc is not None and not label_names_dc(spec.label if spec else "", dc):
            line += f" vs DC {dc}"
        return line, staged_breakdown(spec, box, dc, twice=twice)

    def _inputs_edited(self, *_args) -> None:
        """The player moved the modifier, the DC or the lane: show what the next roll
        now comes to, in place of a result already read (it is in the history).

        Not while a die is in the air, and not for the changes a roll makes itself as
        it lands (:attr:`_winding_down`) — those would wipe the number it just rolled.
        """
        if self._rolling or self._winding_down:
            return
        self._prompt_readout(replace_result=True)

    def _refresh_preview(self) -> None:
        """Bring the preview up to date if it is what the readout shows. Never trades a
        result away: what calls this (a line's limit pulled down after a roll's condition
        landed) is not the player setting up a roll."""
        if self._rolling or self._showing_result:
            return
        self._prompt_readout(replace_result=False)

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
        return self._routine_next

    def _as_minion(self) -> bool:
        """Whether the roll in flight is a minion's — the last roll's, on a reroll."""
        if self._rerolling and self._last_roll is not None:
            return bool(self._last_roll.get("minion"))
        return self._spec_minion

    def _twice(self) -> bool:
        """Whether the roll in flight is rolled twice for the higher (Aim) — the last
        roll's, on a reroll."""
        if self._rerolling and self._last_roll is not None:
            return bool(self._last_roll.get("twice"))
        spec = self._made_of()
        return spec is not None and spec.roll_twice

    def _spend_routine(self) -> None:
        """A routine check is one roll's worth: forget it once that roll is made."""
        self._routine_next = False

    def take_ten(self) -> bool:
        """Make the check now as a routine one (take 10, PDF p17): the die counts as 10.

        Whatever is loaded, with the modifier and the DC as set — the same roll a click
        of the die makes, made as a 10. ``False`` if a die is already tumbling or the
        roll was called off.
        """
        if self._rolling:
            return False
        self._routine_next = True
        if not self._start_roll():
            self._routine_next = False
            return False
        return True

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
        dc = self.dc()
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

    def _on_die_clicked(self) -> None:
        """The die was clicked: roll what the panel holds."""
        self._start_roll()

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
            # What the lines read may have moved since they were set (a condition, a
            # power switched): pulled down to what fits now, never up.
            self._lane.relimit()
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
                "routine": self._routine_next,
                "minion": self._spec_minion,
                "lines": lines,
                "twice": bool(flight is not None and flight.roll_twice),
            }
            self._rolled_lines = lines
            self._then = flight.then if flight is not None else None
            if lines and self._spec is not None:
                # So a star saving this roll saves a pill that re-arms the lines.
                self.remember_recipe(
                    {"name": self._roll_label(), "bonus": bonus, "penalty": penalty},
                    self._spec,
                    lines,
                    self._modifier_spin.value(),
                )
        else:
            self._rolled_lines = ()
            self._then = None
        self._rolling = True
        self._sync_reroll_button()
        self._glow_outcome(None)
        self._celebrate(None)
        self._die_button.setEnabled(False)
        for widget in self._input_widgets():
            widget.setEnabled(False)
        self._roll_clock.restart()
        self._aim_first = None
        self._aim_stage = 1 if self._twice() and not self._routine() else 0
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
            twice=self._twice(),
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
        if self._aim_stage == 1 and self._roll_clock.elapsed() >= ROLL_DURATION_MS:
            self._land_first_die()
        elif self._aim_stage == 0 and self._roll_clock.elapsed() >= ROLL_DURATION_MS:
            self._reveal_session_roll(roll)

    def _tick_roll(self) -> None:
        """One flicker frame: show a random face, then reschedule at a widening
        interval so the tumble decelerates, or finish once there is a result."""
        if not self._rolling:  # a direct _finish_roll() (e.g. in tests) pre-empted us
            return
        if self._aim_stage == 2:  # resting on Aim's first die
            return
        elapsed = self._roll_clock.elapsed()
        duration = AIM_SECOND_MS if self._aim_stage == 3 else ROLL_DURATION_MS
        if elapsed >= duration:
            if not self._awaiting:
                if not self._land_first_die():
                    self._finish_roll()
                return
            if self._pending is not None:
                if not self._land_first_die():
                    self._reveal_session_roll(self._pending)
                return
            if elapsed >= SESSION_ROLL_TIMEOUT_MS:
                self._abandon_roll(NO_ANSWER)
                return
        self._face.setText(str(random.randint(1, 20)))
        # Ease-out: interval grows with the square of progress, so frames start
        # rapid and stretch out as the die settles.
        progress = min(1.0, elapsed / duration) if duration else 1.0
        interval = FLICKER_MIN_MS + (FLICKER_MAX_MS - FLICKER_MIN_MS) * progress**2
        self._tumble_timer.start(round(interval))

    def _land_first_die(self) -> bool:
        """Aim's first throw is over: show it and rest on it, then throw the second.

        ``False`` when there is no first die to show — a roll of one die, the second of
        two, or a tumble with no length (the tests', whose rolls land at once). Made
        here, the first die is thrown now and kept for :meth:`_finish_roll`; rolled by
        the session, both came back together and either is shown first — two
        independent dice, so which was "first" is only the order they are shown in.
        """
        if self._aim_stage != 1 or not ROLL_DURATION_MS:
            return False
        if self._awaiting:
            roll = self._pending or {}
            dice = [int(roll.get("die", 0)), int(roll.get("other_die") or 0)]
            face = random.choice(dice) if all(dice) else dice[0]
        else:
            self._aim_first = reroll_d20() if self._rerolling else (roll_d20(), True)
            face = self._aim_first[0]
        self._aim_stage = 2
        self._tumble_timer.stop()
        self._face.setText(str(face))
        self._readout.setText(
            f"<span style='color:{theme.color('text.muted.rich')}'>"
            f"{AIM_FIRST_TEXT.format(die=face)}</span>"
        )
        self._readout.setToolTip("")
        self._readout_changed()
        self._aim_pause.start(AIM_PAUSE_MS)
        return True

    def _throw_second_die(self) -> None:
        """The pause on Aim's first die is over: tumble for the second."""
        if not self._rolling or self._aim_stage != 2:
            return
        self._aim_stage = 3
        self._roll_clock.restart()
        self._tick_roll()

    def _finish_roll(self) -> None:
        """Resolve the roll locally and report it, ending any tumble in progress."""
        self._rolling = False  # stops the _tick_roll chain from rescheduling

        routine, minion, twice = self._routine(), self._as_minion(), self._twice()
        self._commit_last_roll()
        bonus, penalty, dc, _hidden = self._roll_parameters()
        if routine:
            die, natural = ROUTINE_DIE, False
        elif self._aim_first is not None:
            # Aim's first die, thrown and shown before the second tumbled.
            die, natural = self._aim_first
        else:
            die, natural = reroll_d20() if self._rerolling else (roll_d20(), True)
        self._end_aim()
        other_die = 0
        if twice and not routine:
            # Aim: a second die, the higher kept — with its own "natural" (a lucky
            # re-roll's lifted die is not one).
            second, second_natural = reroll_d20() if self._rerolling else (roll_d20(), True)
            if second > die:
                die, natural, other_die = second, second_natural, die
            else:
                other_die = second
        self._face.setText(str(die))

        modifier = bonus - penalty
        result = (
            resolve_check(modifier, dc, roll=die, natural=natural, added_success=not minion)
            if dc is not None
            else None
        )
        spec = self._rolled_spec()
        self._update_readout(
            die,
            modifier,
            dc,
            None if result is None else result.degree,
            bool(result is not None and result.critical),
            routine=routine,
            other_die=other_die,
            record={
                "die": die,
                "bonus": bonus,
                "penalty": penalty,
                "dc": dc,
                "degree": None if result is None else result.degree,
                "critical": bool(result is not None and result.critical),
                "label": self._roll_label(),
                "spec": None if spec is None else spec.to_dict(),
                "reroll": self._rerolling,
                "routine": routine,
                "other_die": other_die,
            },
        )
        self.localRoll.emit(
            {
                "die": die,
                "bonus": bonus,
                "penalty": penalty,
                "dc": dc,
                "result": result,
                "label": self._roll_label(),
                "spec": spec,
                "reroll": self._rerolling,
                "routine": routine,
                "minion": minion,
                "other_die": other_die,
            }
        )
        self._unlock_inputs()
        # Last, so everything above still sees what was rolled.
        self._wind_down()

    def _end_aim(self) -> None:
        """Forget Aim's two throws: the roll has landed, or been given up on."""
        self._aim_stage = 0
        self._aim_first = None
        self._aim_pause.stop()
        self._tumble_timer.stop()

    def _wind_down(self) -> None:
        """Clear what the roll that just landed used up — its one-roll trait, its
        modifier, its lines, a take 10 — leaving its result beside the die.

        Those inputs moving is the roll's doing, not the player's, so for as long as
        this runs they do not trade the result for the preview (:meth:`_inputs_edited`).
        """
        self._rerolling = False
        self._flight = None
        self._winding_down = True
        try:
            self._settle_spec()
            self._spend_extras()
            self._spend_routine()
        finally:
            self._winding_down = False

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
        self._end_aim()
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
            other_die=int(roll.get("other_die") or 0),
            record=roll,
        )
        self._unlock_inputs()
        # The die has settled — a paired history panel can now show this own roll.
        # The spec is already on the record (the server recorded what we sent and
        # broadcast it), so this is the same dict every other seat is reading.
        self.sessionRollRevealed.emit(roll)
        # And the window closes *after* the reveal, so the card this roll produced
        # is placed by the cue above rather than by the flush the close performs.
        self.awaitingOwnRoll.emit(False)
        self._wind_down()

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
        self._end_aim()
        self._pending_last = None
        self._rerolling = False
        self._rolled_lines = ()
        self._then = None
        self._flight = None
        # Take 10 was for the roll that never came back; a later click of the die is a
        # die roll, and with no box ticked any more nothing would say otherwise.
        self._routine_next = False
        self._disconnect_session()
        self.awaitingOwnRoll.emit(False)
        self._face.setText("?")
        self._readout.setText(f"<span style='color:{theme.color('tint.worse')}'>{message}</span>")
        self._readout.setToolTip("")
        self._warning.hide()
        self._reroll_button.hide()
        self._shown_natural = None
        self._readout_changed()
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
        # The Ask button follows whether a trait is chosen, for the same reason.
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
        other_die: int = 0,
        record: dict | None = None,
    ) -> None:
        """Show a result beside the die: the short line, and on hover everything the
        roll was made of (:func:`~mm_companion.ui.roll_history.roll_breakdown`)."""
        self._showing_result = True
        label = reroll_label(self._roll_label(), self._rerolling)
        natural = natural_kind(die, degree, critical, routine=routine, lifted=self._rerolling)
        html = readout_html(
            label,
            die,
            modifier,
            dc,
            degree,
            critical,
            hidden=hidden,
            routine=routine,
            other_die=other_die,
            natural=natural,
        )
        self._readout.setText(html)
        self._readout.setToolTip(roll_breakdown(record) if record is not None else "")
        self._show_warning(self._result_flag(dc))
        self._shown_natural = natural
        self._sync_reroll_button()
        self._readout_changed()
        self._glow_outcome(
            outcome_token(die, degree, critical, routine=routine, lifted=self._rerolling)
        )
        self._celebrate(natural)

    def _result_flag(self, dc: int | None) -> tuple[str, str, str] | None:
        """The flag under the result just shown, as ``(text, hover, tint)``, or ``None``.

        An attack nothing will judge, or a save rolled off one's own attack card
        (:attr:`~mm_companion.core.rules.RollSpec.own_attack`). Never both: a save is
        no attack.
        """
        if self._unjudged_attack(dc):
            return (NO_DC_SHORT, NO_DC_WARNING, "tint.worse")
        spec = self._rolled_spec()
        if spec is not None and spec.own_attack:
            return (OWN_ATTACK_SHORT, OWN_ATTACK_HINT, "tint.warning")
        return None

    def _show_warning(self, flag: tuple[str, str, str] | None) -> None:
        """The short flag under the result (:meth:`_result_flag`), or none."""
        if flag is None:
            if self._warning.isHidden():
                return
            self._warning.hide()
        else:
            text, hover, tint = flag
            self._warning.setText(text)
            self._warning.setToolTip(help_markup(hover))
            self._warning.setStyleSheet(tinted_style(tint, bold=True))
            self._warning.show()
        self._readout_changed()

    def warning_text(self) -> str:
        """The flag under the result as shown (``""`` when hidden), for tests and hosts."""
        return "" if self._warning.isHidden() else self._warning.text()

    def _celebrate(self, natural: str | None) -> None:
        """A natural 20 or 1 gets more than a glow (the user's call, 2026-11-10): a burst
        round the die in its colour, and the halo swelling (20) or the die shaking (1);
        a 20's numeral turns gold. ``None`` puts it all back (the next roll begins)."""
        token = NATURAL_TOKENS[NATURAL_TWENTY] if natural == NATURAL_TWENTY else "dice.face"
        self._face.setStyleSheet(f"color: {theme.color(token)};")
        if natural is None:
            if self._burst is not None:
                self._burst.stop()
            return
        if self._burst is None:
            self._burst = DieBurst(self)
        self._burst.play(natural, self._die_button)
        if natural == NATURAL_TWENTY:
            pulse_halo(self._die_button)
        elif natural == NATURAL_ONE:
            shake([self._die_button, self._face])

    @property
    def celebrating(self) -> str | None:
        """The natural roll whose burst is on screen now, or ``None``."""
        burst = self._burst
        return burst.kind if burst is not None and burst.running else None

    def _readout_changed(self) -> None:
        """The readout's text changed, and with it, perhaps, how many lines it wraps to:
        whatever holds the panel has to share its height out again, or a long result
        (the unjudged-attack warning) is cut off at the bottom of the panel.

        The layouts between the readout and the panel are invalidated **now**, not left
        to Qt: it passes the change up one level per turn of the event loop, so the view
        re-divided against the panel's old height-for-width and kept it.
        """
        widget: QWidget | None = self._readout
        while widget is not None and widget is not self:
            widget = widget.parentWidget()
            if widget is not None and widget.layout() is not None:
                widget.layout().invalidate()
        self.updateGeometry()
        self.contentChanged.emit()
