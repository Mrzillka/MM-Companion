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

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.data_loader import load_game_data
from mm_companion.core.rules import (
    RollSpec,
    follow_up_for_result,
    outcome_is_failure,
    resistance_outcome,
)
from mm_companion.core.session.model import KIND_MOD, KIND_NOTE, KIND_REQUEST, KIND_ROLL
from mm_companion.ui import theme
from mm_companion.ui.session_bridge import SessionBridge
from mm_companion.ui.toasts import announce
from mm_companion.ui.widgets import discard_widget, muted_style, tinted_style

#: Marks a roll only the GM can see. Shown on the GM's own history; a player's
#: copy never receives a hidden roll at all.
HIDDEN_MARK = "👁"
#: How a lucky re-roll (``reroll`` on the record) is named after its label, so a
#: die that could not come up under 11 never passes for an ordinary one.
REROLL_MARK = "(reroll)"

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

#: How tall a roll history *asks* to be. A card is roughly 70px, so this is two of
#: them and the start of a third — enough that the list reads as a list. It is the
#: history's size **hint**, deliberately not its minimum: see
#: :data:`HISTORY_FLOOR_HEIGHT`.
MIN_HISTORY_HEIGHT = 200

#: Shortest a roll history may actually be squeezed to — one card.
#:
#: The distinction between this and :data:`MIN_HISTORY_HEIGHT` is the whole fix for
#: a bug worth remembering. A block's minimum is its *content's* height
#: (``BlockFrame.minimumSizeHint``), and the strip propagates that up to hold the
#: window open. With 200px pinned as a hard minimum, the history could never give
#: a pixel back — so the moment the roll panel grew (a stat clicked on the sheet
#: shows the spec chip) the whole Dice block grew with it, past the window, and the
#: strip answered with a scrollbar. The history is the elastic part of that block:
#: it is a list, it has its own scroll area, and shortening it is the honest way to
#: find room. So the *hint* asks for two cards and this floor is what it will
#: settle for. The width has no such split — the reflow's row/column decision reads
#: :data:`MIN_HISTORY_WIDTH` as a real minimum.
HISTORY_FLOOR_HEIGHT = 90


def size_history_scroll(scroll: QScrollArea) -> None:
    """Give a history's scroll area its floors, and hand back the size to ask for.

    Both histories — the private :class:`~mm_companion.ui.dice_roller.LocalRollHistory`
    and the shared :class:`RollHistoryPanel` — are the same widget in every way
    that matters to a layout: a scrolling list of the same cards. So the three
    numbers that make one behave live here once, rather than being restated in a
    module that only differs in where its cards come from.

    Pair it with ``sizeHint`` returning :data:`HISTORY_SIZE_HINT`, which is the
    half a scroll area cannot be told: left alone it reports its *inner widget's*
    hint, so a history grew with every roll — and since a block's minimum is its
    content's preferred height, the Dice block's minimum climbed all session until
    the pinned strip could no longer fit it. A history is a scrolling list; what it
    wants is a readable window onto the log, not the whole log.
    """
    scroll.setMinimumWidth(MIN_HISTORY_WIDTH)
    scroll.setMinimumHeight(HISTORY_FLOOR_HEIGHT)


#: What a history asks for: two cards' worth, however long the log actually is.
#: See :func:`size_history_scroll` for the half of the bargain it belongs to.
HISTORY_SIZE_HINT = QSize(MIN_HISTORY_WIDTH, MIN_HISTORY_HEIGHT)

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
    """The quick-roll parameters behind a shared roll — what its star saves.

    Its **name** as well as its numbers: a roll travels named (``label``), and a
    strip of chips reading ``+6``, ``+4``, ``+4`` is a puzzle a few rolls later.
    Saving is still one click with no dialog — the name is simply the one the roll
    already had, and the chip's own ▸ Rename… is for changing it afterwards.

    The **DC is deliberately not saved.** A quick roll is a roll you make often;
    the difficulty it is made against belongs to the situation in front of you, not
    to the roll, and a chip that silently re-armed the DC box with a number from
    last session graded everything after it against a difficulty nobody had chosen.
    """
    return {
        "name": str(roll.get("label", "")),
        "bonus": int(roll.get("bonus", 0)),
        "penalty": int(roll.get("penalty", 0)),
    }


def quick_roll_key(params: dict) -> tuple[str, int, int]:
    """A quick roll's identity: what it is called, and the numbers it loads.

    The name counts because it is no longer decoration typed on afterwards — it is
    saved from the roll, so "Strength +4" and "Dodge +4" are two different quick
    rolls, and a card's star must light for its own. This is what de-duplicates the
    strip and what a card matches itself against.

    The consequence to know: renaming a chip re-identifies it, so the star on the
    card it was saved from goes dark. That is the honest reading — the chip is not
    that roll any more — but it is why :meth:`DiceRollerPanel._rename_quick_roll`
    refuses a name that would collide with another chip.
    """
    return (
        str(params.get("name", "")),
        int(params.get("bonus", 0)),
        int(params.get("penalty", 0)),
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
    a widget's own ``QFont`` — see the theme rules in
    ``docs/notes/theme.md``).
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
            self.setToolTip("Save this roll to the quick rolls")
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


def chain_widgets(
    spec: object,
    *,
    die: int,
    degree: int | None,
    on_follow_up: Callable[[RollSpec], None],
    localize: bool = True,
    total: int | None = None,
) -> list[QWidget]:
    """What a rolled :class:`RollSpec` adds under its history card: the chain.

    Two things, either or neither:

    * a **follow-up button** when the roll provoked another one and landed — an
      attack that hit makes its target save, and the button primes that save with
      its DC already filled in, adjusted for a critical hit or a natural 1
      (:func:`~mm_companion.core.rules.follow_up_for_result`);
    * an **outcome line** — "Incapacitated!" on a failed save, or the Hit a *made*
      Toughness save still costs, rather than a bare "2 degrees of failure".

    This goes on **every** card, not only one's own, and that is the point of the
    whole chain: the player rolls the attack, and the target's player — reading the
    same history — clicks the save straight off it. The spec travels with the roll
    (see ``docs/mm-session-architecture.md``); the crit adjustment and the ladder
    lookup are then computed here from the broadcast die and degree, so every screen
    at the table derives the same chip and the same sentence without the server
    knowing a single rule.

    Accepts the spec as a :class:`RollSpec` or as the plain dict it arrives as.
    ``total`` is the roll's total, which an *opposed* follow-up (resisting an
    impression) takes as its DC.

    ``localize`` is what decides whether the follow-up arrives with the reader's own
    resistance filled in (:func:`~mm_companion.core.rules.localize_spec`, applied
    downstream by the Dice block). It is off on **one's own** card: you are not the
    target of your own attack, so quietly filling in your own Toughness there would
    be a confident wrong number. It is carried by dropping the spec's trait key
    rather than by a flag the whole chain would have to thread.
    """

    resolved = spec if isinstance(spec, RollSpec) else RollSpec.from_dict(spec)
    if resolved is None:
        return []
    widgets: list[QWidget] = []
    follow_up = follow_up_for_result(resolved, die, degree, load_game_data(), total)
    if follow_up is not None and not localize:
        follow_up = replace(follow_up, trait_key="")
    if follow_up is not None:
        button = QPushButton(f"🎲 {follow_up.label}")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(follow_up.hint or f"Roll {follow_up.label}")
        button.clicked.connect(lambda _=False, s=follow_up: on_follow_up(s))
        widgets.append(button)
    outcome = resistance_outcome(resolved, degree)
    if outcome:
        line = QLabel(f"{escape_rich_text(outcome)}!")
        line.setWordWrap(True)
        # A failed save is harm; a made one that still leaves a Hit is a caveat.
        tint = "tint.worse" if outcome_is_failure(degree) else "tint.warning"
        line.setStyleSheet(tinted_style(tint, bold=True))
        widgets.append(line)
    return widgets


def request_caption(spec: RollSpec) -> str:
    """What a requested roll is called on its button: the trait, and any DC."""

    return f"{spec.label} vs. DC {spec.dc}" if spec.dc is not None else spec.label


def request_widgets(spec: object, *, on_roll: Callable[[RollSpec], None]) -> list[QWidget]:
    """What a *requested* roll adds under its card: the one button that rolls it.

    Deliberately not :func:`chain_widgets`, which asks a rolled spec what it
    *provoked* — it needs a ``follow_up`` and a degree that passed. A request is
    not something a roll provoked; it **is** the roll, sitting in the history
    waiting for somebody to make it. Wrapping it as its own follow-up to reuse
    that function would mean a spec whose label nothing ever reads.

    It also never drops the spec's :attr:`~RollSpec.trait_key` the way
    ``chain_widgets(localize=False)`` does on one's own card. That rule is about
    *saves* — you are not the target of your own attack — and it does not hold
    here: someone who asks the table to roll Perception is asking themselves too,
    and their own sheet is the one screen that can fill the number in for certain.
    """

    resolved = spec if isinstance(spec, RollSpec) else RollSpec.from_dict(spec)
    if resolved is None:
        return []
    choices = [resolved]
    if resolved.dc_if_resisted is not None:
        # A save offered at two DCs — an area's, at full rank or at half after a made
        # Dodge: one button each, so whoever answers picks the one that is theirs.
        plain = replace(resolved, dc_if_resisted=None, note_if_resisted="")
        half = f"{resolved.label} (half)"
        if resolved.note_if_resisted:
            half = f"{half} — {resolved.note_if_resisted}"
        choices = [
            replace(plain, label=f"{resolved.label} (full)"),
            replace(plain, label=half, dc=resolved.dc_if_resisted),
        ]
    buttons: list[QWidget] = []
    for choice in choices:
        button = QPushButton(f"🎲 {request_caption(choice)}")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(choice.hint or f"Roll {choice.label} on this sheet")
        button.clicked.connect(lambda _=False, s=choice: on_roll(s))
        buttons.append(button)
    return buttons


def reroll_label(label: str, reroll: bool) -> str:
    """*label* as a roll is named — with :data:`REROLL_MARK` after it for a re-roll."""
    if not reroll:
        return label
    return f"{label} {REROLL_MARK}" if label else REROLL_MARK


def roll_heading(roll: dict) -> str:
    """A roll's first line, as rich text: who rolled it, and what it was."""
    who = str(roll.get("player_name", "")) or "Someone"
    label = reroll_label(str(roll.get("label", "")), bool(roll.get("reroll")))
    heading = f"<b>{escape_rich_text(who)}</b>"
    if roll.get("hidden"):
        heading = f"{HIDDEN_MARK} {heading}"
    if label:
        heading += (
            f" <span style='color:{theme.color('text.muted.rich')}'>"
            f"— {escape_rich_text(label)}</span>"
        )
    return heading


def roll_headline(roll: dict) -> str:
    """A roll's number line, as rich text: the total, how it was made, and how it went.

    The degree rides on the **same line** as the total rather than one of its own. A
    history is a column of these in a strip a few cards tall, and the line a degree
    used to take was a third of every card — the difference between three rolls in
    view and five.
    """
    die = int(roll.get("die", 0))
    modifier = int(roll.get("bonus", 0)) - int(roll.get("penalty", 0))
    muted = theme.color("text.muted.rich")
    breakdown = f"<span style='color:{muted}'>(d20 {die} {modifier:+d})</span>"
    headline = f"<b>{die + modifier}</b> {breakdown}"
    dc = roll.get("dc")
    if dc is not None:
        headline += f" vs DC {int(dc)}"
    degree = roll.get("degree")
    if degree is not None:
        colour = theme.color("tint.better" if int(degree) > 0 else "tint.worse")
        words = degree_label(int(degree), bool(roll.get("critical")), die)
        headline += f" · <span style='color:{colour}'>{escape_rich_text(words)}</span>"
    return headline


def _rich(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(True)
    return label


def toast_widgets(entry: dict) -> list[QWidget]:
    """A history entry as a notification reads it: who, what, and how it went.

    No buttons — a notification is read at a glance from the corner of the screen,
    and the history card it mirrors is where the follow-up chip and the star live.
    """
    kind = entry.get("kind", KIND_ROLL)
    who = escape_rich_text(str(entry.get("player_name", "")) or "Someone")
    if kind in (KIND_NOTE, KIND_MOD):
        text = QLabel(str(entry.get("text", "")))
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setWordWrap(True)
        text.setStyleSheet(muted_style(italic=True))
        return [_rich(f"<b>{who}</b>"), text]
    if kind == KIND_REQUEST:
        spec = RollSpec.from_dict(entry.get("spec"))
        widgets = [_rich(f"Requested by <b>{who}</b>")]
        if spec is not None:
            ask = QLabel(f"🎲 {request_caption(spec)}")
            ask.setWordWrap(True)
            ask.setStyleSheet(tinted_style("accent.dice", bold=True))
            widgets.append(ask)
        return widgets
    return [_rich(roll_heading(entry)), _rich(roll_headline(entry))]


def toast_accent(entry: dict) -> str | None:
    """The colour of a notification's edge: how the roll went, or what it is."""
    kind = entry.get("kind", KIND_ROLL)
    if kind == KIND_REQUEST:
        return theme.color("accent.dice")
    if kind != KIND_ROLL:
        return None
    degree = entry.get("degree")
    if degree is None:
        return theme.color("accent.dice")
    return theme.color("tint.better" if int(degree) > 0 else "tint.worse")


def announce_entry(entry: dict, source: QWidget | None) -> None:
    """Pop a *live* history entry up as a notification (see :mod:`~.toasts`).

    Keyed by the server's sequence number, so the same roll fed to two histories in
    this app — a GM window and a sheet — is announced once. A negative ``seq`` is a
    window's own off-air counter and identifies nothing outside it.
    """
    seq = entry.get("seq")
    key = ("seq", seq) if isinstance(seq, int) and seq > 0 else None
    announce(lambda: toast_widgets(entry), key=key, accent=toast_accent(entry), source=source)


def tighten_card(layout: QHBoxLayout, info: QVBoxLayout) -> None:
    """Pack a history card's lines close: the strip shows more rolls at once."""
    small = int(theme.metric("space.sm"))
    layout.setContentsMargins(int(theme.metric("space.md")), small, small, small)
    layout.setSpacing(small)
    info.setSpacing(int(theme.metric("space.xxs")))


class HistoryCard(QFrame):
    """What every card in a history has in common: an id, and a star or not.

    The two histories hold a mix of rolls and notes, and the things done *to* a
    card — striking it by ``seq``, telling it what the quick-roll strip holds —
    have to work whichever it is. So they are declared here and a note simply
    inherits the do-nothing versions.
    """

    def __init__(self, seq: object = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # A session entry has a positive seq; a pre-hosting (offline) roll is given
        # a negative one so it too has a stable id to remove by. Only an entry with
        # no id at all cannot be struck.
        self.seq = seq if isinstance(seq, int) else None

    def set_save_state(self, saved_keys: set, room: bool) -> None:
        """No-op unless the card carries a star (see :class:`SessionRollCard`)."""


class NoteCard(HistoryCard):
    """A line in the history that nobody rolled: "spent a hero point — 2 left".

    Deliberately plain beside a roll card — no headline number, no star, no chain.
    A note is context for the rolls around it, so it reads as an aside rather than
    competing with them for the eye.

    The GM can strike one exactly like a roll (``can_remove``): it took a sequence
    number from the same counter, which is the whole reason a note is a record in
    the log rather than a message beside it.
    """

    removeRequested = Signal(int)

    def __init__(
        self,
        note: dict,
        *,
        show_author: bool = True,
        can_remove: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(note.get("seq"), parent)

        layout = QHBoxLayout(self)
        info = QVBoxLayout()
        tighten_card(layout, info)

        if show_author:
            who = str(note.get("player_name", "")) or "Someone"
            name_line = QLabel(f"<b>{escape_rich_text(who)}</b>")
            name_line.setTextFormat(Qt.TextFormat.RichText)
            name_line.setWordWrap(True)
            info.addWidget(name_line)

        # Plain text explicitly: a QLabel defaults to AutoText and will render this
        # as HTML the moment it looks like markup. The sentence comes off the wire
        # from another player, and every other peer-supplied string in this file is
        # escaped for exactly that reason.
        text = QLabel(str(note.get("text", "")))
        text.setTextFormat(Qt.TextFormat.PlainText)
        text.setWordWrap(True)
        text.setStyleSheet(muted_style(italic=True))
        info.addWidget(text)

        layout.addLayout(info, stretch=1)

        if can_remove and self.seq is not None:
            remove_button = QPushButton("✕")
            remove_button.setToolTip("Remove this note from the history")
            remove_button.setFixedWidth(int(theme.metric("column.roll-button")))
            remove_button.clicked.connect(lambda: self.removeRequested.emit(self.seq))
            layout.addWidget(remove_button, alignment=Qt.AlignmentFlag.AlignVCenter)


class RequestCard(HistoryCard):
    """A roll somebody asked for: who asked, for what, and a button to answer it.

    The third kind of entry, and it reads as a note that can be acted on — an
    author line and a muted sentence, then the button. It is not a
    :class:`SessionRollCard` because nothing was rolled: there is no number to
    headline, no degree, and no parameters worth saving as a quick roll (the
    button loads the trait, which is the better version of that).

    The ``✕`` is the GM's in the shared log, exactly as it is on a note: a request
    took a sequence number from the same counter, which is the whole reason it is a
    record rather than a message beside it — and a call the table has answered is
    worth striking. In the *private* history it is everyone's, since every card
    there is one's own and each already carries a way to be thrown away.
    """

    removeRequested = Signal(int)
    #: This card's button was pressed — roll this spec here, on this sheet.
    rollRequested = Signal(object)

    def __init__(
        self,
        request: dict,
        *,
        show_author: bool = True,
        can_remove: bool = False,
        own_names: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(request.get("seq"), parent)

        layout = QHBoxLayout(self)
        info = QVBoxLayout()
        tighten_card(layout, info)

        # "Requested by X" rather than "X asks for a roll", because the subject is
        # not always a third party: off the air the asker is the reader, and the
        # private history names them "you" — which the second phrasing cannot take
        # without disagreeing with its own verb.
        if show_author:
            who = str(request.get("player_name", "")) or "Someone"
            name_line = QLabel(f"Requested by <b>{escape_rich_text(who)}</b>")
            name_line.setTextFormat(Qt.TextFormat.RichText)
            name_line.setWordWrap(True)
            info.addWidget(name_line)

        # Who the roll is for — the players an area caught, the one a GM asked. Anyone
        # may still answer it (an ally interposing), but the ones it names should not
        # have to hunt for it.
        spec = RollSpec.from_dict(request.get("spec"))
        self.for_reader = False
        if spec is not None and spec.targets:
            self.for_reader = bool(own_names & set(spec.targets))
            names = escape_rich_text(", ".join(spec.targets))
            caught = QLabel(f"<b>For you</b> — {names}" if self.for_reader else f"For: {names}")
            caught.setTextFormat(Qt.TextFormat.RichText)
            caught.setWordWrap(True)
            caught.setStyleSheet(
                tinted_style("tint.warning") if self.for_reader else muted_style(italic=True)
            )
            info.addWidget(caught)

        for widget in request_widgets(request.get("spec"), on_roll=self.rollRequested.emit):
            info.addWidget(widget)

        layout.addLayout(info, stretch=1)

        if can_remove and self.seq is not None:
            remove_button = QPushButton("✕")
            remove_button.setToolTip("Remove this request from the history")
            remove_button.setFixedWidth(int(theme.metric("column.roll-button")))
            remove_button.clicked.connect(lambda: self.removeRequested.emit(self.seq))
            layout.addWidget(remove_button, alignment=Qt.AlignmentFlag.AlignVCenter)


class SessionRollCard(HistoryCard):
    """One roll in the shared history: who rolled it, what it was, how it went.

    Deliberately not :class:`~mm_companion.ui.dice_roller.RollCard`: that one is
    the local roller's own entry and can be thrown away, while this is a line in a
    log everyone shares. Its parameters can be saved for reuse (for a roll of one's
    own), and the GM — and only the GM — can strike it from everyone's log with the
    ``✕`` button (``can_remove``), and show one of their hidden rolls to the table
    with the **Show** button beside it.
    """

    #: This card's star was clicked — save these parameters as a quick roll, or, if
    #: they already are one, take them back out. The roller owns that decision (it
    #: owns the strip), so the card only reports the click and its parameters.
    saveToggled = Signal(dict)
    removeRequested = Signal(int)
    #: The GM asked to show this hidden roll to the table, by its ``seq``.
    revealRequested = Signal(int)

    #: A chain button on this card was pressed — roll the spec it carries. Only ever
    #: on one's own card, since a follow-up is read off local game data (see
    #: :func:`chain_widgets`).
    rollFollowUp = Signal(object)

    def __init__(
        self,
        roll: dict,
        *,
        own: bool = False,
        can_remove: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(roll.get("seq"), parent)
        self.roll = dict(roll)

        die = int(roll.get("die", 0))
        degree = roll.get("degree")

        layout = QHBoxLayout(self)
        info = QVBoxLayout()
        tighten_card(layout, info)

        info.addWidget(_rich(roll_heading(roll)))
        info.addWidget(_rich(roll_headline(roll)))

        # The chain — on every card, whoever rolled it. A save an attack forced is
        # made by the *other* side of the table, so the button has to be on the
        # reader's screen, not the roller's.
        for widget in chain_widgets(
            roll.get("spec"),
            die=die,
            degree=None if degree is None else int(degree),
            total=die + int(roll.get("bonus", 0)) - int(roll.get("penalty", 0)),
            on_follow_up=self.rollFollowUp.emit,
            localize=not own,
        ):
            info.addWidget(widget)

        layout.addLayout(info, stretch=1)

        # Only for a roll of one's own: saving someone else's modifiers into your
        # quick rolls would be saving their character's numbers.
        self.star: QuickRollStar | None = None
        if own:
            self.star = QuickRollStar()
            self.star.clicked.connect(lambda: self.saveToggled.emit(roll_parameters(self.roll)))
            layout.addWidget(self.star, alignment=Qt.AlignmentFlag.AlignVCenter)

        # GM only, and only for a session roll: a pre-hosting roll (negative seq) was
        # never in a log the table could be shown.
        self.reveal_button: QPushButton | None = None
        if can_remove and roll.get("hidden") and self.seq is not None and self.seq > 0:
            self.reveal_button = QPushButton("Show")
            self.reveal_button.setToolTip("Show this hidden roll to the table")
            self.reveal_button.clicked.connect(lambda: self.revealRequested.emit(self.seq))
            layout.addWidget(self.reveal_button, alignment=Qt.AlignmentFlag.AlignVCenter)

        if can_remove and self.seq is not None:
            # GM only — strikes the roll from the log (from every screen, in a session).
            remove_button = QPushButton("✕")
            remove_button.setToolTip("Remove this roll from the history")
            remove_button.setFixedWidth(int(theme.metric("column.roll-button")))
            remove_button.clicked.connect(lambda: self.removeRequested.emit(self.seq))
            layout.addWidget(remove_button, alignment=Qt.AlignmentFlag.AlignVCenter)

    def parameters(self) -> dict:
        """The quick-roll parameters this card would save."""
        return roll_parameters(self.roll)

    def set_save_state(self, saved_keys: set, room: bool) -> None:
        """Light this card's star if its roll is among *saved_keys* (a no-op without one)."""
        if self.star is None:
            return
        key = quick_roll_key(self.parameters())
        self.star.set_state(key in saved_keys, room)


def table_key(group: str) -> str:
    """Which table card a group roll belongs to — the group id less any stage suffix.

    An action resolved in stages rolls each stage as its own group (an area's Dodge
    checks, then the saves behind them), named ``"<id>.1"``, ``"<id>.2"``. They are one
    table: the second stage fills in the rows the first one started.
    """
    head, dot, tail = group.rpartition(".")
    return head if dot and head and tail.isdigit() else group


def _table_cell(roll: dict, row: str) -> str:
    """One roll in a table row, as rich text: what it was, the total, and the DC."""
    label = str(roll.get("label", ""))
    if row and label.startswith(f"{row}: "):
        label = label[len(row) + 2 :]
    die = int(roll.get("die", 0))
    total = die + int(roll.get("bonus", 0)) - int(roll.get("penalty", 0))
    text = f"{escape_rich_text(label)} <b>{total}</b>"
    dc = roll.get("dc")
    if dc is not None:
        text += f" vs {int(dc)}"
    degree = roll.get("degree")
    if degree is not None:
        degree = int(degree)
        mark = "✓" if degree > 0 else "✗" * max(1, min(3, abs(degree)))
        colour = theme.color("tint.better" if degree > 0 else "tint.worse")
        text += f" <span style='color:{colour}'>{mark}</span>"
    if die in (1, 20):
        muted = theme.color("text.muted.rich")
        text += f" <span style='color:{muted}'>(nat {die})</span>"
    return text


def _provokes(roll: dict) -> bool:
    """Whether a roll carries a follow-up — which only its own card can offer.

    An impression run as "request only" is a group of one: its check, whose card is
    where every target finds the button to resist with. A table has no buttons, so a
    roll like that keeps its ordinary card.
    """
    spec = RollSpec.from_dict(roll.get("spec"))
    return spec is not None and spec.follow_up is not None


def action_table_key(roll: dict) -> str:
    """The table a roll belongs to: its group's, or the action its spec names.

    A group's rolls are the server's (:func:`table_key`). A roll a *player* made for an
    action — their Dodge against an area, the impress check the GM asked them for, an
    area's attack — is an ordinary roll, but its spec carries the action's id and a
    title, and the action's groups are named after that same id. So it joins the table.
    """
    group = str(roll.get("group", "") or "")
    if group:
        return table_key(group)
    spec = RollSpec.from_dict(roll.get("spec"))
    if spec is not None and spec.action_id and spec.title:
        return spec.action_id
    return ""


def _row_name(roll: dict) -> str:
    """The creature a group roll concerns — the first name its spec targets, or none."""
    spec = RollSpec.from_dict(roll.get("spec"))
    return spec.targets[0] if spec is not None and spec.targets else ""


class GroupTableCard(HistoryCard):
    """Every roll of one group — an impression, an area — as **one** table.

    A group used to land as a card per creature, which buried the one thing the table
    wanted to read (who was caught, and what it did to them) under a dozen near-identical
    cards. Here each creature is a row — the name a line's spec names in its
    :attr:`~mm_companion.core.rules.RollSpec.targets` — and every roll that concerns it
    is a cell of that row, so an area's Dodge check and the save after it read side by
    side, with what the save did at the end. A roll naming nobody (an impression's own
    check) is a row of its own.

    Rolls join it as they arrive, kept in ``seq`` order, which is what lets a second
    stage fill in rows the first one started. :attr:`seqs` is every roll it holds, so
    striking one by ``seq`` finds its table.
    """

    removeRequested = Signal(int)

    def __init__(
        self, key: str, *, can_remove: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(None, parent)
        self.key = key
        self.records: list[dict] = []
        layout = QHBoxLayout(self)
        self._info = QVBoxLayout()
        tighten_card(layout, self._info)
        layout.addLayout(self._info, stretch=1)
        self._lines: list[QLabel] = []
        if can_remove:
            remove_button = QPushButton("✕")
            remove_button.setToolTip("Remove this whole table from the history")
            remove_button.setFixedWidth(int(theme.metric("column.roll-button")))
            remove_button.clicked.connect(self._remove_all)
            layout.addWidget(remove_button, alignment=Qt.AlignmentFlag.AlignTop)

    @property
    def seqs(self) -> set[int]:
        return {r["seq"] for r in self.records if isinstance(r.get("seq"), int)}

    def add_record(self, roll: dict) -> None:
        """Take one more roll of the group (or a fresher copy of one), and redraw."""
        seq = roll.get("seq")
        if isinstance(seq, int):
            self.records = [r for r in self.records if r.get("seq") != seq]
        self.records.append(dict(roll))
        # In the order they were rolled. Off the air a window counts its own rolls
        # *down* from -1, so it is the magnitude that grows with time either way.
        self.records.sort(key=lambda r: abs(r["seq"]) if isinstance(r.get("seq"), int) else 0)
        seqs = self.seqs
        self.seq = min(seqs) if seqs else None
        self._redraw()

    def remove_seq(self, seq: int) -> bool:
        """Drop the roll *seq*; ``True`` when the table has nothing left in it."""
        self.records = [r for r in self.records if r.get("seq") != seq]
        self._redraw()
        return not self.records

    def _remove_all(self) -> None:
        for seq in sorted(self.seqs):
            self.removeRequested.emit(seq)

    def rows(self) -> list[tuple[str, list[dict]]]:
        """``(row name, its rolls)`` in the order the rows first appeared."""
        rows: list[tuple[str, list[dict]]] = []
        named: dict[str, list[dict]] = {}
        for roll in self.records:
            name = _row_name(roll)
            if not name:
                rows.append(("", [roll]))
            elif name in named:
                named[name].append(roll)
            else:
                named[name] = [roll]
                rows.append((name, named[name]))
        return rows

    def title(self) -> str:
        for roll in self.records:
            spec = RollSpec.from_dict(roll.get("spec"))
            if spec is not None and spec.title:
                return spec.title
        return ""

    def _redraw(self) -> None:
        for line in self._lines:
            self._info.removeWidget(line)
            discard_widget(line)
        self._lines = []
        if not self.records:
            return
        who = str(self.records[0].get("player_name", "")) or "Someone"
        heading = f"<b>{escape_rich_text(who)}</b>"
        if any(r.get("hidden") for r in self.records):
            heading = f"{HIDDEN_MARK} {heading}"
        title = self.title()
        if title:
            muted = theme.color("text.muted.rich")
            heading += f" <span style='color:{muted}'>— {escape_rich_text(title)}</span>"
        self._add_line(heading)
        for name, rolls in self.rows():
            cells = " · ".join(_table_cell(r, name) for r in rolls)
            text = f"<b>{escape_rich_text(name)}</b> {cells}" if name else cells
            last = rolls[-1]
            spec = RollSpec.from_dict(last.get("spec"))
            degree = last.get("degree")
            degree = None if degree is None else int(degree)
            outcome = resistance_outcome(spec, degree) if spec is not None else ""
            if outcome:
                tint = theme.color("tint.worse" if outcome_is_failure(degree) else "tint.warning")
                text += f" → <span style='color:{tint}'><b>{escape_rich_text(outcome)}</b></span>"
            self._add_line(text)

    def _add_line(self, text: str) -> None:
        line = _rich(text)
        self._info.addWidget(line)
        self._lines.append(line)

    def lines(self) -> list[str]:
        """The table's rows as plain text, heading first — what a test reads."""
        return [line.text() for line in self._lines]


class ActionCard(HistoryCard):
    """A line in the **GM's** history that waits on them: an action to resolve.

    Never on the wire — it is the GM's own reminder, built by the controller that
    owns the action and shown only in the GM window's history, where the rolls it
    is about land too. Its one button reopens the action's window; once resolved
    it says so and the button goes.

    While it waits it is :attr:`pending`, and the panel **keeps** a pending card through
    a replay (a reconnect's ``Welcome``) and past :data:`MAX_CARDS`: it is the only way
    back to the action, and losing it would leave an attack nobody can resolve.
    """

    actionRequested = Signal(str)

    def __init__(
        self, action_id: str, text: str, button: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(None, parent)
        self.action_id = action_id
        layout = QHBoxLayout(self)
        info = QVBoxLayout()
        tighten_card(layout, info)
        self._text = _rich(text)
        info.addWidget(self._text)
        self.button = QPushButton(button)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(lambda: self.actionRequested.emit(self.action_id))
        info.addWidget(self.button)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setStyleSheet(muted_style(italic=True))
        info.addWidget(self._status)
        self._status.hide()
        layout.addLayout(info, stretch=1)
        self.pending = True

    def text(self) -> str:
        return self._text.text()

    def set_text(self, text: str) -> None:
        self._text.setText(text)

    def set_status(self, text: str, *, button: str = "") -> None:
        """Say where the action stands while it still waits — who is still to roll.

        *button* renames the button to what it does now ("Open" once it is rolling).
        """
        if button:
            self.button.setText(button)
        self._status.setText(text)
        self._status.setVisible(bool(text))

    def status(self) -> str:
        return self._status.text()

    def set_done(self, text: str) -> None:
        """The action is settled: say how, and take the button away."""
        self.pending = False
        self.button.hide()
        self._status.setText(text)
        self._status.show()


class RollHistoryPanel(QWidget):
    """The shared roll log, newest first, fed by a :class:`SessionBridge`."""

    #: A card's star — the roll's parameters, for the roller to save or unsave.
    saveToggled = Signal(dict)
    #: A roll the GM struck with no live session to remove it from — so an owner
    #: (the GM window) can drop it from a persisted, off-air session too.
    rollRemovedLocally = Signal(int)
    #: A hidden roll the GM showed with no live session to show it to — so the GM
    #: window can mark it visible in the persisted, off-air session.
    rollRevealedLocally = Signal(int)

    #: A card's follow-up button was pressed — roll this spec (see :func:`chain_widgets`).
    rollFollowUp = Signal(object)

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
        # ...but only while that roller actually has a die in the air
        # (see :meth:`set_awaiting_own`). The two are different questions — "is
        # there a roller to wait for" and "is it waiting right now" — and holding a
        # roll when the answer to the second is no is how one's own rolls used to
        # vanish for good.
        self._awaiting_own = False
        self._held: dict[int, dict] = {}
        # True while a whole log is being laid in (a join, a reconnect, the GM's log
        # off disk): none of that is news, so none of it pops up as a notification.
        self._replaying = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.addStretch()

        # The empty state belongs *inside* the list, not above it. Above it, this
        # word-wrapped line added its two rows to the panel's height — and since the
        # Dice block's minimum is its size hint (``BlockFrame.minimumSizeHint``, with
        # no ``max_height`` to cap ``dice`` against), the block grew by exactly that
        # much the moment a session attached, which is when this label shows. On a
        # scaled display that was the difference between the pinned strip fitting the
        # screen and not. Inside the container it occupies room the history already
        # has, so the private and the shared history are the same height and joining
        # a table costs nothing.
        #
        # It goes in at the top and sinks as cards are inserted above it, which is
        # invisible — it is only ever shown when there are no cards at all. What it
        # is *not* invisible to is :meth:`_trim`, which counts cards for that reason.
        self._empty = QLabel(EMPTY_TEXT)
        self._empty.setWordWrap(True)
        self._empty.setEnabled(False)
        self._layout.insertWidget(0, self._empty)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._container)
        size_history_scroll(self._scroll)
        layout.addWidget(self._scroll)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Two cards' worth, however long the log is — see :func:`size_history_scroll`."""
        return HISTORY_SIZE_HINT

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

        This says only that there *is* a roller to wait for.
        :meth:`set_awaiting_own` says whether it is waiting, and both have to be
        true before anything is held.
        """
        self._defer_own = defer

    def set_awaiting_own(self, awaiting: bool) -> None:
        """Whether the paired roller has a roll of ours in flight right now.

        Driven by that panel's ``awaitingOwnRoll``. Turning it **off** flushes
        whatever is still held, and that is the point of the method rather than a
        tidy-up: a held roll is only ever shown again by :meth:`release_roll`, so an
        own roll that reaches :meth:`add_roll` outside a tumble had nothing to
        release it and stayed invisible for the rest of the session — to its own
        roller alone, while every other seat saw it. Two ways that happened, and
        both are ordinary play:

        * a **reconnect** re-sends the whole log (``Welcome`` → ``historyReplaced``
          → :meth:`set_rolls`), so every one of that player's own rolls was deferred
          again at once and their history lost all of them;
        * a roll the roller **gave up on** after ``SESSION_ROLL_TIMEOUT_MS``, whose
          record then arrived a moment later on this panel's own feed.
        """
        awaiting = bool(awaiting)
        if awaiting == self._awaiting_own:
            return
        self._awaiting_own = awaiting
        if not awaiting:
            self.release_held()

    def release_held(self) -> None:
        """Show every roll still being held, oldest first.

        In ``seq`` order rather than arrival order: the log reads newest-first and
        the cards are inserted at the top, so flushing them out of a dict's
        insertion order would put them on screen backwards.
        """
        held, self._held = self._held, {}
        for seq in sorted(held):
            self._insert(held[seq])

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
        bridge.rollRevealed.connect(self.reveal_roll)
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
            (bridge.rollRevealed, self.reveal_roll),
            (bridge.historyReplaced, self.set_rolls),
        )
        for signal, slot in pairs:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    # -- rendering ---------------------------------------------------------

    def set_rolls(self, rolls: object) -> None:
        """Replace everything on screen with *rolls* (oldest first, as stored).

        A replay, not a feed: this is what a reconnect's fresh ``Welcome`` lands
        on. Which seat is ours is re-read from the bridge first, because
        :meth:`attach` runs once per join and a redial that failed to reclaim the
        old slot comes back under a new ``player_id`` — after which every one of
        our own rolls would be drawn as somebody else's.
        """
        if self._bridge is not None:
            self._own_id = self._bridge.own_player_id()
        self.clear()
        if isinstance(rolls, list):
            self._replaying = True
            try:
                for roll in rolls[-MAX_CARDS:]:
                    self.add_roll(roll)
            finally:
                self._replaying = False
        # What clear() kept back — an action still waiting on the GM — goes back on top,
        # where it was before the replay buried it under the log.
        for card in reversed(self._waiting()):
            self._layout.removeWidget(card)
            self._layout.insertWidget(0, card)

    def add_roll(self, roll: object) -> None:
        """Put one entry at the top of the list.

        One's own roll is held (not shown) while a paired roller has a die in the
        air, so it can release it as the die settles — see :meth:`set_defer_own`,
        :meth:`set_awaiting_own` and :meth:`release_roll`. Outside that window
        nothing is held at all, because nothing would ever let it go again.
        A note or a request of one's own is *not* held either: deferral waits on a
        die animation, and neither has one to wait for, so holding either would
        hold it forever. Hence the test is "is this a die roll", not "is this not
        a note".
        """
        if not isinstance(roll, dict):
            return
        seq = roll.get("seq")
        if isinstance(seq, int) and seq in self._seen:
            return
        if (
            self._defer_own
            and self._awaiting_own
            and roll.get("kind", KIND_ROLL) == KIND_ROLL
            and self._is_own(roll)
        ):
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

    def _own_names(self) -> frozenset[str]:
        """The names this seat goes by on the scene — what a request's targets name."""
        if self._bridge is None or not self._own_id:
            return frozenset()
        return frozenset(
            str(entry.get("name", ""))
            for entry in self._bridge.scene()
            if entry.get("player_id") == self._own_id and entry.get("name")
        )

    def _is_own(self, roll: dict) -> bool:
        return bool(self._own_id) and str(roll.get("player_id", "")) == self._own_id

    def _insert(self, roll: dict) -> None:
        """Build and place a card for *roll*, deduplicating by ``seq``."""
        seq = roll.get("seq")
        if isinstance(seq, int):
            if seq in self._seen:
                return
            self._seen.add(seq)

        key = action_table_key(roll) if roll.get("kind", KIND_ROLL) == KIND_ROLL else ""
        if key and not _provokes(roll):
            self._add_to_table(roll, key)
            return

        card: HistoryCard
        if roll.get("kind") in (KIND_NOTE, KIND_MOD):
            # A mod's line is drawn as a note, and that is the point rather than a
            # shortcut. The two ends of a table can load different mods, so this
            # card has to read sensibly for a mod this app has never heard of —
            # and a plain sentence attributed to the seat that sent it is exactly
            # what a note already is. A mod that *is* installed can dress its own
            # line up later; nothing here has to know which.
            card = NoteCard(roll, can_remove=self._gm)
            card.removeRequested.connect(self._request_remove)
        elif roll.get("kind") == KIND_REQUEST:
            card = RequestCard(roll, can_remove=self._gm, own_names=self._own_names())
            card.removeRequested.connect(self._request_remove)
            # The same signal the follow-up chip travels on, and for the same
            # reason: both are "roll this on your own sheet", and the roller
            # already answers it (localizer and all).
            card.rollRequested.connect(self.rollFollowUp)
        else:
            card = self._roll_card(roll)
        # Newest on top: insert above every existing card (the stretch is last).
        self._layout.insertWidget(0, card)
        self._trim()
        self._empty.setVisible(False)
        if not self._replaying:
            announce_entry(roll, self)

    def _add_to_table(self, roll: dict, key: str) -> None:
        """Put a group's roll in its table card — a new one for the group's first roll.

        Only the first roll of a table is announced: the rest are the same news, a row
        at a time.
        """
        table = next(
            (c for c in self.cards() if isinstance(c, GroupTableCard) and c.key == key), None
        )
        if table is not None:
            table.add_record(roll)
            return
        table = GroupTableCard(key, can_remove=self._gm)
        table.removeRequested.connect(self._request_remove)
        table.add_record(roll)
        self._layout.insertWidget(0, table)
        self._trim()
        self._empty.setVisible(False)
        if not self._replaying:
            announce_entry(roll, self)

    def add_card(self, card: HistoryCard) -> None:
        """Put a card the owner built at the top — a GM's :class:`ActionCard`.

        Not a record: nothing is added to the log and nothing of it is saved. A replay
        keeps it rather than bringing it back (:meth:`clear`), for as long as it waits.
        """
        self._layout.insertWidget(0, card)
        self._trim()
        self._empty.setVisible(False)

    def _roll_card(self, roll: dict) -> SessionRollCard:
        card = SessionRollCard(roll, own=self._is_own(roll), can_remove=self._gm)
        card.set_save_state(self._saved_keys, self._quick_room)
        card.saveToggled.connect(self.saveToggled)
        card.removeRequested.connect(self._request_remove)
        card.revealRequested.connect(self._request_reveal)
        card.rollFollowUp.connect(self.rollFollowUp)
        return card

    def _request_reveal(self, seq: int) -> None:
        """A hidden roll's Show was clicked — show it to the table (GM action).

        The mirror of :meth:`_request_remove`: in a session the server flips it and
        echoes :attr:`~SessionBridge.rollRevealed` to every screen, this one
        included. Off the air the card is redrawn here and
        :attr:`rollRevealedLocally` lets the owner persist it.
        """
        if self._bridge is not None and self._bridge.in_session:
            # In a session only the server's answer redraws the card: a request that
            # failed to send must not show the GM a roll the table never saw.
            self._bridge.reveal_roll(seq)
            return
        card = next((c for c in self.cards() if c.seq == seq), None)
        if isinstance(card, SessionRollCard):
            self.reveal_roll({**card.roll, "hidden": False})
            self.rollRevealedLocally.emit(seq)

    def reveal_roll(self, roll: object) -> None:
        """A hidden roll has been shown: redraw its card in place, or add it.

        The GM's screen already has the card, marked hidden, and swaps it for a
        visible one where it stands. A player's screen has never seen the roll, so
        it simply arrives — at the top, since that is when the table learned of it.
        """
        if not isinstance(roll, dict):
            return
        seq = roll.get("seq")
        table = next(
            (c for c in self.cards() if isinstance(c, GroupTableCard) and seq in c.seqs), None
        )
        if table is not None:
            table.add_record(roll)
            return
        old = next((c for c in self.cards() if seq is not None and c.seq == seq), None)
        if old is None:
            self.add_roll(roll)
            return
        index = self._layout.indexOf(old)
        self._layout.removeWidget(old)
        discard_widget(old)
        self._layout.insertWidget(index, self._roll_card(roll))

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
        """Drop the card for *seq* (from a ``rollRemoved`` broadcast); no-op if absent.

        A roll in a table takes only its cell with it; the table goes once it is empty.
        """
        for card in self.cards():
            if isinstance(card, GroupTableCard) and seq in card.seqs:
                if card.remove_seq(seq):
                    self._layout.removeWidget(card)
                    discard_widget(card)
                break
            if card.seq == seq:
                self._layout.removeWidget(card)
                discard_widget(card)
                break
        self._seen.discard(seq)
        if not self.cards():
            self._empty.setVisible(True)

    def clear(self) -> None:
        """Drop every card — except an :class:`ActionCard` still waiting on the GM.

        That one is no record, so nothing would bring it back, and it is the only way
        to the action it stands for.
        """
        waiting = self._waiting()
        for card in self.cards():
            if card in waiting:
                continue
            self._layout.removeWidget(card)
            discard_widget(card)
        self._seen.clear()
        self._held.clear()
        self._empty.setVisible(not waiting)

    def _waiting(self) -> list[HistoryCard]:
        """The action cards on screen still waiting on the GM, newest first."""
        return [c for c in self.cards() if isinstance(c, ActionCard) and c.pending]

    def cards(self) -> list[HistoryCard]:
        """The cards on screen, newest first — rolls and notes alike.

        Read off the layout rather than ``findChildren``, which answers in
        construction order — the opposite end of the list from where a new card
        goes.
        """
        found = []
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, HistoryCard):
                found.append(widget)
        return found

    def _trim(self) -> None:
        """Drop the oldest cards once the list is longer than :data:`MAX_CARDS`.

        Counted in *cards* rather than layout items, because the layout also holds
        the empty-state label and the trailing stretch — and the label sinks to the
        bottom of the stack as cards are inserted above it, which is exactly where a
        positional "the oldest one is second from the end" rule would have found it.
        """
        # A waiting action card is never the one to go — see ActionCard.
        cards = [c for c in self.cards() if not (isinstance(c, ActionCard) and c.pending)]
        for card in cards[MAX_CARDS:]:
            self._layout.removeWidget(card)
            discard_widget(card)


def escape_rich_text(text: str) -> str:
    """Escape a player-supplied string for a rich-text label.

    Display names and roll labels come off the wire, and these labels render
    HTML — an unescaped ``<`` would let a peer restyle someone else's history.
    Public because the private history's cards (:mod:`mm_companion.ui.dice_roller`)
    render the same strings and must escape them the same way.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
