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
    """The quick-roll parameters behind a shared roll — what "★ Save" saves."""
    dc = roll.get("dc")
    return {
        "bonus": int(roll.get("bonus", 0)),
        "penalty": int(roll.get("penalty", 0)),
        "dc": None if dc is None else int(dc),
    }


class SessionRollCard(QFrame):
    """One roll in the shared history: who rolled it, what it was, how it went.

    Deliberately not :class:`~mm_companion.ui.dice_roller.RollCard`: that one is
    the local roller's own entry and can be thrown away, while this is a line in
    a log everyone shares — there is no removing it, only saving its parameters
    for reuse, and only for a roll of one's own.
    """

    saveRequested = Signal(dict)

    def __init__(self, roll: dict, *, own: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._roll = dict(roll)

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
        heading = f"<b>{_escape(who)}</b>"
        if hidden:
            heading = f"{HIDDEN_MARK} {heading}"
        if label:
            heading += f" <span style='color:gray'>— {_escape(label)}</span>"
        name_line = QLabel(heading)
        name_line.setTextFormat(Qt.TextFormat.RichText)
        name_line.setWordWrap(True)
        info.addWidget(name_line)

        headline = (
            f"<b>{die + modifier}</b> " f"<span style='color:gray'>(d20 {die} {modifier:+d})</span>"
        )
        if dc is not None:
            headline += f" vs DC {int(dc)}"
        title = QLabel(headline)
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setWordWrap(True)
        info.addWidget(title)

        if degree is not None:
            outcome = QLabel(degree_label(int(degree), bool(roll.get("critical")), die))
            colour = theme.TINT_BETTER if int(degree) > 0 else theme.TINT_WORSE
            outcome.setStyleSheet(f"color: {colour};")
            info.addWidget(outcome)

        layout.addLayout(info, stretch=1)

        if own:
            # Only for a roll of one's own: saving someone else's modifiers into
            # your quick rolls would be saving their character's numbers.
            save_button = QPushButton("★ Save")
            save_button.setToolTip("Save these parameters to the quick rolls strip")
            save_button.clicked.connect(
                lambda: self.saveRequested.emit(roll_parameters(self._roll))
            )
            layout.addWidget(save_button, alignment=Qt.AlignmentFlag.AlignVCenter)


class RollHistoryPanel(QWidget):
    """The shared roll log, newest first, fed by a :class:`SessionBridge`."""

    #: A card's "★ Save" — the roll's parameters, for the roller's quick strip.
    saveRequested = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge: SessionBridge | None = None
        self._own_id = ""
        # Newest first, so the seq of the card at the top is the newest seen. Kept
        # to drop a roll that arrives twice — a fresh history replacing an
        # existing one overlaps with what was already appended.
        self._seen: set[int] = set()

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
        layout.addWidget(self._scroll)

    # -- the feed ----------------------------------------------------------

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
        bridge.historyReplaced.connect(self.set_rolls)
        self.set_rolls(bridge.history())

    def detach(self) -> None:
        """Stop following the session; the cards on screen stay as they are."""
        bridge, self._bridge = self._bridge, None
        if bridge is None:
            return
        pairs = ((bridge.rollAdded, self.add_roll), (bridge.historyReplaced, self.set_rolls))
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
        """Put one roll at the top of the list."""
        if not isinstance(roll, dict):
            return
        seq = roll.get("seq")
        if isinstance(seq, int):
            if seq in self._seen:
                return
            self._seen.add(seq)

        own = bool(self._own_id) and str(roll.get("player_id", "")) == self._own_id
        card = SessionRollCard(roll, own=own)
        card.saveRequested.connect(self.saveRequested)
        # Newest on top: insert above every existing card (the stretch is last).
        self._layout.insertWidget(0, card)
        self._trim()
        self._empty.setVisible(False)

    def clear(self) -> None:
        for card in self.cards():
            self._layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._seen.clear()
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


def _escape(text: str) -> str:
    """Escape a player-supplied string for the rich-text labels above.

    Display names and roll labels come off the wire, and these labels render
    HTML — an unescaped ``<`` would let a peer restyle someone else's history.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
