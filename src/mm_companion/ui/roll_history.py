"""The session's shared roll history — the same list on every screen at the table.

Every roll in a session is resolved by the server and broadcast, so this panel is
a *view* of that log rather than a record any one app keeps: it seeds from
:meth:`~mm_companion.ui.session_bridge.SessionBridge.history` when it attaches
(so a roller opened halfway through the evening is not blank) and appends each
:attr:`~mm_companion.ui.session_bridge.SessionBridge.rollAdded` after that. The
GM window and every player's Dice Roller show the same widget over the same feed.

Hidden GM rolls are the one asymmetry, and it is enforced a layer below this: the
server never puts one on the wire, so a player's panel cannot render what it was
never sent. The GM's own panel *does* see them, and marks them with
:data:`HIDDEN_MARK` so it is obvious which rolls the table cannot.

A roll can be struck from the log — but only the GM's panel (``gm=True``) shows the
``✕``. Clicking it asks the session to remove the roll, which replies with a
``rollRemoved`` broadcast; every panel drops the card off that reply, so the GM's
own screen and every player's fall away together.

Nothing here talks to a socket — the bridge is the only thing this module knows
about, and grading is plain arithmetic over the roll dict.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.session_bridge import SessionBridge

#: Marks a roll only the GM can see. Shown on the GM's own history; a player's
#: copy never receives a hidden roll at all.
HIDDEN_MARK = "👁"

#: How many cards the panel keeps on screen. The log itself is unbounded and
#: lives on the server; a few hundred widgets is already more than anyone
#: scrolls, and every one of them costs layout on each new roll.
MAX_CARDS = 200

EMPTY_TEXT = "No rolls yet — every roll at this table shows up here."

#: Narrowest a roll history may get. A card carries a name, a headline and its
#: star; below this its buttons truncate. Both histories pin this as a floor — a
#: scroll area asks for almost nothing on its own, so without it a history is
#: squeezed to a sliver by whatever it shares its space with (and, for the Dice
#: block, a reflow would read that sliver as "a row fits here").
MIN_HISTORY_WIDTH = 260

#: Shortest a roll history may get, for the same reason and pinned in the same two
#: places. A card is roughly 70px tall, so this is two of them and the start of a
#: third — enough that the list reads as a list. Without it the scroll area gives
#: its height away just as readily as its width, and at the Dice block's minimum
#: the history collapses to about one card.
MIN_HISTORY_HEIGHT = 200

#: How many quick rolls the strip holds. A cap rather than a scroll: the strip
#: shares the Dice block with the roll controls and the die, so an unbounded list
#: of chips ratchets the block — and the pinned strip holding it — ever taller. Six
#: is two rows of chips in the narrow strip, and more than anyone reaches for.
MAX_QUICK_ROLLS = 6


def degree_label(degree: int | None, critical: bool, die: int) -> str:
    """Human-readable degree of success from the graded numbers themselves.

    The counterpart of :func:`mm_companion.ui.dice_roller.degree_text`, which
    grades a local :class:`~mm_companion.core.dice.CheckResult`. A roll that came
    off the wire is a plain dict instead, so this takes the numbers directly and
    both paths end up with exactly the same words.
    """
    if degree is None:
        return ""
    count = abs(degree)
    base = "Success" if degree > 0 else "Failure"
    text = base if count == 1 else f"{base} ({count} degrees)"
    if critical:
        text += " — Nat 20!" if die == 20 else " — Nat 1!"
    return text


def roll_parameters(roll: dict) -> dict:
    """The quick-roll parameters behind a shared roll — what its star saves."""
    dc = roll.get("dc")
    return {
        "bonus": int(roll.get("bonus", 0)),
        "penalty": int(roll.get("penalty", 0)),
        "dc": None if dc is None else int(dc),
    }


def quick_roll_key(params: dict) -> tuple[int, int, int | None]:
    """A quick roll's identity: its numbers, **ignoring any name** it was given.

    Two quick rolls are the same roll when they load the same inputs, whatever they
    are called — so this is what de-duplicates the strip, and what a card's star
    matches itself against to know whether it is already saved. Comparing whole
    entries instead would make "+3 vs DC 15" and the same numbers named "Attack"
    two different rolls, and the star could then glow for one and not the other.
    """
    dc = params.get("dc")
    return (
        int(params.get("bonus", 0)),
        int(params.get("penalty", 0)),
        None if dc is None else int(dc),
    )


class QuickRollStar(QPushButton):
    """A card's star: save this roll to the quick-roll strip, or take it back out.

    Shared by both card classes — the local roller's :class:`RollCard` and the
    session's :class:`SessionRollCard` — so it lives here, the module they can both
    import (see :data:`MIN_HISTORY_WIDTH` for the same reason).

    It is a *state* as much as a button, which is the point: a roll already in the
    strip glows, so there is no need to look down at the chips to see whether this
    one is saved. Three looks, all from :meth:`set_state`:

    ``★`` lit — saved, and a click takes it back out again.
    ``☆`` muted — not saved, and there is room for it.
    ``☆`` disabled — not saved, and the strip is full (:data:`MAX_QUICK_ROLLS`).

    Deliberately no font size: the glyph rides the widget font, so it needs no size
    token, and nothing here sets ``font-size`` in a stylesheet (which would outrank
    a widget's own ``QFont`` — see the theme rules in ``CLAUDE.md``).
    """

    #: Kept the same in every state so the button's geometry doesn't jump as it
    #: lights up: the unsaved border is drawn in ``transparent``, not dropped.
    _WIDTH = 30

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(self._WIDTH)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._saved = False
        self.set_state(False, True)

    def is_saved(self) -> bool:
        """Whether this star is currently lit (its roll is in the strip)."""
        return self._saved

    def set_state(self, saved: bool, room: bool) -> None:
        """Light the star, mute it, or disable it for want of room."""
        self._saved = bool(saved)
        width = int(theme.metric("border.width.emphasis"))
        radius = int(theme.metric("radius.chip"))
        if saved:
            accent = theme.color("accent.dice")
            self.setText("★")
            self.setEnabled(True)
            self.setToolTip("In the quick rolls — click to take it out again")
            self.setStyleSheet(
                f"color: {accent};"
                f" background: {theme.wash('accent.dice', 0.25)};"
                f" border: {width}px solid {accent};"
                f" border-radius: {radius}px;"
            )
            return
        self.setText("☆")
        self.setEnabled(room)
        if room:
            self.setToolTip("Save these parameters to the quick rolls")
        else:
            self.setToolTip(
                f"The quick rolls are full ({MAX_QUICK_ROLLS}) — take one out to save this"
            )
        self.setStyleSheet(
            f"color: {theme.color('text.muted.rich')};"
            " background: transparent;"
            f" border: {width}px solid transparent;"
            f" border-radius: {radius}px;"
        )


class SessionRollCard(QFrame):
    """One roll in the shared history: who rolled it, what it was, how it went.

    Deliberately not :class:`~mm_companion.ui.dice_roller.RollCard`: that one is
    the local roller's own entry and can be thrown away, while this is a line in a
    log everyone shares. Its parameters can be saved for reuse (for a roll of one's
    own), and the GM — and only the GM — can strike it from everyone's log with the
    ``✕`` button (``can_remove``).
    """

    #: This card's star was clicked — save these parameters as a quick roll, or, if
    #: they already are one, take them back out. The roller owns that decision (it
    #: owns the strip), so the card only reports the click and its parameters.
    saveToggled = Signal(dict)
    removeRequested = Signal(int)

    def __init__(
        self,
        roll: dict,
        *,
        own: bool = False,
        can_remove: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._roll = dict(roll)
        raw_seq = roll.get("seq")
        # A session roll has a positive seq; a pre-hosting (offline) roll is given a
        # negative one so it too has a stable id to remove by. Only a roll with no
        # id at all cannot be struck.
        self.seq = raw_seq if isinstance(raw_seq, int) else None

        die = int(roll.get("die", 0))
        bonus = int(roll.get("bonus", 0))
        penalty = int(roll.get("penalty", 0))
        modifier = bonus - penalty
        dc = roll.get("dc")
        degree = roll.get("degree")
        hidden = bool(roll.get("hidden"))

        layout = QHBoxLayout(self)
        info = QVBoxLayout()

        who = str(roll.get("player_name", "")) or "Someone"
        label = str(roll.get("label", ""))
        heading = f"<b>{escape_rich_text(who)}</b>"
        if hidden:
            heading = f"{HIDDEN_MARK} {heading}"
        if label:
            heading += (
                f" <span style='color:{theme.color('text.muted.rich')}'>"
                f"— {escape_rich_text(label)}</span>"
            )
        name_line = QLabel(heading)
        name_line.setTextFormat(Qt.TextFormat.RichText)
        name_line.setWordWrap(True)
        info.addWidget(name_line)

        headline = (
            f"<b>{die + modifier}</b> "
            f"<span style='color:{theme.color('text.muted.rich')}'>(d20 {die} {modifier:+d})</span>"
        )
        if dc is not None:
            headline += f" vs DC {int(dc)}"
        title = QLabel(headline)
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setWordWrap(True)
        info.addWidget(title)

        if degree is not None:
            outcome = QLabel(degree_label(int(degree), bool(roll.get("critical")), die))
            colour = theme.color("tint.better" if int(degree) > 0 else "tint.worse")
            outcome.setStyleSheet(f"color: {colour};")
            info.addWidget(outcome)

        layout.addLayout(info, stretch=1)

        # Only for a roll of one's own: saving someone else's modifiers into your
        # quick rolls would be saving their character's numbers.
        self.star: QuickRollStar | None = None
        if own:
            self.star = QuickRollStar()
            self.star.clicked.connect(lambda: self.saveToggled.emit(roll_parameters(self._roll)))
            layout.addWidget(self.star, alignment=Qt.AlignmentFlag.AlignVCenter)

        if can_remove and self.seq is not None:
            # GM only — strikes the roll from the log (from every screen, in a session).
            remove_button = QPushButton("✕")
            remove_button.setToolTip("Remove this roll from the history")
            remove_button.setFixedWidth(28)
            remove_button.clicked.connect(lambda: self.removeRequested.emit(self.seq))
            layout.addWidget(remove_button, alignment=Qt.AlignmentFlag.AlignVCenter)

    def parameters(self) -> dict:
        """The quick-roll parameters this card would save."""
        return roll_parameters(self._roll)

    def set_save_state(self, saved_keys: set, room: bool) -> None:
        """Light this card's star if its roll is among *saved_keys* (a no-op without one)."""
        if self.star is None:
            return
        key = quick_roll_key(self.parameters())
        self.star.set_state(key in saved_keys, room)


class RollHistoryPanel(QWidget):
    """The shared roll log, newest first, fed by a :class:`SessionBridge`."""

    #: A card's star — the roll's parameters, for the roller to save or unsave.
    saveToggled = Signal(dict)
    #: A roll the GM struck with no live session to remove it from — so an owner
    #: (the GM window) can drop it from a persisted, off-air session too.
    rollRemovedLocally = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, gm: bool = False) -> None:
        super().__init__(parent)
        self._bridge: SessionBridge | None = None
        self._own_id = ""
        # What the roller's quick-roll strip currently holds, so every card's star —
        # the ones on screen and the ones still to be built — shows the same thing.
        # Pushed in by whoever owns both this panel and the roller; see
        # :meth:`set_quick_roll_state`.
        self._saved_keys: set = set()
        self._quick_room = True
        # The GM's panel gets a per-roll ✕ to strike a roll from everyone's log.
        self._gm = gm
        # Newest first, so the seq of the card at the top is the newest seen. Kept
        # to drop a roll that arrives twice — a fresh history replacing an
        # existing one overlaps with what was already appended.
        self._seen: set[int] = set()
        # When paired with a dice roller (see :meth:`set_defer_own`), one's own
        # roll is held here until the die finishes tumbling, so the card lands as
        # the die settles rather than the instant the server resolves it.
        self._defer_own = False
        self._held: dict[int, dict] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._empty = QLabel(EMPTY_TEXT)
        self._empty.setWordWrap(True)
        self._empty.setEnabled(False)
        layout.addWidget(self._empty)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.addStretch()

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._container)
        self._scroll.setMinimumWidth(MIN_HISTORY_WIDTH)
        self._scroll.setMinimumHeight(MIN_HISTORY_HEIGHT)
        layout.addWidget(self._scroll)

    # -- the quick-roll strip's state --------------------------------------

    def set_quick_roll_state(self, saved_keys: set, room: bool) -> None:
        """Tell every card what the quick-roll strip holds, so its star can show it.

        *saved_keys* are :func:`quick_roll_key` tuples and *room* is whether another
        quick roll would fit. Remembered as well as applied, since a card built after
        this call has to start out in the same state (see :meth:`_insert`).
        """
        self._saved_keys = set(saved_keys)
        self._quick_room = bool(room)
        for card in self.cards():
            card.set_save_state(self._saved_keys, self._quick_room)

    # -- the feed ----------------------------------------------------------

    def set_defer_own(self, defer: bool) -> None:
        """Hold one's own roll until :meth:`release_roll`, matching a die's tumble.

        Set on a panel paired with a :class:`~mm_companion.ui.dice_roller.DiceRollerPanel`,
        whose ``sessionRollRevealed`` drives :meth:`release_roll`. Other players'
        rolls — which have no local animation to wait for — still land at once.
        """
        self._defer_own = defer

    def attach(self, bridge: SessionBridge | None) -> None:
        """Follow *bridge*'s rolls, starting from the history it already has.

        Safe to call repeatedly with the same bridge — attaching detaches first,
        so a window re-syncing on show never ends up double-connected and
        rendering every roll twice.
        """
        self.detach()
        if bridge is None:
            return
        self._bridge = bridge
        self._own_id = bridge.own_player_id()
        bridge.rollAdded.connect(self.add_roll)
        bridge.rollRemoved.connect(self.remove_roll)
        bridge.historyReplaced.connect(self.set_rolls)
        self.set_rolls(bridge.history())

    def detach(self) -> None:
        """Stop following the session; the cards on screen stay as they are."""
        bridge, self._bridge = self._bridge, None
        if bridge is None:
            return
        pairs = (
            (bridge.rollAdded, self.add_roll),
            (bridge.rollRemoved, self.remove_roll),
            (bridge.historyReplaced, self.set_rolls),
        )
        for signal, slot in pairs:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    # -- rendering ---------------------------------------------------------

    def set_rolls(self, rolls: object) -> None:
        """Replace everything on screen with *rolls* (oldest first, as stored)."""
        self.clear()
        if not isinstance(rolls, list):
            return
        for roll in rolls[-MAX_CARDS:]:
            self.add_roll(roll)

    def add_roll(self, roll: object) -> None:
        """Put one roll at the top of the list.

        One's own roll is held (not shown) while :attr:`_defer_own` is set, so a
        paired roller can release it as the die settles — see :meth:`release_roll`.
        """
        if not isinstance(roll, dict):
            return
        seq = roll.get("seq")
        if isinstance(seq, int) and seq in self._seen:
            return
        if self._defer_own and self._is_own(roll):
            if isinstance(seq, int):
                self._held[seq] = roll
            return
        self._insert(roll)

    def release_roll(self, roll: object) -> None:
        """Show a previously-deferred own roll now that its die has settled."""
        if not isinstance(roll, dict):
            return
        seq = roll.get("seq")
        if isinstance(seq, int):
            self._held.pop(seq, None)
        self._insert(roll)

    def _is_own(self, roll: dict) -> bool:
        return bool(self._own_id) and str(roll.get("player_id", "")) == self._own_id

    def _insert(self, roll: dict) -> None:
        """Build and place a card for *roll*, deduplicating by ``seq``."""
        seq = roll.get("seq")
        if isinstance(seq, int):
            if seq in self._seen:
                return
            self._seen.add(seq)

        card = SessionRollCard(roll, own=self._is_own(roll), can_remove=self._gm)
        card.set_save_state(self._saved_keys, self._quick_room)
        card.saveToggled.connect(self.saveToggled)
        card.removeRequested.connect(self._request_remove)
        # Newest on top: insert above every existing card (the stretch is last).
        self._layout.insertWidget(0, card)
        self._trim()
        self._empty.setVisible(False)

    def _request_remove(self, seq: int) -> None:
        """A ✕ was clicked — drop the roll (GM action).

        In a live session the server removes it and echoes :attr:`rollRemoved` back,
        which drives :meth:`remove_roll` so every screen (this one included) drops it
        together. With no live session — a roll made before hosting, or a resumed
        session shown off the air — there is nothing to broadcast to, so the card is
        dropped here and :attr:`rollRemovedLocally` lets an owner persist that.
        """
        if self._bridge is not None and self._bridge.remove_roll(seq):
            return
        self.remove_roll(seq)
        self.rollRemovedLocally.emit(seq)

    def remove_roll(self, seq: int) -> None:
        """Drop the card for *seq* (from a ``rollRemoved`` broadcast); no-op if absent."""
        for card in self.cards():
            if card.seq == seq:
                self._layout.removeWidget(card)
                card.setParent(None)
                card.deleteLater()
                break
        self._seen.discard(seq)
        if not self.cards():
            self._empty.setVisible(True)

    def clear(self) -> None:
        for card in self.cards():
            self._layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._seen.clear()
        self._held.clear()
        self._empty.setVisible(True)

    def cards(self) -> list[SessionRollCard]:
        """The cards on screen, newest first.

        Read off the layout rather than ``findChildren``, which answers in
        construction order — the opposite end of the list from where a new card
        goes.
        """
        found = []
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, SessionRollCard):
                found.append(widget)
        return found

    def _trim(self) -> None:
        """Drop the oldest cards once the list is longer than :data:`MAX_CARDS`."""
        # The stretch is the last item, so the oldest card sits just above it.
        while self._layout.count() - 1 > MAX_CARDS:
            item = self._layout.itemAt(self._layout.count() - 2)
            widget = item.widget() if item is not None else None
            if widget is None:
                return
            self._layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()


def escape_rich_text(text: str) -> str:
    """Escape a player-supplied string for a rich-text label.

    Display names and roll labels come off the wire, and these labels render
    HTML — an unescaped ``<`` would let a peer restyle someone else's history.
    Public because the private history's cards (:mod:`mm_companion.ui.dice_roller`)
    render the same strings and must escape them the same way.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
