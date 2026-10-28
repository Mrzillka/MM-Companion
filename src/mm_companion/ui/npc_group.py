"""A group of NPCs on the GM's board: one frame, one row per creature.

The squad a GM runs as one — five thugs, a pack of drones — framed together so the
board reads as the fight does. The rows are ordinary :class:`~.npc_card.NPCCard`\\ s
built in their row mode, so every control on a creature (its pinned tiles, its damage
row, its conditions, its menu, its drag) works exactly as on a loose card, and the GM
window wires both through one road.

The header names the squad, counts it, and says how many are still up — a row is
dimmed while its creature is down (``ui/defeat_look.py``). Its ⋯ menu renames the
group, puts all of it on the Scene or takes it off, and ungroups it. Dragging the header
moves the whole group; dragging a row out of it takes that creature out (see
``GMWindow._drop_on_npcs``), and dropping a card on another makes or grows a group
(``GMWindow._merge_npcs``). What a group *is* lives in :mod:`mm_companion.core.npc_groups`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.npc_groups import NPCGroup
from mm_companion.core.rules import DEFEAT_NONE
from mm_companion.ui import theme
from mm_companion.ui.card_chips import start_card_drag
from mm_companion.ui.npc_card import DRAG_THRESHOLD, NPCCard
from mm_companion.ui.widgets import muted_style

#: What a group's dragged header carries: ``"npcgroup:<group id>"``.
GROUP_KIND = "npcgroup"


def group_ref(group_id: str) -> str:
    """The drag reference of a whole group."""
    return f"{GROUP_KIND}:{group_id}"


class NPCGroupCard(QFrame):
    """One squad of NPCs, framed, a row per member."""

    #: The group's id — the GM asked to rename it.
    renameRequested = Signal(str)
    #: The group's id — break the group up into loose cards again.
    ungroupRequested = Signal(str)
    #: ``(group id, on)`` — put every member on the Scene, or take every one off.
    sceneRequested = Signal(str, bool)
    #: The group's id — its members combine their attacks on one target (PDF p201).
    teamAttackRequested = Signal(str)

    def __init__(self, group: NPCGroup, rows: list[NPCCard], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.group_id = group.id
        self._name = group.name
        self._rows = list(rows)
        self._press_pos = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*theme.box("card.margins"))
        layout.setSpacing(int(theme.metric("space.xs")))

        self._header = QWidget()
        header = QHBoxLayout(self._header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(int(theme.metric("space.sm")))
        self._title = QLabel()
        font = self._title.font()
        font.setBold(True)
        self._title.setFont(font)
        header.addWidget(self._title)
        self._status = QLabel()
        self._status.setStyleSheet(muted_style())
        header.addWidget(self._status, stretch=1)
        self._menu_button = QToolButton()
        self._menu_button.setText("⋯")
        self._menu_button.setAutoRaise(True)
        self._menu_button.setToolTip("Rename, put on the Scene, or ungroup")
        self._menu_button.clicked.connect(
            lambda: self.build_menu().exec(
                self._menu_button.mapToGlobal(self._menu_button.rect().bottomLeft())
            )
        )
        header.addWidget(self._menu_button, alignment=Qt.AlignmentFlag.AlignTop)
        self._header.setToolTip("Drag to move the whole group; right-click for its menu")
        layout.addWidget(self._header)

        for index, row in enumerate(self._rows):
            if index:
                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setFrameShadow(QFrame.Shadow.Sunken)
                layout.addWidget(line)
            layout.addWidget(row)
            row.defeatChanged.connect(lambda _state: self.refresh_status())
        layout.addStretch()
        self.refresh_status()

    # -- what it shows ---------------------------------------------------------

    @property
    def rows(self) -> list[NPCCard]:
        return list(self._rows)

    @property
    def members(self) -> list[str]:
        """The members' file names, in row order."""
        return [row.name_key for row in self._rows]

    def title_text(self) -> str:
        return self._title.text()

    def status_text(self) -> str:
        return self._status.text()

    def refresh_status(self) -> None:
        """Restate the header: the name, the head count, and how many are still up."""
        count = len(self._rows)
        self._title.setText(f"{self._name} ×{count}")
        down = sum(1 for row in self._rows if row.defeat != DEFEAT_NONE)
        self._status.setText(f"{count - down} up · {down} down" if down else "")

    def build_menu(self) -> QMenu:
        """The group's menu (the ⋯ button and a right-click on the header)."""
        menu = QMenu(self)
        menu.addAction(
            "Team attack…", lambda: self.teamAttackRequested.emit(self.group_id)
        ).setToolTip("Combine the members' attacks on one target")
        menu.addSeparator()
        menu.addAction("Rename group…", lambda: self.renameRequested.emit(self.group_id))
        menu.addSeparator()
        menu.addAction(
            "Put all on the Scene", lambda: self.sceneRequested.emit(self.group_id, True)
        )
        menu.addAction(
            "Take all off the Scene", lambda: self.sceneRequested.emit(self.group_id, False)
        )
        menu.addSeparator()
        menu.addAction("Ungroup", lambda: self.ungroupRequested.emit(self.group_id))
        return menu

    # -- width -----------------------------------------------------------------

    def natural_width(self, strip: int) -> int:
        """How wide the group wants to be to keep each row on one line."""
        left, _top, right, _bottom = theme.box("card.margins")
        widest = max((row.row_width_hint(strip) for row in self._rows), default=0)
        return widest + left + right + 2 * self.frameWidth()

    def apply_width(self, strip: int, room: int = 0) -> None:
        """Take a line of the board to itself: *room* wide, or its own width before the
        board has measured. Each row is handed what is inside the frame."""
        width = room if room > 0 else self.natural_width(strip)
        left, _top, right, _bottom = theme.box("card.margins")
        inner = max(1, width - left - right - 2 * self.frameWidth())
        for row in self._rows:
            row.apply_row_width(inner, strip)
        self.setFixedWidth(width)

    # -- dragging the whole group ---------------------------------------------

    def _on_header(self, point) -> bool:
        return self._header.geometry().contains(point)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton and self._on_header(
            event.position().toPoint()
        ):
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._press_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < DRAG_THRESHOLD:
            super().mouseMoveEvent(event)
            return
        self._press_pos = None
        start_card_drag(self, group_ref(self.group_id))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._on_header(event.pos()):
            self.build_menu().exec(event.globalPos())
            return
        super().contextMenuEvent(event)
