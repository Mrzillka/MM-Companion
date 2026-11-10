"""The private roll history: what this app rolled outside a session, one card per roll,
each removable and saveable as a quick roll.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.dice import CheckResult
from mm_companion.core.rules import RollSpec
from mm_companion.core.session.model import KIND_NOTE, KIND_REQUEST, KIND_ROLL
from mm_companion.ui import theme
from mm_companion.ui.roll_history import (
    HISTORY_SIZE_HINT,
    MAX_CARDS,
    NoteCard,
    QuickRollStar,
    RequestCard,
    announce_entry,
    chain_widgets,
    escape_rich_text,
    heading_row,
    latest_time,
    mark_natural,
    quick_roll_key,
    remove_button,
    reroll_label,
    reveal_hover_buttons,
    roll_breakdown,
    roll_headline,
    size_history_scroll,
    stamp_card,
    tighten_card,
)
from mm_companion.ui.widgets import (
    discard_widget,
)

#: What the private history says before there is anything in it — and how to keep a roll.
LOCAL_EMPTY_TEXT = (
    "No rolls yet. Click the die, or a trait on the sheet. ☆ on a roll keeps it as a quick " "roll."
)


def local_record(
    die: int,
    bonus: int,
    penalty: int,
    dc: int | None,
    result: CheckResult | None,
    *,
    label: str = "",
    routine: bool = False,
) -> dict:
    """A roll this app made on its own, in the shape a session record has.

    So the shared history's line builders (``roll_headline``) and the notification
    (``toast_widgets``) read it exactly as they read one off the wire. *routine* is a
    routine check (take 10), which the line names instead of a d20.
    """
    return {
        "routine": routine,
        "kind": KIND_ROLL,
        "player_name": "You",
        "label": label,
        "die": die,
        "bonus": bonus,
        "penalty": penalty,
        "dc": dc,
        "degree": None if result is None else result.degree,
        "critical": False if result is None else result.critical,
    }


class RollCard(QFrame):
    """One history entry: the die, the modifier breakdown, and (with a DC) the
    degree of success — plus a star to save its parameters and a ``✕`` to drop it."""

    #: This card's star was clicked — see
    #: :attr:`~mm_companion.ui.roll_history.SessionRollCard.saveToggled`, which is
    #: the same contract from the shared history's card.
    saveToggled = Signal(dict)
    removeRequested = Signal()

    #: A chain button on this card was pressed — see
    #: :func:`~mm_companion.ui.roll_history.chain_widgets`.
    rollFollowUp = Signal(object)

    def __init__(
        self,
        *,
        die: int,
        bonus: int,
        penalty: int,
        dc: int | None,
        result: CheckResult | None,
        label: str = "",
        spec: object = None,
        reroll: bool = False,
        routine: bool = False,
        minion: bool = False,
        other_die: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # What this card's star would save — its name and its numbers, not its DC.
        # The shared history's card says the same thing through
        # :func:`~mm_companion.ui.roll_history.roll_parameters`.
        self._params = {"name": label, "bonus": bonus, "penalty": penalty}

        layout = QHBoxLayout(self)
        info = QVBoxLayout()
        tighten_card(layout, info)

        # The same number line the shared history's card prints — degree and all, on
        # one line (see roll_headline) — and the same breakdown on hover.
        record = local_record(die, bonus, penalty, dc, result, routine=routine, label=label)
        record["reroll"] = reroll
        record["other_die"] = other_die
        record["spec"] = spec.to_dict() if isinstance(spec, RollSpec) else spec
        mark_natural(self, record)
        headline = roll_headline(record)
        # What was rolled leads, the way the shared history's card names the player. A
        # roll with nothing loaded has no name: its line says it was a d20 and what it
        # came to, and a "d20" over it in the accent colour read as a link.
        shown = reroll_label(label, reroll)
        if shown:
            headline = (
                f"<span style='color:{theme.color('accent.dice')}'>"
                f"{escape_rich_text(shown)}</span><br>"
                f"{headline}"
            )
        title = QLabel(headline)
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setWordWrap(True)
        title.setToolTip(roll_breakdown(record))
        # The star and the ✕ sit beside the first line, as on the shared history's
        # card (see heading_row), so the chain's buttons below get the card's width.
        buttons = heading_row(info, title)

        for widget in chain_widgets(
            spec,
            die=die,
            degree=None if result is None else result.degree,
            total=die + bonus - penalty,
            on_follow_up=self.rollFollowUp.emit,
            # Every card in the private history is one's own, and off the air the
            # roller is usually running both sides — so never assume the sheet in
            # front of us is the target's.
            localize=False,
            # A minion's 20 adds nothing, and nor does a lucky re-roll's: its die is
            # lifted, never a natural 20 (nor a routine 10, which is never 20).
            added_success=not minion and not reroll,
        ):
            info.addWidget(widget)

        layout.addLayout(info, stretch=1)

        self.star = QuickRollStar()
        self.star.clicked.connect(lambda: self.saveToggled.emit(dict(self._params)))
        buttons.addWidget(self.star, alignment=Qt.AlignmentFlag.AlignTop)

        # ✕, as on every other card: a "−" read as "collapse".
        remove = remove_button("Remove this roll from history", self.removeRequested.emit)
        buttons.addWidget(remove, alignment=Qt.AlignmentFlag.AlignTop)

    def parameters(self) -> dict:
        """The quick-roll parameters this card would save."""
        return dict(self._params)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override
        reveal_hover_buttons(self, True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        reveal_hover_buttons(self, False)
        super().leaveEvent(event)

    def set_save_state(self, saved_keys: set, room: bool) -> None:
        """Light this card's star if its roll is among *saved_keys*."""
        self.star.set_state(quick_roll_key(self._params) in saved_keys, room)


class LocalRollHistory(QWidget):
    """The private list of what *this* app rolled, newest first.

    The counterpart of :class:`~mm_companion.ui.roll_history.RollHistoryPanel`:
    that one is a view of a log the whole table shares, this one is a scratch list
    of one's own rolls that only exists while there is no session. Each card can
    be thrown away or saved to the quick-roll strip.

    It keeps the same :data:`~mm_companion.ui.roll_history.MAX_CARDS` cap. This
    list *is* the log, so the cap loses the oldest rolls, but unbounded was worse:
    every card is ~5 widgets that Qt re-polishes whenever the history changes
    parent or width, so after an evening's play the roller took seconds to move
    (see :meth:`~DiceRollerView.release_roller`).
    """

    #: A card's star — the roll's parameters, for the roller to save or unsave.
    saveToggled = Signal(dict)

    #: A card's follow-up button — the next roll in the chain, for the roller to make.
    rollFollowUp = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # What the roller's quick-roll strip holds, mirrored here so every card's
        # star agrees; see :meth:`set_quick_roll_state`.
        self._saved_keys: set = set()
        self._quick_room = True
        # Ids for the cards that need one to be removed by; negative, as the shared
        # history's pre-hosting entries are, so they can never collide with a
        # sequence number the server hands out.
        self._offline_seq = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.addStretch()
        # Inside the list, as the shared history's is, so it costs no height of its own;
        # it sinks under the cards and is shown only while there are none.
        self._empty = QLabel(LOCAL_EMPTY_TEXT)
        self._empty.setWordWrap(True)
        self._empty.setEnabled(False)
        self._layout.insertWidget(0, self._empty)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._container)
        # The same floors and the same hint the shared history takes, from the one
        # place both get them: these two are the same widget as far as a layout is
        # concerned, and differ only in where their cards come from.
        size_history_scroll(self._scroll)
        layout.addWidget(self._scroll)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Two cards' worth, however long the log is — see :func:`size_history_scroll`."""
        return HISTORY_SIZE_HINT

    def set_quick_roll_state(self, saved_keys: set, room: bool) -> None:
        """Tell every card what the quick-roll strip holds, so its star can show it.

        The counterpart of
        :meth:`RollHistoryPanel.set_quick_roll_state
        <mm_companion.ui.roll_history.RollHistoryPanel.set_quick_roll_state>` — the
        two histories take the same instruction, so whichever one is showing can be
        driven by the same wiring.
        """
        self._saved_keys = set(saved_keys)
        self._quick_room = bool(room)
        for card in self.cards():
            card.set_save_state(self._saved_keys, self._quick_room)

    def add_roll(self, roll: object) -> None:
        """Record a roll this app made on its own — the session was not involved."""
        if not isinstance(roll, dict):
            return
        card = RollCard(
            die=int(roll["die"]),
            bonus=int(roll["bonus"]),
            penalty=int(roll["penalty"]),
            dc=roll["dc"],
            result=roll["result"],
            label=str(roll.get("label", "")),
            spec=roll.get("spec"),
            reroll=bool(roll.get("reroll")),
            routine=bool(roll.get("routine")),
            minion=bool(roll.get("minion")),
            other_die=int(roll.get("other_die") or 0),
        )
        card.set_save_state(self._saved_keys, self._quick_room)
        card.saveToggled.connect(self.saveToggled)
        card.rollFollowUp.connect(self.rollFollowUp)
        card.removeRequested.connect(lambda c=card: self._remove_card(c))
        self._place(card)
        record = local_record(
            int(roll["die"]),
            int(roll["bonus"]),
            int(roll["penalty"]),
            roll["dc"],
            roll["result"],
            label=str(roll.get("label", "")),
            routine=bool(roll.get("routine")),
        )
        announce_entry(record, self)

    def add_note(self, text: str) -> None:
        """Write a line that nobody rolled — the off-air twin of a session note.

        The author is left off: this list is one's own, so naming oneself on every
        card would be noise. Otherwise it is the same card the shared history
        shows, which is the point of it living in the lower module.
        """
        card = NoteCard({"text": text}, show_author=False)
        self._place(card)
        announce_entry({"kind": KIND_NOTE, "player_name": "You", "text": text}, self)

    def add_request(self, spec: object) -> None:
        """Write a requested roll that reached nobody — the off-air twin of one.

        Still worth a card with no table: the button rolls the trait here, so the
        row parks a roll for later even solo. Unlike :meth:`add_note` it keeps its
        "Requested by you" line, or it reads like a follow-up chip a roll left.

        It can be thrown away like every card here: it needs no permission, only an
        id, so it takes a negative sequence number as a pre-hosting roll does.
        """
        self._offline_seq -= 1
        card = RequestCard(
            {
                "seq": self._offline_seq,
                "player_name": "you",
                "spec": spec.to_dict() if isinstance(spec, RollSpec) else spec,
            },
            can_remove=True,
        )
        card.rollRequested.connect(self.rollFollowUp)
        card.removeRequested.connect(lambda _seq, c=card: self._remove_card(c))
        self._place(card)
        announce_entry(
            {
                "kind": KIND_REQUEST,
                "player_name": "you",
                "spec": spec.to_dict() if isinstance(spec, RollSpec) else spec,
            },
            self,
        )

    def _place(self, card: QWidget) -> None:
        """Put *card* at the top (newest first), stamped with the time it was made."""
        newest = next((t for t in map(latest_time, self._entries()) if t is not None), None)
        stamp_card(card, datetime.now().astimezone(), newest)
        # Newest on top: insert above every existing card (the stretch is last).
        self._layout.insertWidget(0, card)
        self._trim()
        self._sync_empty()

    def cards(self) -> list[RollCard]:
        """The roll cards on screen, newest first (notes carry no parameters)."""
        found = []
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, RollCard):
                found.append(widget)
        return found

    def _entries(self) -> list[QWidget]:
        """Every card in the list, newest first — rolls, notes and requests alike.

        Wider than :meth:`cards` on purpose: that one answers "what could be saved
        to the quick-roll strip", this one answers "what is in the list", which is
        the question :meth:`_trim` has to ask. The layout holds nothing but cards,
        the empty-state line and its trailing stretch, so every other widget is one.
        """
        found = []
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None and widget is not self._empty:
                found.append(widget)
        return found

    def _trim(self) -> None:
        """Drop the oldest cards past :data:`MAX_CARDS`, the shared history's cap.

        Called from every one of the three ways a card gets here. The shared
        history's :meth:`~mm_companion.ui.roll_history.RollHistoryPanel._trim` is
        the same rule, and it reads the same constant so the two lists can never
        drift apart on how long a history is.
        """
        for widget in self._entries()[MAX_CARDS:]:
            self._layout.removeWidget(widget)
            discard_widget(widget)

    def _remove_card(self, card: QWidget) -> None:
        """Drop one card from the list — a roll's or a request's ``✕``."""
        self._layout.removeWidget(card)
        discard_widget(card)
        self._sync_empty()

    def _sync_empty(self) -> None:
        """Show the empty-state line exactly while there are no cards."""
        self._empty.setVisible(not self._entries())
