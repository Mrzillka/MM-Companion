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
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import library
from mm_companion.core.character import Character
from mm_companion.core.data_loader import Condition, GameData
from mm_companion.core.dice import roll_d20
from mm_companion.core.library import CharacterSummary
from mm_companion.core.rules import initiative_modifier
from mm_companion.ui import theme
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.player_card import _ConditionChip
from mm_companion.ui.sections.conditions import (
    addable_conditions,
    condition_display_name,
    condition_tooltip,
)

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
    #: ``(file_name, condition_id, parameter)`` — put this condition on the NPC.
    #: The parameter rides as ``object`` (it is ``str | None``).
    applyConditionRequested = Signal(str, str, object)
    #: ``(file_name, condition_id, parameter)`` — take it off again.
    removeConditionRequested = Signal(str, str, object)
    #: ``(file_name, total)`` — this NPC just rolled initiative.
    initiativeRolled = Signal(str, int)

    def __init__(
        self,
        character: Character,
        summary: CharacterSummary,
        data: GameData,
        parent: QWidget | None = None,
        *,
        initiative: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(CARD_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._data = data
        self._character = character
        self._summary = summary
        self._conditions_by_id: dict[str, Condition] = {c.id: c for c in data.conditions}
        self._addable_conditions: list[Condition] = addable_conditions(data)
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

        pl_row = QHBoxLayout()
        pl_row.setContentsMargins(0, 0, 0, 0)
        self._pl_label = QLabel(f"PL {summary.power_level}")
        pl_row.addWidget(self._pl_label)
        pl_row.addStretch()
        self._initiative_badge = QLabel("")
        badge_font = self._initiative_badge.font()
        badge_font.setBold(True)
        self._initiative_badge.setFont(badge_font)
        self._initiative_badge.setStyleSheet(f"color: {theme.ACCENT};")
        pl_row.addWidget(self._initiative_badge)
        layout.addLayout(pl_row)

        buttons_row = QHBoxLayout()
        buttons_row.setContentsMargins(0, 0, 0, 0)
        self._initiative_button = QToolButton()
        self._initiative_button.setText("Initiative")
        self._initiative_button.setToolTip("Roll initiative for this NPC")
        self._initiative_button.clicked.connect(self.roll_initiative)
        buttons_row.addWidget(self._initiative_button)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        self.set_initiative(initiative)

        condition_row = QHBoxLayout()
        condition_row.setContentsMargins(0, 0, 0, 0)
        self._condition_button = QToolButton()
        self._condition_button.setText("+")
        self._condition_button.setToolTip("Apply a condition to this NPC")
        self._condition_button.clicked.connect(self._show_condition_menu)
        condition_row.addWidget(self._condition_button)
        condition_row.addStretch()
        layout.addLayout(condition_row)

        self._chips = FlowContainer()
        self._chip_flow = FlowLayout(self._chips)
        layout.addWidget(self._chips)
        self.refresh_conditions()

    # -- what the card is showing -----------------------------------------

    @property
    def character(self) -> Character:
        """The NPC's model — the GM edits this directly."""
        return self._character

    def display_name(self) -> str:
        """The NPC's name, as its summary gives it."""
        return self._summary.name

    @property
    def initiative(self) -> int | None:
        """The NPC's rolled initiative this session, or ``None`` if unrolled."""
        return self._initiative

    # -- initiative --------------------------------------------------------

    def set_initiative(self, total: int | None) -> None:
        """Show (or clear) the NPC's initiative badge."""
        self._initiative = total
        self._initiative_badge.setText("" if total is None else f"init {total}")

    def roll_initiative(self) -> int:
        """Roll d20 + this NPC's initiative modifier, show it, and announce it.

        Local by design — an NPC is the GM's own and never on the wire, so this
        is not routed through the session server the way a shared roll is.
        """
        total = roll_d20() + initiative_modifier(self._character, self._data)
        self.set_initiative(total)
        self.initiativeRolled.emit(self.name_key, total)
        return total

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

    # -- conditions --------------------------------------------------------

    def refresh_conditions(self) -> None:
        """Rebuild the condition chips from the (possibly just-changed) model."""
        while self._chip_flow.count():
            item = self._chip_flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for applied in self._character.conditions:
            record = self._conditions_by_id.get(applied.condition_id)
            chip = _ConditionChip(
                condition_display_name(applied, record),
                tooltip=condition_tooltip(applied, record, self._conditions_by_id),
            )
            chip.arm_removal(
                lambda cid=applied.condition_id, param=applied.parameter: (
                    self.removeConditionRequested.emit(self.name_key, cid, param)
                )
            )
            self._chip_flow.addWidget(chip)
        self._chips.setVisible(bool(self._character.conditions))

    def condition_names(self) -> list[str]:
        """The chip captions, in order — the readable form of the NPC's conditions."""
        return [
            self._chip_flow.itemAt(i).widget().text()  # type: ignore[union-attr]
            for i in range(self._chip_flow.count())
        ]

    def _show_condition_menu(self) -> None:
        """The same catalog the sheet's own "+" offers, aimed at this NPC."""
        menu = QMenu(self)
        for condition in sorted(self._addable_conditions, key=lambda c: c.name):
            menu.addAction(
                condition.name,
                lambda checked=False, c=condition: self._choose_condition(c),
            )
        button = self._condition_button
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _choose_condition(self, condition: Condition) -> None:
        """Ask for the condition's subject if it needs one, then request it.

        The dialog reads the NPC's own character for its "a specific advantage /
        power" choices, so the GM picks from what the NPC actually has.
        """
        from mm_companion.ui.sections.condition_dialog import prompt_condition_parameter

        go_ahead, parameter = prompt_condition_parameter(
            condition, self._data, self._character, self
        )
        if go_ahead:
            self.applyConditionRequested.emit(self.name_key, condition.id, parameter)

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
