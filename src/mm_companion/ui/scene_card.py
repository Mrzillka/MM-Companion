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

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget

from mm_companion.core.character import AppliedCondition
from mm_companion.core.data_loader import Condition, GameData
from mm_companion.ui import theme
from mm_companion.ui.card_chips import _ConditionChip
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.sections.conditions import condition_display_name, condition_tooltip
from mm_companion.ui.widgets import ElidingLabel

#: What a dragged scene reference is called on the clipboard. One format for both
#: halves of the gesture — dragging a card *into* the scene and dragging one
#: *within* it — because the drop is the same question either way: put this
#: reference at this index. See :meth:`~mm_companion.ui.sections.scene.SceneFlow`.
SCENE_MIME = "application/x-mm-scene-ref"

#: How wide one card's column is, so a row of them lines up in the flow. Narrower
#: than a GM card (:data:`~mm_companion.ui.npc_card.CARD_WIDTH`): a scene card has
#: no pinned strip, no damage row and no buttons, and the block it lives in is
#: pinned to the strip on a player's sheet where every pixel of width is dear.
SCENE_CARD_WIDTH = 178

#: How far the pointer must move with the button down to count as a drag. The
#: same number the GM cards use, so one gesture feels like one gesture.
DRAG_THRESHOLD = 8

#: The badge before anything has been rolled. A dash rather than a zero, for the
#: reason :data:`~mm_companion.ui.npc_card.NO_INITIATIVE` is one: not rolled yet
#: is not an initiative of nought, and the two sort differently.
NO_INITIATIVE = "—"


def entry_ref(entry: dict) -> str:
    """The opaque handle a scene entry answers to."""
    return str(entry.get("ref", ""))


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

    def __init__(
        self,
        entry: dict,
        data: GameData,
        parent: QWidget | None = None,
        *,
        gm: bool = False,
        portrait: QPixmap | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(SCENE_CARD_WIDTH)

        self._data = data
        self._gm = gm
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

        self._badge = QLabel(NO_INITIATIVE)
        badge_font = self._badge.font()
        badge_font.setBold(True)
        badge_font.setPointSizeF(theme.font_size("size.terms"))
        self._badge.setFont(badge_font)
        self._badge.setStyleSheet(f"color: {theme.color('accent')};")
        header.addWidget(self._badge)
        layout.addLayout(header)

        self._chips = FlowContainer()
        self._chip_flow = FlowLayout(self._chips)
        layout.addWidget(self._chips)

        self.set_entry(entry)
        self.set_portrait(portrait)

    # -- what the card is showing -----------------------------------------

    def set_entry(self, entry: dict) -> None:
        """Restate the card from a wire entry. The portrait is not touched.

        Portraits arrive on their own messages and outlive any number of scene
        updates, so a rebuild that also cleared the picture would blank every card
        each time a GM applied a condition.
        """
        self.ref = entry_ref(entry)
        name = str(entry.get("name", "")) or "Unnamed"
        self._name.setText(name)
        self._initiative = entry_initiative(entry)
        self._badge.setText(NO_INITIATIVE if self._initiative is None else str(self._initiative))
        self._badge.setToolTip(
            "Has not rolled initiative yet" if self._initiative is None else "Initiative"
        )
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
        parts = [name]
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
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(SCENE_MIME, self.ref.encode("utf-8"))
        drag.setMimeData(mime)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, 12))
        self.dragStarted.emit(self.ref)
        drag.exec(Qt.DropAction.MoveAction)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Right-click means "take that away", the specific answer first.

        Clearing an initiative is the narrower of the two and comes first, and it
        only appears when there is one to clear — the general "off the board
        entirely" is always there underneath it.
        """
        if not self._gm or not self.ref:
            return
        menu = QMenu(self)
        if self._initiative is not None:
            menu.addAction("Clear initiative", lambda: self.initiativeCleared.emit(self.ref))
        menu.addAction("Remove from scene", lambda: self.removeRequested.emit(self.ref))
        menu.exec(event.globalPos())
