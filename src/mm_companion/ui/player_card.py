"""One connected player, as a card on the GM's window.

A card is fed from two places, because the session deliberately keeps them
apart. The **roster** entry (``display_name``, ``connected``, ``is_gm``) rides on
every broadcast and carries no character — see
:meth:`~mm_companion.core.session.model.PlayerSlot.roster_dict` — while the
player's **character snapshot** arrives separately, only ever on the host's own
side. So :meth:`PlayerCard.set_roster` and :meth:`PlayerCard.set_character` are
two calls, and a card renders happily with only the first (a player who has
joined but not yet pushed a sheet).

Snapshots carry no ``image_path`` — resolving a remote peer's path would read the
*receiver's* files — so the portrait is a placeholder for now.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import library
from mm_companion.core.character import Character
from mm_companion.core.data_loader import Condition, GameData
from mm_companion.ui import theme
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.sections.conditions import condition_display_name
from mm_companion.ui.sections.system_info import HeroPointsWidget

#: The portrait placeholder's side, in pixels. Matches the launcher's cards.
PORTRAIT_SIZE = 96
#: How wide a card is. Fixed, so a row of them lines up in the flow layout.
CARD_WIDTH = 210
#: Shown in place of a character name before the player pushes a snapshot.
NO_CHARACTER = "no character yet"


class PlayerCard(QFrame):
    """A player's live state: name, character, PL, hero points, conditions."""

    #: The player's id — the GM asked to see their sheet.
    openSheetRequested = Signal(str)

    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(CARD_WIDTH)

        self._data = data
        self._conditions_by_id: dict[str, Condition] = {c.id: c for c in data.conditions}
        self.player_id = ""
        self._character: Character | None = None

        layout = QVBoxLayout(self)

        self._name_label = QLabel("")
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

        self._character_label = QLabel(NO_CHARACTER)
        self._character_label.setWordWrap(True)
        layout.addWidget(self._character_label)

        self._pl_label = QLabel("")
        layout.addWidget(self._pl_label)

        hero_row = QHBoxLayout()
        hero_row.setContentsMargins(0, 0, 0, 0)
        self._hero_label = QLabel("HP")
        hero_row.addWidget(self._hero_label)
        self._hero_points = HeroPointsWidget()
        # The GM watches these, they do not spend them: the widget renders exactly
        # as it does on the sheet but takes no clicks. setEnabled(False) would grey
        # the circles out, which reads as "this player has no hero points".
        self._hero_points.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        hero_row.addWidget(self._hero_points)
        hero_row.addStretch()
        layout.addLayout(hero_row)

        self._chips = FlowContainer()
        self._chip_flow = FlowLayout(self._chips)
        layout.addWidget(self._chips)

        self._open_button = QPushButton("Open sheet")
        self._open_button.setEnabled(False)
        self._open_button.clicked.connect(lambda: self.openSheetRequested.emit(self.player_id))
        layout.addWidget(self._open_button)

    # -- what the card is showing -----------------------------------------

    @property
    def character(self) -> Character | None:
        """The last snapshot as a model, or ``None`` before one arrives."""
        return self._character

    def set_roster(self, entry: dict) -> None:
        """Apply one roster entry: who this is, and whether they are still here."""
        self.player_id = str(entry.get("player_id", ""))
        name = str(entry.get("display_name", "")) or "Player"
        if entry.get("is_gm"):
            self._name_label.setText(f"{name} (you)")
            self._name_label.setStyleSheet("")
        elif not entry.get("connected"):
            self._name_label.setText(f"{name} — offline")
            self._name_label.setStyleSheet(f"color: {theme.TINT_WORSE};")
        else:
            self._name_label.setText(name)
            self._name_label.setStyleSheet("")

    def set_character(self, raw: dict) -> None:
        """Apply a character snapshot (a sanitized ``Character.to_dict``)."""
        if not raw:
            return
        character = Character.from_dict(raw)
        self._character = character
        self._character_label.setText(library.display_name(character))
        self._pl_label.setText(f"PL {character.power_level}")
        self._hero_points.set_value(int(character.characteristics.get("hero_points", 0) or 0))
        self._show_conditions(character)
        self._open_button.setEnabled(True)

    # -- condition chips ---------------------------------------------------

    def _show_conditions(self, character: Character) -> None:
        while self._chip_flow.count():
            item = self._chip_flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for applied in character.conditions:
            record = self._conditions_by_id.get(applied.condition_id)
            self._chip_flow.addWidget(_chip(condition_display_name(applied, record)))
        self._chips.setVisible(bool(character.conditions))

    def condition_names(self) -> list[str]:
        """The chip captions, in order — the readable form of the card's state."""
        return [
            self._chip_flow.itemAt(i).widget().text()  # type: ignore[union-attr]
            for i in range(self._chip_flow.count())
        ]


def _chip(text: str) -> QLabel:
    """One condition, styled as a compact read-only chip."""
    label = QLabel(text)
    label.setStyleSheet(
        f"border: 1px solid {theme.TINT_WORSE};"
        f"background: {theme.tint_rgba(theme.TINT_WORSE, 0.12)};"
        "border-radius: 6px; padding: 1px 5px;"
    )
    return label
