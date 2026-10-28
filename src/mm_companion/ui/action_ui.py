"""What the two actions against other characters share: Impress and area attacks.

Both run the same way at a table, so they are built from the same pieces:

* **The table's rule** (:func:`players_may_choose`, :func:`sets_up_own_actions`). A GM
  decides whether players pick their own targets, and each player whether they want
  to. The GM's half is published to every seat as the core app's own entry on the
  session's shared-state channel (:data:`TABLE_MOD_ID`), so a player reads it off the
  table rather than asking; a player whose answer is no sends the action **bare** and
  the GM sets it up.
* **The target picker** a player's dialog takes scene cards into
  (:class:`TargetDropField`, :class:`TargetsBox`), and the drop box the GM's window
  takes them into (:class:`ActionDropBox`).
* **The GM's window** (:class:`ActionWindow`): who is involved and what each rolls,
  in a table of rows, set up, then rolled — and after that the same window is where
  each roll comes back and what it did is shown, until everything is in.
* **The roll the GM asks a player for** (:class:`RollPromptDialog`), which pops up on
  that player's screen while the same request waits in everyone's history.
* :func:`fit_to_screen`, because a window listing a whole scene is the one that
  outgrows the screen first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    BOOK_MINION_RULES,
    KIND_BEST_OF,
    MinionRules,
    RollSpec,
    best_own_rolls,
    localize_spec,
)
from mm_companion.ui import theme
from mm_companion.ui.card_chips import SCENE_MIME
from mm_companion.ui.card_drop import CardDropFlow
from mm_companion.ui.drop_feedback import DropFeedback
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import (
    HelpButton,
    make_signed_spin_box,
    muted_style,
    tinted_style,
)

#: The ``ActionPrompt`` kinds the GM sends one player while an action runs: roll this
#: (their check, their Dodge, their save), that roll is no longer wanted, and what the
#: action did to you.
KIND_ROLL_ASK = "roll"
KIND_ROLL_CLOSED = "roll-closed"
KIND_OUTCOME = "outcome"

#: The core app's own entry on the session's shared-state channel: the table's rules
#: for actions, which the GM publishes and every seat reads.
TABLE_MOD_ID = "mm-companion.table"
TABLE_ACTIONS_KEY = "actions"
#: The session's own settings, which the GM sets on the Session settings page and
#: which belong to this session alone: stored with it on the server, replayed to every
#: seat, and forgotten when it is (see :func:`session_minion_rules`).
TABLE_SESSION_KEY = "session"


def signed(value: int) -> str:
    return f"{value:+d}"


# -- the table's rule -------------------------------------------------------------------


def players_may_choose(bridge) -> bool:
    """Whether the GM at this table lets players set up their own actions.

    Read off the table, where the GM published it; a table that never said (an older
    GM's app) lets them, which is how it always was.
    """
    try:
        entry = bridge.mod_state(TABLE_MOD_ID).get(TABLE_ACTIONS_KEY, {})
    except AttributeError:
        return True
    value = entry.get("players_choose") if isinstance(entry, dict) else None
    return value if isinstance(value, bool) else True


def publish_table_rules(bridge) -> bool:
    """Tell the table what this GM's setting is (see :func:`players_may_choose`)."""
    return bool(
        bridge.set_mod_state(
            TABLE_MOD_ID,
            TABLE_ACTIONS_KEY,
            {"players_choose": storage.gm_players_choose_actions()},
        )
    )


def session_minion_rules(bridge) -> MinionRules:
    """Which minion rules the open session plays by — the book's when it never said.

    Read off the session's shared state, where the GM's Session settings page
    published them. They are the *session's*, not the GM's: a new session starts from
    the book (every rule on), and with no session open there is nothing to read, so
    the book's rules apply then too.
    """
    # In a session, blips included; a hosting server that has stopped still holds the
    # state it had, and reading that would carry one session's rules past its end.
    if bridge is None or not getattr(bridge, "in_session", False):
        return BOOK_MINION_RULES
    try:
        entry = bridge.mod_state(TABLE_MOD_ID).get(TABLE_SESSION_KEY, {})
    except AttributeError:
        return BOOK_MINION_RULES
    raw = entry.get("minions") if isinstance(entry, dict) else None
    return MinionRules.from_dict(raw)


def publish_minion_rules(bridge, rules: MinionRules) -> bool:
    """Store *rules* as the open session's (the GM's Session settings page's Save).

    The rest of the session's settings entry is kept as it is, so a later setting
    added beside the minion rules is not wiped by saving these.
    """
    if bridge is None:
        return False
    try:
        current = bridge.mod_state(TABLE_MOD_ID).get(TABLE_SESSION_KEY, {})
    except AttributeError:
        current = {}
    entry = dict(current) if isinstance(current, dict) else {}
    entry["minions"] = rules.to_dict()
    return bool(bridge.set_mod_state(TABLE_MOD_ID, TABLE_SESSION_KEY, entry))


def sets_up_own_actions(bridge) -> bool:
    """Whether this seat picks the targets of its own Impress or area attack.

    Off the air there is nobody else to, so always. The GM never does from a sheet:
    their own window is where the GM sets an action up. A player does when both they
    and the table say so; otherwise the action goes to the GM bare.
    """
    if bridge is None:
        return True
    if bridge.is_gm:
        return False
    return storage.resolve_actions_myself() and players_may_choose(bridge)


# -- sizing -------------------------------------------------------------------------------


class BodyScroll(QScrollArea):
    """A scroll area that asks for the whole of what it holds.

    A plain one caps its hint at a few dozen lines, so a window built around one opens
    small and scrolls when the screen had room for all of it. This asks for everything;
    :func:`fit_to_screen` is what trims that to the screen, and only then does it scroll.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        inner = self.widget()
        if inner is None:
            return super().sizeHint()
        hint = inner.sizeHint()
        bar = self.verticalScrollBar().sizeHint().width()
        return QSize(hint.width() + bar, hint.height())


#: How wide an action's dialog opens, at least, in average characters of its font —
#: so a line of numbers is not wrapped into three in a dialog sized to its widest combo.
DIALOG_CHARS = 52


def fit_to_screen(window: QWidget, *, share: float = 0.85, min_chars: int = 0) -> None:
    """Size *window* to what it asks for, but never past *share* of its screen.

    Width and height alike, and moved back onto the screen if that left it hanging off
    an edge. Everything inside is in a :class:`BodyScroll`, so what does not fit scrolls.
    *min_chars* opens it at least that many average characters wide — the size it
    opens at, which the user may still drag narrower.
    """
    screen = window.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        return
    room = screen.availableGeometry()
    # Measured on first show, before Qt has laid anything out, and straight after a part
    # of the body was shown or hidden, before the layout has heard of it: invalidate and
    # activate the layouts first, or the scrolled body's hint is the stale one — from
    # before its rows existed, or from before a section folded away. ``activate`` alone
    # does nothing to a layout Qt has not yet marked dirty, which is the second case: a
    # folded section left the window at its unfolded height.
    for scroll in window.findChildren(BodyScroll):
        inner = scroll.widget()
        if inner is not None and inner.layout() is not None:
            inner.layout().invalidate()
            inner.layout().activate()
        scroll.updateGeometry()
    if window.layout() is not None:
        window.layout().invalidate()
        window.layout().activate()
    hint = window.sizeHint()
    floor = window.fontMetrics().averageCharWidth() * min_chars
    width = min(max(hint.width(), window.width(), floor), int(room.width() * share))
    height = min(hint.height(), int(room.height() * share))
    window.resize(width, max(height, window.minimumSizeHint().height()))
    frame = window.frameGeometry()
    x = min(max(frame.x(), room.x()), room.right() - frame.width())
    y = min(max(frame.y(), room.y()), room.bottom() - frame.height())
    if (x, y) != (frame.x(), frame.y()):
        window.move(max(room.x(), x), max(room.y(), y))


# -- picking targets ----------------------------------------------------------------------


class TargetDropField(CardDropFlow):
    """A flow of target chips a scene card is dropped into — one, or any number.

    *resolve* turns a dragged reference into ``(ref, name)``, or ``None`` for one this
    window cannot target (the actor's own card); the field never interprets a ref
    itself. A chip's ✕ takes the creature back out.
    """

    changed = Signal()

    def __init__(
        self,
        name: str,
        *,
        resolve: Callable[[str], tuple[str, str] | None],
        single: bool = False,
        placeholder: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(name, parent, accepts=lambda ref: resolve(ref) is not None)
        self._resolve = resolve
        self._single = single
        self._chips: dict[str, QToolButton] = {}
        self.set_placeholder(placeholder)
        self.dropped.connect(lambda ref, _index: self.add_ref(ref))

    def add_ref(self, raw_ref: str) -> bool:
        """Put the creature *raw_ref* names in the field; ``False`` if it cannot go."""
        found = self._resolve(raw_ref)
        if found is None:
            return False
        ref, name = found
        if ref in self._chips:
            return False
        if self._single:
            self.clear_refs(notify=False)
        chip = QToolButton()
        chip.setText(f"{name}  ✕")
        chip.setToolTip(f"Take {name} back out")
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.clicked.connect(lambda _=False, r=ref: self.remove_ref(r))
        self._chips[ref] = chip
        self.add_card(chip)
        self.changed.emit()
        return True

    def remove_ref(self, ref: str, *, notify: bool = True) -> None:
        chip = self._chips.pop(ref, None)
        if chip is None:
            return
        self.set_cards(list(self._chips.values()))
        if notify:
            self.changed.emit()

    def clear_refs(self, *, notify: bool = True) -> None:
        self._chips.clear()
        self.clear()
        if notify:
            self.changed.emit()

    def refs(self) -> list[str]:
        return list(self._chips)


def add_button(
    choices: Callable[[], list[tuple[str, str]]],
    add: Callable[[str], object],
    *,
    everyone: bool = False,
) -> QPushButton:
    """An "Add" button listing the scene, for when the board is not to hand.

    With *everyone* the menu also offers the whole scene at once — a crowd caught in
    one area, or a room of mooks impressed together.
    """
    button = QPushButton("Add")
    button.setToolTip("Pick a creature off the scene — or drag its card in")
    menu = QMenu(button)

    def add_all() -> None:
        for ref, _name in choices():
            add(ref)

    def fill() -> None:
        menu.clear()
        entries = choices()
        if not entries:
            menu.addAction("Nobody else is on the scene").setEnabled(False)
            return
        if everyone and len(entries) > 1:
            menu.addAction("Everyone on the scene", add_all)
            menu.addSeparator()
        for ref, name in entries:
            menu.addAction(name, lambda r=ref: add(r))

    menu.aboutToShow.connect(fill)
    button.setMenu(menu)
    return button


class TargetsBox(QGroupBox):
    """A titled drop field with its Add button — one way of picking, in every dialog."""

    def __init__(
        self,
        title: str,
        name: str,
        *,
        resolve: Callable[[str], tuple[str, str] | None],
        choices: Callable[[], list[tuple[str, str]]],
        single: bool = False,
        placeholder: str = "",
        help_text: str = "",
    ) -> None:
        super().__init__(title)
        layout = QHBoxLayout(self)
        self.field = TargetDropField(name, resolve=resolve, single=single, placeholder=placeholder)
        layout.addWidget(self.field, stretch=1)
        layout.addWidget(
            add_button(choices, self.field.add_ref, everyone=not single),
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        if help_text:
            layout.addWidget(HelpButton(help_text), alignment=Qt.AlignmentFlag.AlignTop)


class ActionDropBox(QGroupBox):
    """A group box a scene card can be dropped on, to put that creature in the action."""

    dropped = Signal(str)

    def __init__(self, title: str, accepts: Callable[[str], bool]) -> None:
        super().__init__(title)
        self._accepts = accepts
        self.setObjectName("actionDropBox")
        self.setAcceptDrops(True)
        self._feedback = DropFeedback(self, "#actionDropBox", border=False)

    def _ref(self, event) -> str:
        if not event.mimeData().hasFormat(SCENE_MIME):
            return ""
        ref = bytes(event.mimeData().data(SCENE_MIME)).decode("utf-8")
        return ref if ref and self._accepts(ref) else ""

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._ref(event):
            event.acceptProposedAction()
            self._feedback.show_accept()
        else:
            self._feedback.show_reject()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._ref(event):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._feedback.clear()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        ref = self._ref(event)
        self._feedback.clear()
        if ref:
            event.acceptProposedAction()
            self.dropped.emit(ref)


# -- the GM's window ------------------------------------------------------------------------


#: How far the GM may move one creature's part in an action, either way.
ADJUST_LIMIT = 20


class ActionRow:
    """One creature's row in an :class:`ActionWindow`: in or out, its numbers, its result.

    *cells* are the action's own columns between the name and the adjustment (what it
    rolls with, its cover); the window lays them out and locks them with the rest. The
    adjustment is one signed box — a bonus is "+2", a penalty "-2".
    """

    def __init__(self, party, cells: list[QWidget]) -> None:
        self.party = party
        self.include = QCheckBox(party.name)
        self.cells = cells
        self.modifier = make_signed_spin_box(
            ADJUST_LIMIT, max_width=int(theme.metric("column.stat.spin"))
        )
        self.result = QLabel()
        self.result.setTextFormat(Qt.TextFormat.RichText)
        self.result.setWordWrap(True)

    def inputs(self) -> list[QWidget]:
        return [self.include, *self.cells, self.modifier]

    def show_result(self, text: str, style: str = "") -> None:
        """Say what this row came to, on its own line — gone again when there is nothing."""
        self.result.setText(text)
        self.result.setStyleSheet(style)
        self.result.setVisible(bool(text))

    @property
    def adjust(self) -> int:
        return self.modifier.value()


#: How a creature is put in an action's table, behind the "?" on it.
BOX_HELP = (
    "Tick a creature to put it in, or drop its card here from the Scene. Adjust is a "
    "bonus (+) or penalty (-) to that creature's own part in the action."
)

#: The phases a GM's window goes through.
PHASE_SETUP = "setup"
PHASE_RUNNING = "running"
PHASE_DONE = "done"


class ActionWindow(QDialog):
    """The GM's window for one action: set it up, roll it, and watch it come back.

    The skeleton both actions share. On top, a heading; under it the action's own
    controls (:meth:`add_section`); then the table — a row per creature on the scene,
    ticked in or dropped in, with the action's own columns, a bonus and a penalty, and
    a **Result** column. Every control scrolls together, so a scene too big for the
    screen still fits.

    **Setup**, then **running**: once rolled, the controls lock and the window stays —
    each roll the server makes or a player answers lands in its row as it comes back,
    and the window says who is still being waited for, with a button to roll for them.
    Closing it then only hides it; the history card opens it again.

    *from_card* is a player's declaration, waiting in the GM's history: it can be closed
    and come back to (**Close**) or thrown away (**Dismiss**). The GM's own is simply
    **Cancel**led.

    How the action works is *help_text*, behind the "?" by the buttons, and how to put a
    creature in is *box_help*, behind the one on the table — not written out in the
    window, where it would be read once and scrolled past every time after.
    """

    #: Closed during setup without rolling.
    cancelled = Signal()
    #: The GM threw a player's declaration away.
    dismissed = Signal()
    #: The GM will roll whatever players have not, themselves.
    rollWaitingRequested = Signal()
    #: The GM stopped a running action: nothing still out will count.
    calledOff = Signal()

    def __init__(
        self,
        data: GameData,
        *,
        action_id: str,
        title: str,
        targets: list,
        go_caption: str,
        box_title: str,
        columns: tuple[tuple[str, str], ...],
        from_card: bool = False,
        help_text: str = "",
        box_help: str = BOX_HELP,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self.action_id = action_id
        self.phase = PHASE_SETUP
        self._settled = False
        self._fitted = False
        self._targets = list(targets)
        self._rows: list[ActionRow] = []
        self._columns = columns
        self.setWindowTitle(title)

        outer = QVBoxLayout(self)
        self.heading = QLabel()
        self.heading.setTextFormat(Qt.TextFormat.RichText)
        self.heading.setWordWrap(True)
        outer.addWidget(self.heading)
        self.heading.hide()

        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._scroll = BodyScroll()
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll, stretch=1)

        self.box = ActionDropBox(box_title, lambda ref: self._party(ref) is not None)
        self.box.dropped.connect(self.include_ref)
        box_layout = QVBoxLayout(self.box)
        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(int(theme.metric("space.sm")))
        headers = (
            ("", ""),
            *columns,
            ("Adjust", "A bonus (+) or penalty (-) to this creature's part in the action"),
        )
        for column, (caption, tip) in enumerate(headers):
            header = QLabel(caption)
            header.setStyleSheet(muted_style())
            header.setToolTip(tip)
            self._grid.addWidget(header, 0, column)
        self._grid.addWidget(
            HelpButton(box_help), 0, len(headers), alignment=Qt.AlignmentFlag.AlignRight
        )
        # The action's own columns take the spare width; each row's result has a line of
        # its own under it, spanning the table, so a long outcome never squeezes a column.
        self._grid.setColumnStretch(1, 1)
        box_layout.addLayout(self._grid)
        self.box.setToolTip(box_help)
        self._body.addWidget(self.box)
        # A window taller than what it holds — dragged so, or left so by a section that
        # folded — keeps the slack under the last section rather than spreading it
        # between every one of them.
        self._body.addStretch(1)

        self._footer = QVBoxLayout()
        outer.addLayout(self._footer)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(muted_style())
        outer.addWidget(self.summary)

        buttons = QHBoxLayout()
        self.help = HelpButton(help_text)
        self.help.setVisible(bool(help_text))
        buttons.addWidget(self.help, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._dismiss = QPushButton("Dismiss")
        self._dismiss.setToolTip("Throw this action away without rolling it")
        self._dismiss.clicked.connect(self._on_dismiss)
        self._call_off = QPushButton("Call off")
        self._call_off.setToolTip("Stop waiting: rolls still out will not count")
        self._call_off.clicked.connect(self._on_call_off)
        self._roll_waiting = QPushButton("🎲 Roll for the waiting")
        self._roll_waiting.setToolTip(
            "Roll what the players have not, here, off their sheets as you last saw them"
        )
        self._roll_waiting.clicked.connect(self.rollWaitingRequested)
        self._close = QPushButton("Close" if from_card else "Cancel")
        self._close.clicked.connect(self.close)
        self.go = QPushButton(go_caption)
        self.go.setDefault(True)
        self.go.clicked.connect(self._emit_go)
        for button in (self._dismiss, self._call_off, self._roll_waiting):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self._close)
        buttons.addWidget(self.go)
        outer.addLayout(buttons)
        self._dismiss.setVisible(from_card)
        self._call_off.hide()
        self._roll_waiting.hide()
        self._from_card = from_card

    # -- building ----------------------------------------------------------------

    def add_section(self, widget: QWidget, *, after_targets: bool = False) -> None:
        """Put one of the action's own controls in, above the table or below it."""
        if after_targets:
            # Above the trailing stretch, which stays last.
            self._body.insertWidget(self._body.count() - 1, widget)
        else:
            self._body.insertWidget(self._body.indexOf(self.box), widget)

    def set_heading(self, text: str) -> None:
        """Say whose action this is, as rich text — or nothing, and take the line away."""
        self.heading.setText(text)
        self.heading.setVisible(bool(text))

    def add_footer(self, widget: QWidget) -> None:
        """Put a line under the scrolled body, above the summary — always in sight."""
        self._footer.addWidget(widget)

    def build_rows(self, make_cells: Callable[[object], list[QWidget]], wanted: set[str]) -> None:
        """A row per creature on the scene, its own cells from *make_cells*."""
        for number, party in enumerate(self._targets):
            index = 1 + 2 * number
            row = self.make_row(party, make_cells(party))
            self._rows.append(row)
            self._grid.addWidget(row.include, index, 0)
            for column, cell in enumerate(row.cells, start=1):
                self._grid.addWidget(cell, index, column)
            tail = 1 + len(row.cells)
            self._grid.addWidget(row.modifier, index, tail)
            self._grid.addWidget(row.result, index + 1, 1, 1, tail)
            row.result.hide()
            row.include.setChecked(party.ref in wanted)
            row.include.toggled.connect(lambda on, r=row: self.on_include(r, on))
            row.modifier.valueChanged.connect(lambda _v: self.sync())
        if not self._rows:
            empty = QLabel("Nobody is on the scene.")
            empty.setStyleSheet(muted_style(italic=True))
            self._grid.addWidget(empty, 1, 0, 1, 2 + len(self._columns))

    def make_row(self, party, cells: list[QWidget]) -> ActionRow:
        """The row object for *party* — an action may keep more on it."""
        row = ActionRow(party, cells)
        row.modifier.setToolTip(f"A bonus (+) or penalty (-) to {party.name}'s part in the action")
        return row

    # -- the rows ------------------------------------------------------------------

    def _party(self, ref: str):
        """The creature a scene ref — or a roster card's ``kind:source`` — names."""
        for party in self._targets:
            if ref == party.ref or ref == f"{party.kind}:{party.source}":
                return party
        return None

    def _row(self, ref: str) -> ActionRow | None:
        return next((r for r in self._rows if r.party.ref == ref), None)

    def row_named(self, name: str) -> ActionRow | None:
        return next((r for r in self._rows if r.party.name == name), None)

    def include_ref(self, ref: str) -> bool:
        """Put the creature *ref* names in (a card dropped on the table)."""
        if self.phase != PHASE_SETUP:
            return False
        party = self._party(ref)
        row = self._row(party.ref) if party is not None else None
        if row is None or not row.include.isEnabled():
            return False
        row.include.setChecked(True)
        return True

    def included(self) -> list[ActionRow]:
        return [row for row in self._rows if row.include.isChecked()]

    def set_included(self, refs: list[str]) -> None:
        for row in self._rows:
            row.include.setChecked(row.party.ref in refs)

    def on_include(self, row: ActionRow, on: bool) -> None:
        """A row was ticked in or out. The action may want to know who."""
        self.sync()

    def sync(self) -> None:
        """Re-read every control. Overridden; the base has nothing of its own to say."""

    # -- results ---------------------------------------------------------------------

    def set_result(self, ref: str, text: str, token: str = "") -> None:
        """What one creature's rolls came to, as rich text, tinted by *token*."""
        row = self._row(ref)
        if row is None:
            return
        row.show_result(text, tinted_style(token, bold=False) if token else "")

    def result_text(self, ref: str) -> str:
        row = self._row(ref)
        return row.result.text() if row is not None else ""

    # -- the phases --------------------------------------------------------------------

    def lock_setup(self) -> None:
        """Lock the action's own controls. Overridden to lock more than the rows."""
        for row in self._rows:
            for widget in row.inputs():
                widget.setEnabled(False)

    def start_running(self) -> None:
        """Rolled: lock everything set up, and wait for the rolls to come back."""
        self.phase = PHASE_RUNNING
        self._settled = True
        self.lock_setup()
        self.go.hide()
        self._dismiss.hide()
        self._close.setText("Close")
        self._call_off.show()

    def set_waiting(self, names: list[str]) -> None:
        """Who is still being waited for — and the button to stop waiting for them."""
        if self.phase != PHASE_RUNNING:
            return
        self._roll_waiting.setVisible(bool(names))
        if names:
            self.summary.setText("Waiting for " + ", ".join(names) + "…")

    def finish(self, text: str) -> None:
        """Everything is in (or it was called off): say so, and stop offering to wait."""
        self.phase = PHASE_DONE
        self._call_off.hide()
        self._roll_waiting.hide()
        self.summary.setText(text)
        self.summary.setStyleSheet("")

    def _emit_go(self) -> None:
        """The action's own go — overridden to hand its plan on."""

    def _on_dismiss(self) -> None:
        self._settled = True
        self.dismissed.emit()
        self.close()

    def _on_call_off(self) -> None:
        self.calledOff.emit()

    # -- showing -------------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        if not self._fitted:
            self._fitted = True
            fit_to_screen(self)

    def done(self, result: int) -> None:
        """Every way the window goes away — Close, Esc, the title bar's ✕.

        Overridden here rather than ``closeEvent``: Esc rejects a dialog without any
        close event. During setup that is a cancel; once it is rolling it only hides,
        since the rolls still to come back are what the window is for.
        """
        if not self._settled:
            self._settled = True
            self.cancelled.emit()
        super().done(result)


# -- the roll the GM asks a player for ------------------------------------------------------


class RollPromptDialog(QDialog):
    """The GM asking this player for one roll — their check, their Dodge, their save.

    Non-modal, so the game goes on around it, and answered with one click: the roll is
    made on the player's own roller, their number filled in, and lands in the action's
    table in everyone's history. Where the spec is a choice of traits (resisting an
    impression), a combo offers "Highest" first and each trait after it. **Later**
    closes it; the same request still waits in the history.
    """

    def __init__(
        self,
        data: GameData,
        character: Character,
        *,
        action_id: str,
        spec: RollSpec,
        asker: str = "",
        roll: Callable[[RollSpec], bool],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._character = character
        self.action_id = action_id
        self._roll_fn = roll
        self._choices: list[RollSpec] = []
        self.setWindowTitle("Roll requested")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QVBoxLayout(self)
        self._what = QLabel()
        self._what.setTextFormat(Qt.TextFormat.RichText)
        self._what.setWordWrap(True)
        layout.addWidget(self._what)
        self._caption = QLabel()
        self._caption.setWordWrap(True)
        self._caption.setStyleSheet(tinted_style("accent.dice"))
        layout.addWidget(self._caption)
        self._choice = QComboBox()
        self._choice.setToolTip("Roll your highest option, or pick the trait to roll")
        guard_wheel(self._choice)
        self._choice.currentIndexChanged.connect(self._sync)
        layout.addWidget(self._choice)
        self._outcomes = QLabel()
        self._outcomes.setWordWrap(True)
        self._outcomes.setStyleSheet(muted_style(italic=True))
        layout.addWidget(self._outcomes)

        buttons = QHBoxLayout()
        later = QPushButton("Later")
        later.setToolTip("Close this — the request still waits in the history")
        later.clicked.connect(self.close)
        self._roll = QPushButton("🎲 Roll")
        self._roll.setDefault(True)
        self._roll.clicked.connect(self._on_roll)
        buttons.addStretch(1)
        buttons.addWidget(later)
        buttons.addWidget(self._roll)
        layout.addLayout(buttons)
        self.update_spec(spec, asker=asker)

    def update_spec(self, spec: RollSpec, *, asker: str = "") -> None:
        """Ask for *spec* now — the next roll of the same action (a save after a Dodge)."""
        self._spec = spec
        who = html_escape(asker or "The GM")
        about = f" for <b>{html_escape(spec.title)}</b>" if spec.title else ""
        self._what.setText(f"<b>{who}</b> asks you to roll{about}:")
        self._choices = (
            best_own_rolls(spec.trait_key, self._character, self._data)
            if spec.kind == KIND_BEST_OF and spec.trait_key
            else []
        )
        best = max(self._choices, key=lambda c: c.modifier) if self._choices else None
        self._choice.blockSignals(True)
        self._choice.clear()
        self._choice.addItem(
            f"Highest ({best.label} {signed(best.modifier)})" if best else "Highest", ""
        )
        for choice in self._choices:
            self._choice.addItem(f"{choice.label} {signed(choice.modifier)}", choice.trait_key)
        self._choice.blockSignals(False)
        self._choice.setVisible(bool(self._choices))
        ladder = " → ".join(spec.outcomes)
        self._outcomes.setText(f"Failing: {ladder}" if ladder else (spec.hint or ""))
        self._sync()

    def spec(self) -> RollSpec:
        """What will be rolled: the request, or the trait the player picked instead."""
        spec = self._spec
        if self._choices and self._choice.currentData():
            chosen = next(
                (c for c in self._choices if c.trait_key == self._choice.currentData()), None
            )
            if chosen is not None:
                spec = replace(
                    spec,
                    kind=chosen.kind,
                    trait_key=chosen.trait_key,
                    label=f"{spec.label} ({chosen.label})",
                )
        return spec

    def _sync(self) -> None:
        shown = localize_spec(self.spec(), self._character, self._data)
        against = f" vs. DC {shown.dc}" if shown.dc is not None else ""
        self._caption.setText(f"🎲 {shown.label} {signed(shown.modifier)}{against}")

    def _on_roll(self) -> None:
        if self._roll_fn(self.spec()):
            self.close()


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
