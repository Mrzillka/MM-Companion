"""The roller and the right history beside it, reflowing to the room it is given. This is
what the sheet's Dice block and the GM's Rolls block put on screen.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QGroupBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.ui.dice_roller import (
    DiceRollerPanel,
)
from mm_companion.ui.reflow import ReflowBox
from mm_companion.ui.roll_history import (
    MIN_HISTORY_WIDTH,
    RollHistoryPanel,
)
from mm_companion.ui.roller.history import LocalRollHistory
from mm_companion.ui.session_bridge import SessionBridge, live_session


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

    That is the ``auto`` layout. The user can instead *choose* a shape and have it
    everywhere: ``compact`` is the panel's business (see
    :meth:`DiceRollerPanel._apply_shape`), while ``extended`` is this view's — it
    pins the second arrangement above, the controls as a column beside a history
    filling the rest, whatever the room. See :func:`mm_companion.core.storage.dice_layout`.

    *history* is for a host that owns its own history panel and only wants the
    arrangement: GM Mode passes its ``gm=True`` one.
    """

    #: A team attack's call to roll was joined in the session history (the call's
    #: spec) — for the host to arm its roller. A host-given history is the host's own
    #: to connect.
    teamJoinRequested = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        hidden_option: bool = False,
        history: RollHistoryPanel | None = None,
    ) -> None:
        super().__init__(parent)
        self.panel = DiceRollerPanel(hidden_option=hidden_option)
        # A history handed in by the host stands in for both of the ones below, and
        # takes its attachment to the session with it: what the GM's panel shows off
        # the air is last night's persisted log, which this view knows nothing about.
        # So everything session-shaped here stands down when there is one.
        self._given_history = history
        if history is None:
            self.panel.localRoll.connect(self._add_local_card)
        # Held so the same object can be disconnected again; a fresh lambda per
        # sync would leave the old one attached and stack up.
        self._session_bridge: SessionBridge | None = None
        self._on_session_end = lambda *_: self._sync_session()

        self._history_part = self._build_history_panel()
        # Connected only now that the histories exist for it to reach.
        self.panel.quickRollsChanged.connect(self._sync_quick_roll_state)
        self.panel.contentChanged.connect(self._on_panel_resized)
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
        # Whether the panel and the history are out on loan to a compact window
        # (see :meth:`release_roller`). While they are, this view is an empty
        # splitter nobody can see, so every arrangement decision it would otherwise
        # make from its own width is meaningless — and would be made against parts
        # that are being laid out by somebody else.
        self._lent = False
        # The user's chosen shape (see :meth:`sync_dice_layout`). Only ``extended``
        # means anything to this view; ``compact`` is the panel's own.
        self._preference = storage.dice_layout()
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

        So offer the panel the row it asks for and nothing beyond it, settling for
        less only as the space runs out: down to the width its row needs plus the
        reflow's dead-band (at exactly the threshold it would refuse to flip), and
        below that back to a column, leaving the history its own minimum throughout.
        **Every pixel past what the controls ask for is the history's** — it is the
        only part of the block that scrolls, so length is worth something to it and
        nothing to a row of spin boxes.
        """
        available = self.reflow_available_width()
        if self._shape_locked():
            # A chosen shape (Compact as well as Extended) gets the width it asks
            # for, never the row-of-three width the auto branch measures: that left
            # the controls stretched across half the block, and handed Compact
            # two-thirds of a wide one.
            floor = self.panel.column_minimum_width()
            wanted = max(floor, self.panel.sizeHint().width())
            wanted = min(wanted, max(floor, available - MIN_HISTORY_WIDTH))
            return [wanted, max(1, available - wanted)]
        floor = self.panel.row_minimum_width() + self.REFLOW_HYSTERESIS
        spare = available - self._history_part.minimumSizeHint().width()
        if spare < floor:
            return [self.panel.column_minimum_width(), max(1, available)]
        # Exactly what the controls ask for: everything past that is the history's.
        # (The panel used to take a quarter of the surplus too, an empty slab on a
        # wide strip.)
        #
        # ``floor`` usually wins, and that is not the clamp misfiring: an empty
        # quick-roll strip asks for almost nothing, so ``row_natural_width``
        # under-reports the row. The floor is that row at its narrowest.
        natural = self.panel.row_natural_width()
        wanted = max(floor, min(natural, spare))
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

    def _shape_locked(self) -> bool:
        """Whether the panel's arrangement is chosen, by either preference.

        The question :meth:`_row_sizes` asks, and a wider one than
        :meth:`_row_locked`: what matters there is only that the panel will not
        reflow, so offering it a shape's width it will never adopt is wrong under
        Compact for exactly the reason it is wrong under Extended.
        """
        return self.panel.shape_locked and not self._lent

    def sync_reflow(self) -> bool:
        """Re-run the width-driven reflow, unless the parts are out on loan.

        **Extended does not pin this axis.** It used to force the row whatever the
        width, so :meth:`minimumSizeHint` reported the row's width and held the
        block, the pinned strip and the window open at close to 600px, squeezing the
        history in a side strip.

        So the room decides the axis, as for an auto roller, and Extended keeps what
        it was chosen for: the **panel** stays a column
        (``DiceRollerPanel._apply_shape``), so a wide roller is controls beside a
        history rather than one row of four, and :meth:`_row_sizes` offers that
        column its own width. Where a row does not fit, the parts stack.
        """
        if self._lent:
            return False
        return super().sync_reflow()

    def _divide_row(self) -> None:
        """Re-divide a row between the panel and the history (a no-op otherwise)."""
        if self.is_row and not self._user_sized and not self._lent:
            self._splitter.setSizes(self._row_sizes())

    def _redivide(self) -> None:
        """Share the space out again on whichever axis is in use.

        A splitter child with no stretch keeps the pixels it was given, so neither axis
        gives space back on its own when the panel needs less of it — the panel's
        minimum drops, and the splitter simply leaves it where it was. So any change to
        what the panel *contains* has to divide the space again explicitly. A split the
        user dragged is still left alone.
        """
        if self._user_sized or self._lent:
            return
        self._splitter.setSizes(self._row_sizes() if self.is_row else self._column_sizes())

    def _on_panel_resized(self) -> None:
        """The panel needs a different amount of room: share the space out again.

        A splitter child with no stretch keeps the pixels it was given, and a
        minimum going *down* provokes no resize at all — so neither direction
        corrects itself. Without this the panel simply kept its old share **plus**
        whatever had just appeared in it, and the block grew past the window instead
        of the history giving the room back.

        It runs twice, and needs to: a chip just dropped is deleted *later*, so the
        panel's size hint only tells the truth on the following turn — the same
        reason the deferred :attr:`_settle` pass exists for a resize.
        """
        self._redivide()
        self._settle.start(0)

    def _histories(self) -> tuple[QWidget, ...]:
        """The history panels this view drives — the injected one, or its own pair."""
        if self._given_history is not None:
            return (self._given_history,)
        return (self._local_history, self._session_history)

    def _sync_quick_roll_state(self) -> None:
        """Push the strip's contents into whichever history is on screen."""
        keys = self.panel.quick_roll_keys()
        room = not self.panel.quick_rolls_full()
        for history in self._histories():
            history.set_quick_roll_state(keys, room)

    # -- construction --------------------------------------------------------

    def _build_history_panel(self) -> QWidget:
        holder = QWidget()
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(0, 0, 0, 0)

        if self._given_history is not None:
            # One box, always shown: the host's history is already the right one for
            # whatever state the session is in, so there is nothing to swap between.
            box = QGroupBox("Rolls")
            box_layout = QVBoxLayout(box)
            box_layout.addWidget(self._given_history)
            self._given_history.saveToggled.connect(self.panel.toggle_quick_roll)
            self._given_history.rollFollowUp.connect(self.panel.roll_spec)
            # Hold this app's own roll until its die stops tumbling; the roller cues
            # both the window and the release, so a roll it never revealed is
            # flushed rather than stranded.
            self._given_history.set_defer_own(True)
            self.panel.awaitingOwnRoll.connect(self._given_history.set_awaiting_own)
            self.panel.sessionRollRevealed.connect(self._given_history.release_roll)
            outer.addWidget(box)
            return holder

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
        self._session_history.teamJoinRequested.connect(self.teamJoinRequested)
        # Hold this app's own roll until its die stops tumbling; the roller cues
        # both the window and the release (see the given-history branch above).
        self._session_history.set_defer_own(True)
        self.panel.awaitingOwnRoll.connect(self._session_history.set_awaiting_own)
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
        if self._given_history is not None:
            # The host owns the attachment (GM Mode's history follows the bridge
            # while there is one and the workspace's saved log when there is not).
            return
        bridge = live_session()
        if bridge is not self._session_bridge:
            self._follow(bridge)
        self._session_history.attach(bridge)
        was_session = self._session_box.isVisibleTo(self)
        self._session_box.setVisible(bridge is not None)
        self._local_box.setVisible(bridge is None)
        if was_session is not (bridge is not None):
            # Joining or leaving a table swaps which box the layout holds, and a
            # splitter child with no stretch keeps the pixels it was given — so
            # without this the incoming history inherits whatever division the
            # outgoing one was left at, and no resize follows to correct it. The
            # same two-pass re-division a quick roll coming or going needs, and for
            # the same reason; see :meth:`_on_quick_rolls_changed`.
            self._history_part.updateGeometry()
            self._redivide()
            self._settle.start(0)

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

    def add_local_note(self, text: str) -> None:
        """Write a note in the private history — what to do with one off the air.

        In a session the note goes to the server instead and comes back through the
        shared history like any other entry, so this is only ever the fallback.
        """
        if self._given_history is None:
            self._local_history.add_note(text)

    def add_local_request(self, spec: object) -> None:
        """Put a requested roll in the private history — the off-air fallback.

        The twin of :meth:`add_local_note`, and hollow for the same reason when a
        host supplies its own history: in a session the request goes to the server
        and comes back through the shared one.
        """
        if self._given_history is None:
            self._local_history.add_request(spec)

    # -- lifecycle -----------------------------------------------------------

    def detach(self) -> None:
        """Let go of the session — the host block or window is going away."""
        if self._given_history is None:
            self._session_history.detach()
        self._follow(None)

    # -- compact mode --------------------------------------------------------

    def release_roller(self) -> tuple[DiceRollerPanel, QWidget]:
        """Hand the panel and the history over to a compact window, and stand down.

        The mini roller is **this** roller moved, not a second one built beside it:
        the loaded spec, the quick-roll strip, the tumble in flight and — the one
        that would really show — the session history and its bridge all carry on
        because the widgets themselves carry on. A second view would have meant a
        second attachment to the table and the same rolls listed twice.

        The two are **hidden, not re-parented**, and handed back in order; the
        caller owns them until :meth:`restore_roller`, and its ``adopt`` takes them
        out of the splitter by putting them in its layout. A re-parent is the
        expensive half of the transition (Qt re-polishes every descendant, ~1000 at
        the :data:`~mm_companion.ui.roll_history.MAX_CARDS` cap), and doing it here
        too cost ~150 ms of frozen window. Hiding keeps them out of the splitter's
        layout meanwhile, and they always have a parent.
        """
        self._lent = True
        for part in self.reflow_parts():
            part.hide()
        return (self.panel, self._history_part)

    def sync_dice_layout(self) -> None:
        """Re-read the layout preference — the Settings page just changed it."""
        self.set_layout(storage.dice_layout())

    def set_layout(self, layout: str) -> None:
        """Take a chosen shape: this view's axis, and the panel's own arrangement.

        The two halves of one preference, which is why they are set together from
        one place rather than each reading the setting for itself.
        """
        self._preference = layout if layout in storage.DICE_LAYOUTS else storage.DICE_LAYOUT_AUTO
        self.panel.set_layout_preference(self._preference)
        # The axis first (locked or back under the room's control), then the
        # division, which has to be re-run whether or not the axis moved: the panel
        # has just changed shape, so its share of a row it was already in is the
        # wrong one. The usual second pass, since the parts' hints only tell the
        # truth on the following turn.
        self.sync_reflow()
        self._user_sized = False  # a chosen shape is not the one a handle was dragged to
        self._redivide()
        self._settle.start(0)

    def restore_roller(self) -> None:
        """Take the panel and the history back into the splitter."""
        self._lent = False
        for index, part in enumerate(self.reflow_parts()):
            self._splitter.insertWidget(index, part)
            part.show()
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        # The split is the compact window's leftover, chosen for a shape this is
        # not — so forget it and re-derive, twice: the parts have only just been
        # re-parented, and their hints tell the truth on the following turn. The
        # same two-pass idiom a quick roll coming or going needs.
        self._user_sized = False
        self.updateGeometry()
        self.sync_reflow()
        self._redivide()
        self._settle.start(0)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt override)
        self._sync_session()
        super().showEvent(event)
