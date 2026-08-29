"""One creature on the shared board, as a card.

The same widget on both screens, in the spirit of the note that a player card and
an NPC card are one card: what a GM and a player *read* off a scene entry is
identical — who it is, where they are in the order, what state they are visibly
in — and only what they may *do* with it differs. So *gm* adds the drag and the
right-click menu, and changes nothing about what is drawn.

**A scene card is not a statblock.** It shows a thumbnail, a name, an initiative
and the condition chips, and there is deliberately nowhere on it for a defence or
a power: the wire carries nothing else (see
:func:`~mm_companion.core.session.protocol.sanitize_scene`), which is the real
guarantee — a card cannot leak what never left the GM's machine.

The card is built from a **wire entry dict**, not from a
:class:`~mm_companion.core.character.Character`, because on a player's screen
there is no character to build it from. The one thing it needs the game data for
is turning a condition id into a caption, which is the same lookup the sheet's own
chips do — and a mod-skew warning already covers the case where the two ends
disagree about what an id means.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget

from mm_companion.core.character import AppliedCondition
from mm_companion.core.data_loader import Condition, GameData
from mm_companion.core.session.protocol import (
    DISPOSITION_ENEMY,
    DISPOSITION_FRIENDLY,
    DISPOSITION_NEUTRAL,
    DISPOSITION_PLAYER,
    SCENE_DISPOSITIONS,
)
from mm_companion.ui import theme
from mm_companion.ui.card_chips import (
    InitiativeBadge,
    _ConditionChip,
    start_card_drag,
)
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.sections.conditions import condition_display_name, condition_tooltip
from mm_companion.ui.widgets import ElidingLabel

#: How wide one card's column is, so a row of them lines up in the flow. Stated
#: rather than measured, unlike a GM card (whose ``body_width_hint`` is), and
#: narrower than one: a scene card has no pinned strip, no damage row and no
#: buttons, so there is nothing on it whose size a ruleset can change — and the
#: block it lives in is pinned to the strip on a player's sheet, where every pixel
#: of width is dear.
SCENE_CARD_WIDTH = 178

#: How far the pointer must move with the button down to count as a drag. The
#: same number the GM cards use, so one gesture feels like one gesture.
DRAG_THRESHOLD = 8


#: What each disposition is called on the card and in the menu that sets it, and
#: which colour token draws its edge. One mapping so the caption and the colour
#: cannot come to disagree, and ordered the way the menu offers them: what a GM
#: reaches for most, first.
DISPOSITION_LABELS: dict[str, str] = {
    DISPOSITION_ENEMY: "Enemy",
    DISPOSITION_FRIENDLY: "Friendly",
    DISPOSITION_NEUTRAL: "Neutral",
    DISPOSITION_PLAYER: "Player",
}
#: The three a GM chooses between. A player's entry is not in it: a seat is a
#: player, and offering to call it an enemy would be offering a lie.
#: How strongly the disposition colour is washed behind a card. Faint on purpose:
#: the edge is the signal, and a fill strong enough to compete with it would make
#: the name and the condition chips harder to read on every card at once.
EDGE_WASH = 0.08

GM_DISPOSITIONS: tuple[str, ...] = (
    DISPOSITION_ENEMY,
    DISPOSITION_FRIENDLY,
    DISPOSITION_NEUTRAL,
)


def entry_ref(entry: dict) -> str:
    """The opaque handle a scene entry answers to."""
    return str(entry.get("ref", ""))


def entry_disposition(entry: dict) -> str:
    """What this entry is to the table, defaulting to :data:`DISPOSITION_ENEMY`.

    An absent or unknown value is *enemy* rather than a fifth "unknown" state,
    and that is the safe way round: a board is mostly things to fight, so the
    default is the common case, and a friendly NPC the GM has not marked reads as
    dangerous rather than a threat reading as safe.
    """
    value = entry.get("disposition")
    if isinstance(value, str) and value in SCENE_DISPOSITIONS:
        return value
    return DISPOSITION_ENEMY


def entry_initiative(entry: dict) -> int | None:
    """One entry's rolled initiative, or ``None`` when it has not rolled.

    Absent and ``None`` mean the same thing here and nothing else does: the wire
    omits the key rather than sending a null, and a zero is a real (bad) roll.
    """
    value = entry.get("initiative")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def order_scene(entries: list[dict], manual: list[str]) -> list[dict]:
    """The scene in reading order: rolled first, then the GM's own arrangement.

    The same rule the NPC block sorts by
    (:meth:`~mm_companion.ui.gm_window.GMWindow._ordered_npcs`), and deliberately
    so — a GM looking at two boards should not have to hold two orderings in their
    head. Entries with an initiative sort above every entry without one, highest
    first; the rest keep the order *manual* puts them in, with anything it has not
    heard of following on the end.

    Ties keep their manual order, which is a stable-sort fact rather than a rule
    about M&M: the book breaks a tie by modifier, then Agility, then Awareness,
    and none of those are on the wire. What is on the wire is what the GM decided,
    and that is the better answer anyway.
    """
    known = [ref for ref in manual if any(entry_ref(e) == ref for e in entries)]
    known += [entry_ref(e) for e in entries if entry_ref(e) not in known]
    by_ref = {entry_ref(e): e for e in entries}
    base = [by_ref[ref] for ref in known if ref in by_ref]
    rolled = sorted(
        (e for e in base if entry_initiative(e) is not None),
        key=lambda e: entry_initiative(e),  # type: ignore[arg-type,return-value]
        reverse=True,
    )
    return rolled + [e for e in base if entry_initiative(e) is None]


class SceneCard(QFrame):
    """One scene entry. Read-only for a player; draggable and removable for a GM."""

    #: The GM dragged this card; the flow it lands in decides what that means.
    dragStarted = Signal(str)
    #: Take this entry off the board (GM only).
    removeRequested = Signal(str)
    #: Put this entry back in the un-rolled zone (GM only).
    initiativeCleared = Signal(str)
    #: Roll this entry's initiative (GM only, and never for a player's entry).
    initiativeRollRequested = Signal(str)
    #: ``(ref, disposition)`` — the GM said what this creature is to the table.
    dispositionChanged = Signal(str, str)

    def __init__(
        self,
        entry: dict,
        data: GameData,
        parent: QWidget | None = None,
        *,
        gm: bool = False,
        own: bool = False,
        portrait: QPixmap | None = None,
    ) -> None:
        super().__init__(parent)
        # Named for the stylesheet that colours its edge, never a bare ``QFrame``:
        # an unscoped rule would repaint every chip and label inside it.
        self.setObjectName("SceneCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedWidth(SCENE_CARD_WIDTH)
        #: What this creature is to the table; :meth:`set_entry` reads it off the
        #: wire and :meth:`_restyle` paints the edge with it.
        self._disposition = DISPOSITION_ENEMY

        self._data = data
        self._gm = gm
        #: Whether this entry is the person reading it. The four disposition
        #: colours say *what* each creature is; every seat is the same blue, so
        #: they cannot also say which one is you — and finding yourself in a
        #: twelve-row turn order is the first thing a player does with the board.
        self._own = own
        self._conditions_by_id: dict[str, Condition] = {c.id: c for c in data.conditions}
        self.ref = entry_ref(entry)
        #: Where a left-button press landed, to tell a drag from a click.
        self._press_pos: QPoint | None = None
        self._initiative: int | None = None

        layout = QVBoxLayout(self)
        margins = theme.box("card.margins")
        layout.setContentsMargins(*margins)
        layout.setSpacing(int(theme.metric("space.xs")))

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(int(theme.metric("space.xs")))

        thumb_size = int(theme.metric("gm.thumb"))
        self._thumb = QLabel("?")
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setFixedSize(thumb_size, thumb_size)
        self._thumb.setFrameShape(QLabel.Shape.Box)
        header.addWidget(self._thumb)

        # Elides rather than wraps, for the reason a collapsed NPC card's name
        # does: it shares a row with the thumbnail, whose height is the row's, and
        # a second line would have nowhere to go but out of the card.
        self._name = ElidingLabel("")
        name_font = self._name.font()
        name_font.setBold(True)
        self._name.setFont(name_font)
        header.addWidget(self._name, stretch=1)

        # This is where initiative is rolled now — the one board that sorts by it.
        # Live only on a GM's screen and only for an NPC: a player rolls their own
        # on their own sheet, so a click here would be rolling somebody else's die.
        # Whether it is live is set in :meth:`set_entry`, which is the first place
        # that knows whose entry this is.
        self._badge = InitiativeBadge()
        self._badge.clicked.connect(lambda: self.initiativeRollRequested.emit(self.ref))
        self._badge.cleared.connect(lambda: self.initiativeCleared.emit(self.ref))
        header.addWidget(self._badge)
        layout.addLayout(header)

        self._chips = FlowContainer()
        self._chip_flow = FlowLayout(self._chips)
        layout.addWidget(self._chips)

        self.set_entry(entry)
        self.set_portrait(portrait)

    # -- what this creature is to the table --------------------------------

    @property
    def disposition(self) -> str:
        """Enemy, friendly, neutral, or player."""
        return self._disposition

    def _restyle(self) -> None:
        """Paint the edge in this creature's colour.

        The **edge**, and a wash behind it, rather than the name or a badge: it
        has to be readable at the size a dozen of these are on screen at once and
        from the corner of an eye, which is the one thing a border is better at
        than text. The card carries the word as well — in its hover text, and in
        the menu that sets it — because a fact told only in colour is told to
        fewer people than a fact told twice.

        A frameless ``QFrame`` painted by a scoped rule rather than the styled
        panel it used to be: a ``StyledPanel`` draws the platform's own border
        *over* a stylesheet one, so the two would fight and the platform would win
        on some styles.
        """
        token = f"scene.{self._disposition}"
        self.setStyleSheet(
            f"#SceneCard {{"
            f" border: {int(theme.metric('border.width.emphasis'))}px solid"
            f" {theme.color(token)};"
            f" background: {theme.wash(token, EDGE_WASH)};"
            f" border-radius: {int(theme.metric('radius.card'))}px; }}"
        )

    # -- what the card is showing -----------------------------------------

    def set_entry(self, entry: dict) -> None:
        """Restate the card from a wire entry. The portrait is not touched.

        Portraits arrive on their own messages and outlive any number of scene
        updates, so a rebuild that also cleared the picture would blank every card
        each time a GM applied a condition.
        """
        self.ref = entry_ref(entry)
        name = str(entry.get("name", "")) or "Unnamed"
        # "(you)", the way a player card already marks the GM's own seat — a word
        # rather than a fifth colour, which would have to compete with the four
        # that already mean something here.
        self._name.setText(f"{name} (you)" if self._own else name)
        self._initiative = entry_initiative(entry)
        self._disposition = entry_disposition(entry)
        self._restyle()
        # A player's entry carries their id; an NPC's carries nothing that says
        # what it stands for, which is the whole point of an opaque ref.
        self._badge.set_live(self._gm and not entry.get("player_id"))
        self._badge.set_initiative(self._initiative)
        self._show_conditions(entry.get("conditions") or [])
        self._name.set_hover_text(self._hover_text(name))

    def set_portrait(self, pixmap: QPixmap | None) -> None:
        """Show a decoded thumbnail, or fall back to the placeholder."""
        if pixmap is None or pixmap.isNull():
            self._thumb.setText("?")
            self._thumb.setPixmap(QPixmap())
            return
        size = self._thumb.width()
        self._thumb.setText("")
        self._thumb.setPixmap(
            pixmap.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    @property
    def initiative(self) -> int | None:
        """This entry's rolled initiative, or ``None``."""
        return self._initiative

    def condition_names(self) -> list[str]:
        """The chip captions, in order — the readable form of the card's state."""
        return [
            self._chip_flow.itemAt(i).widget().text()  # type: ignore[union-attr]
            for i in range(self._chip_flow.count())
        ]

    def _show_conditions(self, raw: list) -> None:
        while self._chip_flow.count():
            item = self._chip_flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        shown = 0
        for item in raw:
            if not isinstance(item, dict):
                continue
            applied = AppliedCondition(
                condition_id=str(item.get("id", "")),
                parameter=item.get("parameter") or None,
                count=int(item.get("count", 1) or 1),
            )
            record = self._conditions_by_id.get(applied.condition_id)
            # Chips are never removable here on either screen. A player has no
            # business taking a condition off a creature, and a GM does it on the
            # card that owns the model — this one is a mirror of it.
            self._chip_flow.addWidget(
                _ConditionChip(
                    condition_display_name(applied, record),
                    tooltip=condition_tooltip(applied, record, self._conditions_by_id),
                    compact=True,
                    parent=self._chips,
                )
            )
            shown += 1
        self._chips.setVisible(bool(shown))

    def _hover_text(self, name: str) -> str:
        """What the name's tooltip says. On the label, not the card.

        A tooltip on the *card* fires wherever the pointer rests, which on a card
        this small is most of it — and one that reads "drag to reorder" while the
        pointer sits over a condition chip is answering a question nobody asked.
        """
        parts = [f"{name} (you)" if self._own else name, DISPOSITION_LABELS[self._disposition]]
        if self._initiative is not None:
            parts.append(f"Initiative {self._initiative}")
        if self._gm:
            parts.append("Drag to reorder · right-click for more")
        return " — ".join(parts)

    # -- the GM's gestures -------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._gm and event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._press_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < DRAG_THRESHOLD:
            return
        self._press_pos = None
        self.start_drag()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def start_drag(self) -> None:
        """Carry this card's ref, so the flow it is dropped in can place it."""
        self.dragStarted.emit(self.ref)
        start_card_drag(self, self.ref)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Right-click means "take that away", the specific answer first.

        Clearing an initiative is the narrower of the two and comes first, and it
        only appears when there is one to clear — the general "off the board
        entirely" is always there underneath it.
        """
        if not self._gm or not self.ref:
            return
        self.build_context_menu().exec(event.globalPos())

    def build_context_menu(self) -> QMenu:
        """The right-click menu, built but not shown.

        Separate from showing it for the reason
        :func:`~mm_companion.ui.sections.conditions.build_condition_menu` is a
        function: a menu that only exists inside ``exec`` cannot be read by
        anything, and what this one *offers* is the interesting half.
        """
        menu = QMenu(self)
        if self._disposition != DISPOSITION_PLAYER:
            # A submenu rather than three flat items: it is a *state* with one
            # answer at a time, so the tick beside the current one is half of what
            # this menu is for. A player's entry gets none of it — a seat is a
            # player, and offering to call it an enemy would be offering a lie.
            # Parented to the menu, never ``menu.addMenu(title)``: that hands
            # ownership *back* to the caller, so a submenu with no Python
            # reference is collected out from under the menu while it is open —
            # the lesson ``build_condition_menu`` already carries.
            marks = QMenu("Mark as", menu)
            menu.addMenu(marks)
            for value in GM_DISPOSITIONS:
                action = marks.addAction(DISPOSITION_LABELS[value])
                action.setCheckable(True)
                action.setChecked(value == self._disposition)
                action.triggered.connect(
                    lambda _checked=False, v=value: self.dispositionChanged.emit(self.ref, v)
                )
            menu.addSeparator()
        if self._initiative is not None:
            menu.addAction("Clear initiative", lambda: self.initiativeCleared.emit(self.ref))
        menu.addAction("Remove from scene", lambda: self.removeRequested.emit(self.ref))
        return menu
