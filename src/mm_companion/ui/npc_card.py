"""One NPC, as a card on the GM's window.

Unlike a :class:`~mm_companion.ui.player_card.PlayerCard` — which shows a *remote*
player's live sheet and can only *ask* their app to change it — an NPC is the
GM's own, held locally as an ordinary :class:`~mm_companion.core.character.Character`
saved in the workspace ``gm_characters/`` dir. So this card acts on the model
directly: conditions apply straight onto it (persisted like any sheet edit),
initiative is rolled here, and the whole thing can be copied. The card is keyed
by its file name, which is the stable identity a session's cast
(:attr:`~mm_companion.core.session.model.SessionState.npc_paths`) records.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import library
from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.library import CharacterSummary

#: The portrait placeholder's side, in pixels.
PORTRAIT_SIZE = 96
#: How wide a card is. Fixed, so a row of them lines up in the flow layout.
CARD_WIDTH = 210


class NPCCard(QFrame):
    """A GM's NPC: portrait, name, and estimated Power Level, acted on locally."""

    #: The NPC's file name — the GM asked to open its sheet.
    openRequested = Signal(str)
    #: The NPC's file name — take it out of this session (the file stays).
    removeRequested = Signal(str)
    #: The NPC's file name — delete the file for good.
    deleteRequested = Signal(str)

    def __init__(
        self,
        character: Character,
        summary: CharacterSummary,
        data: GameData,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(CARD_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._data = data
        self._character = character
        self._summary = summary
        #: The file name, this card's stable identity across a refresh.
        self.name_key = summary.path.name if summary.path is not None else ""

        layout = QVBoxLayout(self)

        self._name_label = QLabel(summary.name)
        name_font = self._name_label.font()
        name_font.setBold(True)
        self._name_label.setFont(name_font)
        self._name_label.setWordWrap(True)
        layout.addWidget(self._name_label)

        self._portrait = QLabel("No image")
        self._portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._portrait.setFixedSize(PORTRAIT_SIZE, PORTRAIT_SIZE)
        self._portrait.setFrameShape(QLabel.Shape.Box)
        layout.addWidget(self._portrait, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._set_portrait(summary.image_path)

        self._pl_label = QLabel(f"PL {summary.power_level}")
        layout.addWidget(self._pl_label)

    # -- what the card is showing -----------------------------------------

    @property
    def character(self) -> Character:
        """The NPC's model — the GM edits this directly."""
        return self._character

    def display_name(self) -> str:
        """The NPC's name, as its summary gives it."""
        return self._summary.name

    def _set_portrait(self, image_path: str | None) -> None:
        """Show the NPC's picture (a local file, so it resolves normally)."""
        resolved = library.resolve_image_path(image_path)
        pixmap = QPixmap(resolved) if resolved else QPixmap()
        if pixmap.isNull():
            self._portrait.setText("No image")
            self._portrait.setPixmap(QPixmap())
            return
        self._portrait.setText("")
        self._portrait.setPixmap(
            pixmap.scaled(
                PORTRAIT_SIZE,
                PORTRAIT_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # -- interaction -------------------------------------------------------

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.openRequested.emit(self.name_key)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        menu = QMenu(self)
        menu.addAction(
            "Remove from this session",
            lambda: self.removeRequested.emit(self.name_key),
        )
        menu.addAction(
            f"Delete {self._summary.name}",
            lambda: self.deleteRequested.emit(self.name_key),
        )
        menu.exec(event.globalPos())
