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
*receiver's* files — so the portrait rides along as a small base64 thumbnail
instead (see :mod:`~mm_companion.ui.session_portrait`); a card with none shows a
placeholder.

The card is also where the GM *acts*: its "+" applies a condition straight onto
that player's live sheet and a right-click on a chip takes one off. Neither touches the
character here — the card only asks, the player's app applies it through the same
rules resolver its own "+" uses, and the snapshot that comes back is what
restates these chips. So what the GM sees is always the player's real state, not
what the GM hoped it would be.

What the GM *reads* off it is shared with the NPC card (see
:mod:`~mm_companion.ui.card_summary`): the portrait is the way into the sheet, and
hovering the card gives the full abilities / resistances / powers summary. The two
cards used to disagree about both — a button here, a click-anywhere there; a
summary there, none here — for no reason either could name.
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

from mm_companion.core import library
from mm_companion.core.character import Character
from mm_companion.core.data_loader import Condition, GameData
from mm_companion.ui import theme
from mm_companion.ui.card_chips import _ConditionChip, _SceneEye, start_card_drag
from mm_companion.ui.card_summary import PORTRAIT_SIZE, PortraitButton, character_summary_html
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.pin_panel import install_pin_panel
from mm_companion.ui.sections.conditions import (
    build_condition_menu,
    condition_display_name,
    condition_tooltip,
)
from mm_companion.ui.sections.system_info import HeroPointsWidget
from mm_companion.ui.session_portrait import decode_portrait

#: How far the pointer must move with the button down to count as a drag. The
#: same number the NPC card uses, so one gesture feels like one gesture.
DRAG_THRESHOLD = 8
#: How this card names itself in a drag (see the NPC card's ``SCENE_KIND``).
SCENE_KIND = "player"
#: Shown in place of a character name before the player pushes a snapshot.
NO_CHARACTER = "no character yet"


class PlayerCard(QFrame):
    """A player's live state: name, character, PL, hero points, conditions."""

    #: The player's id — the GM asked to see their sheet.
    openSheetRequested = Signal(str)
    #: ``(player_id, condition_id, parameter)`` — put this condition on that
    #: player. The parameter rides as ``object`` because it is ``str | None`` and
    #: a ``Signal(str)`` would flatten ``None`` into an empty string.
    applyConditionRequested = Signal(str, str, object)
    #: ``(player_id, condition_id, parameter)`` — take it off again.
    removeConditionRequested = Signal(str, str, object)
    #: ``(player_id, value)`` — set this player's hero-point total. Like a
    #: condition, the GM only *asks*; the player's app applies it and pushes back.
    setHeroPointsRequested = Signal(str, int)
    #: The player's id — the GM asked to remove this seat from the session.
    removePlayerRequested = Signal(str)
    #: ``(player_id, on)`` — the GM put this seat on the shared board, or took it
    #: off. Only reachable while the eye is shown; see ``_scene_eye``.
    sceneToggled = Signal(str, bool)
    #: ``(player_id, refs)`` — this card's pinned-parameter strip changed.
    pinsChanged = Signal(str, object)
    #: A ``RollSpec`` — the GM clicked a pinned chip and wants it in the roller.
    loadRequested = Signal(object)
    #: The same, double-clicked — throw it.
    rollRequested = Signal(object)
    #: The player's id — the GM asked to pin something to this card.
    pinPickerRequested = Signal(str)

    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._data = data
        self._conditions_by_id: dict[str, Condition] = {c.id: c for c in data.conditions}
        self.player_id = ""
        self._display_name = "Player"
        self._character: Character | None = None
        # Whether this seat can be *commanded*: a connected player, not the GM's
        # own card (the GM applies conditions to itself on its own sheet) and not
        # someone whose socket has gone.
        self._targetable = False
        # Whether this is the GM's own seat. Kept apart from ``_targetable``:
        # removing a seat works even when it has gone offline (a lingering seat is
        # exactly what a GM wants to clear), but the GM can never remove itself.
        self._is_gm = False
        # Whether the roster last said someone was in this seat. Distinct from
        # ``_targetable``, which is also false for the GM's own card.
        self._connected = False
        #: Where a left-button press landed, to tell a drag from a click.
        self._press_pos = None

        layout = QVBoxLayout()

        self._name_label = QLabel("")
        name_font = self._name_label.font()
        name_font.setBold(True)
        self._name_label.setFont(name_font)
        self._name_label.setWordWrap(True)
        layout.addWidget(self._name_label)

        # The only way into this player's sheet. Dead until a snapshot arrives —
        # there is nothing to open before then.
        self._portrait = PortraitButton()
        self._portrait.set_clickable(False)
        self._portrait.clicked.connect(lambda: self.openSheetRequested.emit(self.player_id))
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
        # The GM *grants and spends* a player's hero points by clicking these pips;
        # the click is sent to the player's app, which applies it and pushes back.
        # Clicks are gated to a commandable seat (see :meth:`set_roster`): a GM's
        # own card and an offline seat take none. setEnabled(False) would grey the
        # circles out, which reads as "this player has no hero points", so the
        # widget is silenced with a mouse-transparency attribute instead.
        self._hero_points.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._hero_points.valueChanged.connect(
            lambda value: self.setHeroPointsRequested.emit(self.player_id, value)
        )
        hero_row.addWidget(self._hero_points)
        hero_row.addStretch()
        layout.addLayout(hero_row)

        # Hosted in a widget rather than added as a bare layout, so the card can
        # ask it how wide it needs to be — see :meth:`body_width_hint`.
        self._action_host = QWidget()
        condition_row = QHBoxLayout(self._action_host)
        condition_row.setContentsMargins(0, 0, 0, 0)
        # Hidden while the GM has players joining the Scene automatically: an
        # action that does nothing is worse than none. See
        # ``storage.gm_scene_auto_players``.
        self._scene_eye = _SceneEye()
        self._scene_eye.setVisible(False)
        self._scene_eye.toggled.connect(lambda on: self.sceneToggled.emit(self.player_id, bool(on)))
        condition_row.addWidget(self._scene_eye)
        self._condition_button = QToolButton()
        self._condition_button.setText("+")
        self._condition_button.setToolTip("Apply a condition to this player")
        self._condition_button.setEnabled(False)
        self._condition_button.clicked.connect(self._show_condition_menu)
        condition_row.addWidget(self._condition_button)
        condition_row.addStretch()
        layout.addWidget(self._action_host)

        self._chips = FlowContainer()
        self._chip_flow = FlowLayout(self._chips)
        layout.addWidget(self._chips)
        layout.addStretch()

        self.pins = install_pin_panel(self, layout, data)
        self.pins.pinsChanged.connect(lambda refs: self.pinsChanged.emit(self.player_id, refs))
        self.pins.loadRequested.connect(self.loadRequested)
        self.pins.rollRequested.connect(self.rollRequested)
        self.pins.pickRequested.connect(lambda: self.pinPickerRequested.emit(self.player_id))
        # A width of its own until the window measures the block; see
        # :meth:`~mm_companion.ui.gm_window.GMWindow._sync_card_widths`.
        self.apply_width(self.body_width_hint(), self.pins.natural_width())

    # -- how wide the card is ----------------------------------------------

    def body_width_hint(self) -> int:
        """The narrowest this card's own column can be and still show its controls.

        The same bargain the NPC card's makes, and deliberately the same number in
        practice: the two cards sit in two blocks a GM reads side by side, and a
        player card visibly narrower than an NPC card would read as a different
        kind of thing rather than the same card with different powers over it.
        """
        return max(
            int(theme.metric("gm.card.min")),
            self._action_host.sizeHint().width(),
            PORTRAIT_SIZE,
        )

    def pin_width_hint(self) -> int:
        """How wide this card's pinned strip wants to be."""
        return self.pins.natural_width()

    def apply_width(self, body: int, strip: int) -> None:
        """Wear *body* and *strip* — the block's widest, not this card's own."""
        self.pins.set_width(strip)
        self.setFixedWidth(body + strip + int(theme.metric("space.sm")) * 3)

    # -- what the card is showing -----------------------------------------

    @property
    def character(self) -> Character | None:
        """The last snapshot as a model, or ``None`` before one arrives."""
        return self._character

    def display_name(self) -> str:
        """The player's display name, as the roster gave it (no status suffix)."""
        return self._display_name

    def set_roster(self, entry: dict) -> None:
        """Apply one roster entry: who this is, and whether they are still here."""
        self.player_id = str(entry.get("player_id", ""))
        name = str(entry.get("display_name", "")) or "Player"
        self._display_name = name
        if entry.get("is_gm"):
            self._name_label.setText(f"{name} (you)")
            self._name_label.setStyleSheet("")
        elif not entry.get("connected"):
            self._name_label.setText(f"{name} — offline")
            self._name_label.setStyleSheet(f"color: {theme.color('tint.worse')};")
        else:
            self._name_label.setText(name)
            self._name_label.setStyleSheet("")
        self._is_gm = bool(entry.get("is_gm"))
        self._connected = bool(entry.get("connected"))
        self._targetable = not self._is_gm and self._connected
        self._condition_button.setEnabled(self._targetable)
        # Only a commandable seat takes hero-point clicks; the rest stay read-only.
        self._hero_points.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, not self._targetable
        )
        # A seat that just went offline (or came back) changes what the chips can
        # do, so they are restated even though the character has not moved.
        self._show_conditions()

    def set_character(self, raw: dict) -> None:
        """Apply a character snapshot (a sanitized ``Character.to_dict``)."""
        if not raw:
            return
        character = Character.from_dict(raw)
        self._character = character
        self._character_label.setText(library.display_name(character))
        self._pl_label.setText(f"PL {character.power_level}")
        self._hero_points.set_value(int(character.characteristics.get("hero_points", 0) or 0))
        self._set_portrait(raw.get("portrait"))
        self._show_conditions()
        self.pins.set_character(character)
        self._portrait.set_clickable(True)
        # The same hover summary an NPC card gives, so the GM can read a player's
        # numbers without opening a window for them.
        self.setToolTip(
            character_summary_html(character, self._data, library.display_name(character))
        )

    def set_scene_controls(self, shown: bool) -> None:
        """Show or hide this card's Scene toggle.

        Shown only when the GM adds players to the board by hand. With the
        automatic setting on there is nothing for it to decide, and a toggle that
        cannot be turned off is a lie about who is in charge.
        """
        self._scene_eye.setVisible(shown)

    def set_in_scene(self, on: bool) -> None:
        """Show whether this seat is on the shared board. Silent, like the NPC card's."""
        self._scene_eye.set_in_scene(on)

    @property
    def in_scene(self) -> bool:
        """Whether the eye says this seat is on the board."""
        return self._scene_eye.isChecked()

    def _set_portrait(self, data: object) -> None:
        """Show the transmitted thumbnail, or fall back to the placeholder."""
        self._portrait.set_image(decode_portrait(data))

    # -- condition chips ---------------------------------------------------

    def _show_conditions(self) -> None:
        while self._chip_flow.count():
            item = self._chip_flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        conditions = self._character.conditions if self._character is not None else []
        for applied in conditions:
            record = self._conditions_by_id.get(applied.condition_id)
            chip = _ConditionChip(
                condition_display_name(applied, record),
                tooltip=condition_tooltip(applied, record, self._conditions_by_id),
            )
            if self._targetable:
                chip.arm_removal(
                    lambda cid=applied.condition_id, param=applied.parameter: (
                        self.removeConditionRequested.emit(self.player_id, cid, param)
                    )
                )
            self._chip_flow.addWidget(chip)
        self._chips.setVisible(bool(conditions))

    def condition_names(self) -> list[str]:
        """The chip captions, in order — the readable form of the card's state."""
        return [
            self._chip_flow.itemAt(i).widget().text()  # type: ignore[union-attr]
            for i in range(self._chip_flow.count())
        ]

    # -- the GM's fast-apply -----------------------------------------------

    def _show_condition_menu(self) -> None:
        """The same catalog, split the same way, the sheet's own "+" offers."""
        menu = build_condition_menu(self, self._data, self._choose_condition)
        button = self._condition_button
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _choose_condition(self, condition: Condition) -> None:
        """Ask for the condition's subject if it needs one, then send the command.

        The dialog reads the *player's* character for its "a specific advantage /
        power" choices, so the GM picks from what that player actually has. A card
        with no snapshot yet falls back to a blank sheet rather than refusing.
        """
        from mm_companion.ui.sections.condition_dialog import prompt_condition_parameter

        subject = self._character or Character.new_default(self._data)
        go_ahead, parameter = prompt_condition_parameter(condition, self._data, subject, self)
        if go_ahead:
            self.applyConditionRequested.emit(self.player_id, condition.id, parameter)

    # -- removing the seat -------------------------------------------------

    # -- dragging onto the Scene -------------------------------------------
    #
    # A player card never had a gesture of its own — there is nothing to reorder in
    # a roster — so this one has nothing to share a threshold with. It exists so
    # that a GM who adds players to the Scene by hand can do it the way they add an
    # NPC, rather than learning that one card drags and the other does not.

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton and self.player_id:
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
        start_card_drag(self, f"{SCENE_KIND}:{self.player_id}")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Right-click offers to remove the player. Never on the GM's own card.

        Offered for an offline seat too — a seat outlives its connection, and
        clearing one a player has abandoned is a fair thing for a GM to want.
        """
        if self._is_gm or not self.player_id:
            return
        menu = QMenu(self)
        # "Seat" rather than "player" once they are already gone: there is nobody
        # there to remove, and the difference is exactly what a GM tidying up
        # after a session wants to be sure of before they click.
        menu.addAction(
            "Remove player" if self._connected else "Remove seat",
            lambda: self.removePlayerRequested.emit(self.player_id),
        )
        menu.exec(event.globalPos())

    @property
    def connected(self) -> bool:
        """Whether the roster last said this seat had someone in it."""
        return self._connected
